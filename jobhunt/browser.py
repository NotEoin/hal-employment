"""Drives a real browser through job application forms with Playwright.

One thread owns the browser; other threads queue work with `BROWSER.call`.
A form's controls are tagged with data-hal-id attributes when read, so the
values approved in the web interface land in the controls they were proposed
for, and every value entered is read back from the page. Nothing is sent
except by `submit`, which the web interface calls only for an application
whose written content has been approved. Requires the playwright package
(see .venv).
"""
import collections
import concurrent.futures
import difflib
import json
import os
import queue
import re
import threading
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from .config import CONFIG, ROOT

PROFILE = ROOT / "data" / "browser-profile"
SHOTS = ROOT / "data" / "apply"
# Sites the applicant has signed in to in Hal's browser, where Apply may send a stored CV at once.
SIGNED_IN = ROOT / "data" / "signed-in.json"
NAV_TIMEOUT = 45_000  # ms

# The page readers are shared with the extension, which runs them in the applicant's own browser.
PAGE_JS = (ROOT / "extension" / "page.js").read_text()
EXTRACT_JS = "() => {\n" + PAGE_JS + "\nreturn halExtract();\n}"
SURVEY_JS = "() => {\n" + PAGE_JS + "\nreturn halSurvey();\n}"
OPTIONS_JS = "(hid) => {\n" + PAGE_JS + "\nreturn halOptions(hid);\n}"
STALE_JS = "() => {\n" + PAGE_JS + "\nreturn halStale();\n}"
RESULTS_JS = "() => {\n" + PAGE_JS + "\nreturn halResults();\n}"
# Apply controls on an advert. Ones that hand the application to another account are left alone.
APPLY_BUTTON = re.compile(
    r"^\s*((easy|quick|1[- ]click) )?apply\b(?!.{0,40}\b(linkedin|indeed|seek|xing|filters?|later|coupon|code)\b).{0,40}$|"
    r"^\s*(start|begin|continue|complete|resume) (your |my |the )?application\s*$|^\s*i'?m interested\s*$", re.I)
NEXT_BUTTON = re.compile(r"^\s*(next|next step|next page|continue|proceed|save (and|&) (continue|next|proceed)|"
                         r"continue to .{0,30}|next: .{0,30}|review( (your |my )?application)?|go to next step)"
                         r"[\s»›>→]*$", re.I)
SUBMIT_BUTTON = re.compile(r"^\s*(submit|submit (your |my |the )?application|send( (your |my )?application)?|"
                           r"apply( now)?|complete( application)?|finish( application)?|"
                           r"(confirm|review) (and|&) submit|submit (and|&) (finish|apply))\s*$", re.I)
SUCCESS = re.compile(r"thank(s| you)[^.]{0,60}(appl|submi|interest)|"
                     r"application (has been |was |is )?(successfully )?(received|submitted|sent|complete)|"
                     r"we('ve| have) (received|got) your application|successfully (submitted|applied)|"
                     r"your application is (in|on its way)", re.I)
APPLIED = re.compile(r"you(('ve| have) already| have|'ve)? applied (for|to) this (job|role|position|vacancy)", re.I)
SIGN_IN = re.compile(r"\b(sign|log)[ -]?in\b|\blogin\b|\bregister\b|create (an |your )?account", re.I)
SIGN_OUT = re.compile(r"^\s*(sign|log) ?out\s*$", re.I)
CLOSED = re.compile(r"(no longer|not) (available|accepting applications|open|exists?|active|live)|not available anymore|"
                    r"(job|position|vacancy|role|posting|advert|page) (you requested )?(has |was |is )?"
                    r"(been )?(filled|expired|closed|removed|not found|unavailable)|"
                    r"(job|position|vacancy|page) not found|applications? (are|is|have|has) (now )?closed|\b404\b", re.I)
BOT_CHECK = re.compile(r"just a moment|verify (that )?you are (a )?human|human verification|are you a robot|"
                       r"attention required|pardon our interruption|checking your browser|security check", re.I)
# Pages that refuse the browser outright, with no check to pass.
BLOCKED = re.compile(r"^blocked\b|access denied|request could not be satisfied|403 forbidden|"
                     r"unusual (traffic|activity)|request blocked", re.I)
# A code the site emails to prove the address before it takes the application.
CODE_WALL = re.compile(r"(verification|security|confirmation|one[- ]time) code|enter the code|code (we|was) sent", re.I)
# Application forms that employers embed in a frame on their own careers page.
ATS_FRAME = ("greenhouse.io", "ashbyhq.com", "lever.co", "workable.com", "smartrecruiters.com", "recruitee.com",
             "personio.", "teamtailor.com", "bamboohr.com", "jobvite.com", "icims.com", "pinpointhq.com")
# Frames that never hold the form: human checks, analytics, chat widgets.
NOT_FORM_FRAME = re.compile(r"recaptcha|hcaptcha|challenges\.cloudflare|googletagmanager|doubleclick|google\.com/|"
                            r"facebook|intercom|hotjar|about:blank|youtube|vimeo|stripe|cookie", re.I)
MAX_PRESSES = 6  # Apply controls followed before giving up on finding the form
# Human checks: reCAPTCHA, hCaptcha and Cloudflare Turnstile, whose frame hides in a closed shadow root.
CAPTCHA = ("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], "
           "input[name='cf-turnstile-response'], .cf-turnstile")
SEARCH = "https://duckduckgo.com/?q="
GONE = "the form's tab was closed"


class TabGone(RuntimeError):
    """The job's tab, or the whole browser window, was closed while Hal was using it."""

    def __init__(self) -> None:
        super().__init__(GONE)


def application_url(job) -> str:
    """Returns the page holding the application form for a job, as best it can be known."""
    url = job["url"] or ""
    host = urlparse(url).netloc
    base = url.split("?")[0].rstrip("/")
    if "ashbyhq.com" in host and not base.endswith("/application"):
        return base + "/application"
    if "lever.co" in host and not base.endswith("/apply"):
        return base + "/apply"
    m = re.search(r"gh_jid=(\d+)", url)
    if m and "greenhouse.io" not in host and job["id"].startswith("greenhouse:"):
        from .config import boards
        slug = next((b["slug"] for b in boards() if b["ats"] == "greenhouse"
                     and b["name"] == job["company"]), None)
        if slug:
            return f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={m.group(1)}"
    return url


class Browser:
    """Owns the Playwright browser on a dedicated thread."""

    def __init__(self) -> None:
        self._work: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ctx = None
        self.pages: dict[str, list] = {}

    def call(self, fn, *args, timeout: float = 600):
        """Runs fn(browser, *args) on the browser thread and returns its result."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="browser", daemon=True)
            self._thread.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._work.put((fn, args, fut))
        return fut.result(timeout)

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            self._pw = pw
            while True:
                fn, args, fut = self._work.get()
                try:
                    fut.set_result(fn(self, *args))
                except Exception as e:  # noqa: BLE001 - errors are re-raised in the caller's thread
                    # A closed tab is reported as such, whichever step it interrupted.
                    fut.set_exception(TabGone() if "has been closed" in str(e) else e)

    def context(self):
        """Returns the browser context, launching it (again, if the window was closed)."""
        if self._ctx is not None:
            try:
                self._ctx.pages  # noqa: B018 - raises once the browser has gone
                return self._ctx
            except Exception:  # noqa: BLE001
                self._ctx = None
        PROFILE.mkdir(parents=True, exist_ok=True)
        headless = mode() == "headless"
        # Wayland refuses a window's request to raise itself, so "Show browser
        # window" only works when the window runs through XWayland.
        args = ["--ozone-platform=x11"] if not headless and os.environ.get("DISPLAY") else []
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE), headless=headless, viewport={"width": 1280, "height": 900},
            locale="en-GB", timezone_id="Europe/London", args=args)
        self._ctx.on("close", lambda _: setattr(self, "_ctx", None))
        self.pages = {}
        return self._ctx

    def page(self, job_id: str):
        """Returns the newest open tab for a job's application, or None."""
        tabs = self.pages.get(job_id, [])
        tabs[:] = [p for p in tabs if not p.is_closed()]
        return tabs[-1] if tabs else None

    def tab(self, job_id: str):
        """Returns the job's tab, brought to the front to be worked on, or raises TabGone when it has none.

        A tab behind another is not drawn, and a page that is not drawn never
        finishes opening a dropdown or settling for a click.
        """
        page = self.page(job_id)
        if page is None:
            raise TabGone()
        if mode() != "headless":
            page.bring_to_front()
        return page

    def follow(self, job_id: str, page) -> None:
        """Makes a tab the job's current one, along with any tab it opens later."""
        tabs = self.pages.setdefault(job_id, [])
        if page not in tabs:
            page.on("popup", lambda popup: self.follow(job_id, popup))
        else:
            tabs.remove(page)
        tabs.append(page)

    def close(self, job_id: str) -> None:
        """Closes the job's tabs, once its application is finished with."""
        for page in self.pages.pop(job_id, []):
            try:
                page.close()
            except Exception:  # noqa: BLE001 - already closed
                pass


BROWSER = Browser()


def _is_form(fields: list[dict]) -> bool:
    """True when the fields look like an application form rather than a search box or banner."""
    if any(f["kind"] == "file" for f in fields):
        return True
    # An advert's search box and alert sign-up are separate small forms; an application is one large one.
    per_form = collections.Counter(f.get("form") for f in fields if f.get("label"))
    return max(per_form.values(), default=0) >= 3


# Cookie banners block clicks underneath them; refusing optional cookies is preferred.
COOKIE_BUTTONS = [
    re.compile(r"^\s*(reject all|reject( all)? (optional|non-essential)( cookies)?|decline( all)?( cookies)?|"
               r"(use |allow )?(only )?(strictly )?(necessary|essential)( cookies)?( only)?|deny( all)?|"
               r"continue without accepting)\s*$", re.I),
    re.compile(r"^\s*(accept( all)?( cookies)?|allow all( cookies)?|i agree|agree|got it|ok|okay)\s*$", re.I),
]


def _dismiss_banners(page) -> None:
    for pattern in COOKIE_BUTTONS:
        button = page.get_by_role("button", name=pattern)
        try:
            if button.count():
                button.first.click(timeout=3_000)
                page.wait_for_timeout(500)
                return
        except Exception:  # noqa: BLE001 - a banner that will not close may not block anything
            pass


def _settle(page) -> None:
    if "adzuna." in urlparse(page.url).netloc and "/land/" in page.url:
        # Adzuna links show a holding page that hands over to the advertiser a few seconds later.
        try:
            page.wait_for_url(lambda u: "adzuna." not in urlparse(u).netloc, timeout=15_000)
        except Exception:  # noqa: BLE001 - some adverts are hosted on Adzuna itself
            pass
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 - some pages poll forever; the DOM is ready regardless
        pass


def _site(url: str) -> str:
    """Returns the site a page belongs to, such as reed.co.uk for secure.reed.co.uk."""
    labels = urlparse(url).netloc.split(":")[0].split(".")
    keep = 3 if len(labels) > 2 and labels[-2] in ("co", "com", "org", "ac", "gov") else 2
    return ".".join(labels[-keep:])


def _signed_in_sites() -> set[str]:
    try:
        return set(json.loads(SIGNED_IN.read_text()))
    except (OSError, ValueError):
        return set()


# Reading -----------------------------------------------------------------------

def _frames(page) -> list:
    """Returns the frames that could hold the form: the page itself, then the frames inside it."""
    inner = [f for f in page.frames if f is not page.main_frame and not f.is_detached()
             and f.url and not NOT_FORM_FRAME.search(f.url)]
    return [page.main_frame, *inner]


def _extract(page) -> tuple[object, list[dict]]:
    """Reads the fields of every frame and returns the frame holding the form, with its fields."""
    best = (page.main_frame, [])
    for frame in _frames(page):
        try:
            fields = frame.evaluate(EXTRACT_JS)
        except Exception:  # noqa: BLE001 - a frame that has gone, or that the site forbids reading
            if frame is page.main_frame:
                raise
            continue
        if (_is_form(fields), len(fields)) > (_is_form(best[1]), len(best[1])):
            best = (frame, fields)
    return best[0], _main_form(best[1])


def _main_form(fields: list[dict]) -> list[dict]:
    """Drops the odd field of another small form, such as a newsletter box beside the application."""
    per_form = collections.Counter(f.get("form") for f in fields)
    if len(per_form) < 2:
        return fields
    main, most = per_form.most_common(1)[0]
    if most < 3 or len(fields) - most > 2:
        return fields
    return [f for f in fields if f.get("form") == main]


def _read(page, options: bool = True) -> tuple[object, list[dict]]:
    """Reads the form, opening each dropdown that keeps its options out of the page."""
    try:
        frame, fields = _extract(page)
    except Exception as e:  # noqa: BLE001 - the page moved to another address mid-read
        if "has been closed" in str(e):
            raise
        page.wait_for_timeout(1_500)
        _settle(page)
        frame, fields = _extract(page)
    if options and _is_form(fields):
        for f in fields:
            if f["kind"] == "combobox" and not f.get("options") and not f.get("current"):
                f["options"] = _combobox_options(page, frame, f["hid"])
    return frame, fields


def _survey(frame) -> dict:
    try:
        return frame.evaluate(SURVEY_JS)
    except Exception as e:  # noqa: BLE001 - mid-navigation; an empty survey is retried by the caller's loop
        if "has been closed" in str(e):
            raise
        return {"url": frame.url, "title": "", "controls": [], "headings": [], "text": "", "alerts": [],
                "password": False, "invalid": 0, "captcha": False, "challenge": False, "stayed": False}


def _close_list(page, box) -> None:
    """Closes an open dropdown without disturbing the form around it."""
    try:
        box.evaluate("e => e.blur()")
        if page.locator("[data-hal-opt]:visible").count():
            box.press("Escape")
    except Exception:  # noqa: BLE001 - nothing was open
        pass


def _open_list(box) -> None:
    """Opens a dropdown: by a click, or from the keyboard when a styled layer lies over the control."""
    try:
        box.click(timeout=1_500)
    except Exception:  # noqa: BLE001
        try:
            box.click(timeout=1_500, force=True)
        except Exception:  # noqa: BLE001
            box.focus(timeout=1_500)
            box.press("ArrowDown")


def _listed(page, frame, hid: str, waits: int = 3) -> list[str]:
    """Returns the options an opened dropdown shows, giving a list fetched from the site time to arrive."""
    options: list[str] = []
    for _ in range(waits):
        page.wait_for_timeout(300)
        options = frame.evaluate(OPTIONS_JS, hid)
        if options:
            break
    return options


def _combobox_options(page, frame, hid: str) -> list[str]:
    """Opens a combobox, reads its options, and closes it again."""
    box = frame.locator(f"[data-hal-id='{hid}']")
    try:
        frame.evaluate(STALE_JS)
        _open_list(box)
        options = _listed(page, frame, hid)
        if not options:
            # The first click can be spent closing the list of the control before.
            _open_list(box)
            options = _listed(page, frame, hid, waits=2)
        _close_list(page, box)
        return options
    except Exception:  # noqa: BLE001 - an option list that will not open is filled by typing
        return []


def _needs(survey: dict, fields: list[dict]) -> str:
    """Names what only the applicant can do on this page: "sign-in", "check", "code", or "" for nothing."""
    head = f"{survey['title']} {' '.join(survey['headings'])}"
    form = _is_form(fields)
    if BOT_CHECK.search(survey["title"]) or (not form and BOT_CHECK.search(survey["text"][:400])):
        return "check"
    if survey["password"]:
        # A registration form that also asks the usual questions is filled in, apart from its password.
        return "" if form and len(fields) > 5 else "sign-in"
    if not form and 0 < len(fields) <= 3 and CODE_WALL.search(survey["text"][:3000]):
        return "code"
    if form:
        return ""
    # Sign-in pages that ask for the email address first show no password box yet.
    if 0 < len(fields) <= 2 and _apply_control(survey) is None \
            and SIGN_IN.search(f"{head} {urlparse(survey['url']).path}"):
        return "sign-in"
    return ""


def _signed_in(survey: dict) -> bool:
    """True when the applicant has an account session on the page's site."""
    return _site(survey["url"]) in _signed_in_sites() or any(SIGN_OUT.search(c["name"]) for c in survey["controls"])


def _apply_control(survey: dict) -> dict | None:
    """Returns the page's Apply link or button, preferring one in the advert over one in a menu."""
    found = [c for c in survey["controls"] if APPLY_BUTTON.search(c["name"])]
    found.sort(key=lambda c: (c["inNav"], not any(h in c["href"] for h in ATS_FRAME)))
    return found[0] if found else None


def _step_control(survey: dict, pattern) -> dict | None:
    """Returns the last button of the form that matches the pattern, such as its Next or Submit."""
    found = [c for c in survey["controls"] if not c["link"] and pattern.search(c["name"])]
    found.sort(key=lambda c: (c["inForm"], c["submit"]))
    return found[-1] if found else None


def _buttons(page, frame) -> tuple[dict | None, dict | None, object]:
    """Returns the form's Next and Submit buttons and the frame they are in."""
    for where in dict.fromkeys([frame, page.main_frame]):
        survey = _survey(where)
        nxt, sub = _step_control(survey, NEXT_BUTTON), _step_control(survey, SUBMIT_BUTTON)
        if nxt is None and sub is None:
            # A form whose only button carries the site's own wording is sent with that button.
            typed = [c for c in survey["controls"] if c["submit"] and c["inForm"]]
            sub = typed[-1] if typed and where is frame else None
        if nxt or sub:
            return nxt, sub, where
    return None, None, frame


def _closed(survey: dict, status: int | None) -> bool:
    head = f"{survey['title']} {' '.join(survey['headings'][:4])}"
    return CLOSED.search(head) is not None or status in (404, 410) \
        or ("greenhouse.io" in survey["url"] and "error=true" in survey["url"])


def _report(page, frame, fields: list[dict], survey: dict, needs: str, pressed: bool,
            status: int | None = None) -> dict:
    form = _is_form(fields)
    text = "" if form else survey["text"]
    nxt, sub, _ = _buttons(page, frame) if form else (None, None, None)
    return {"url": page.url, "title": page.title(), "site": _site(page.url),
            # A sign-in page's inputs are not questions to answer; the applicant signs in by hand.
            "fields": [] if needs else fields, "needs": needs,
            "sent": pressed and SUCCESS.search(text) is not None,
            "closed": not form and _closed(survey, status),
            "blocked": not form and not needs and BLOCKED.search(f"{survey['title']} {survey['text'][:300]}") is not None,
            "applied": APPLIED.search(text) is not None,
            "captcha": survey["captcha"], "alerts": survey["alerts"],
            "next": nxt is not None and sub is None, "submit": sub is not None,
            "password": form and survey["password"],
            # What is left to press when no form was found, for choosing the way on.
            "controls": [] if form or needs else [c for c in survey["controls"] if not c["inNav"]][:60]}


# Navigation --------------------------------------------------------------------

def _press(page, frame, control: dict) -> None:
    """Presses a control the survey found; a link that will not take the click is followed by address."""
    target = frame.locator(f"[data-hal-nav='{control['id']}']").first
    try:
        target.click(timeout=5_000)
    except Exception:  # noqa: BLE001 - usually a pop-up lying over the control
        if control["href"].startswith("http"):
            page.goto(control["href"], wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        else:
            target.click(timeout=5_000, force=True)


def _advance(b: Browser, job_id: str, pressed: bool = False, may_send: bool = False,
             status: int | None = None) -> dict:
    """Presses through advert pages towards the application form and reports where it stopped.

    Stops at the form, at a page only the applicant can pass (`needs`), or when
    no Apply control is left. `pressed` says Apply was just pressed on a site
    the applicant is signed in to, where a confirmation on the page means the
    application went out. `may_send` allows that press, once the application's
    written content is approved.
    """
    page = b.tab(job_id)
    needs = ""
    for presses in range(MAX_PRESSES + 1):
        _settle(page)
        _dismiss_banners(page)
        frame, fields = _read(page, options=False)
        survey = _survey(page.main_frame)
        needs = _needs(survey, fields)
        if needs or _is_form(fields) or presses == MAX_PRESSES or _closed(survey, status):
            break
        control = _apply_control(survey)
        if control is None:
            break
        if _signed_in(survey):
            # Signed in, Apply may send the stored CV at once, which is a submission like any other.
            if not may_send and _site(page.url) not in CONFIG.get("signed_in_apply", []):
                needs = "apply"
                break
            pressed = True
        _press(page, page.main_frame, control)
        page.wait_for_timeout(1_500)
        page = b.page(job_id) or page  # the press may have opened the form in a new tab
        status = None
    if _is_form(fields) and not needs:
        frame, fields = _read(page)
    return _report(page, frame, fields, survey, needs, pressed, status)


def _open_form(b: Browser, job_id: str, url: str) -> dict:
    """Opens the job's application page and works through to its form."""
    page = b.page(job_id) or b.context().new_page()
    b.follow(job_id, page)
    b.tab(job_id)
    response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    return _advance(b, job_id, status=response.status if response else None)


def _resume(b: Browser, job_id: str, passed: str, wall: str) -> dict:
    """Carries on towards the form once the applicant has done what the page at `wall` needed."""
    if passed == "sign-in":
        SIGNED_IN.write_text(json.dumps(sorted(_signed_in_sites() | {_site(wall)})))
    return _advance(b, job_id, pressed=passed == "apply", may_send=passed == "apply")


def _press_control(b: Browser, job_id: str, name: str) -> dict:
    """Presses the control with this name, chosen from a page where no Apply control was recognised."""
    page = b.tab(job_id)
    survey = _survey(page.main_frame)
    control = next((c for c in survey["controls"] if c["name"] == name), None)
    if control is None:
        raise RuntimeError(f"no control named {name!r} on the page")
    _press(page, page.main_frame, control)
    page.wait_for_timeout(1_500)
    return _advance(b, job_id)


def _peek(b: Browser, job_id: str) -> dict:
    """Reports the state of the job's tab without pressing anything."""
    page = b.page(job_id)
    if page is None:
        return {"gone": True}
    try:
        frame, fields = _extract(page)
        survey = _survey(frame)
        text = survey["text"]
        return {"needs": _needs(survey, fields), "form": _is_form(fields), "url": page.url,
                "sent": SUCCESS.search(text) is not None, "applied": APPLIED.search(text) is not None,
                "challenge": survey["challenge"],
                # A password box still empty, on a form Hal has filled in around it.
                "password": frame.evaluate("() => [...document.querySelectorAll('input[type=password]')]"
                                           ".some(e => e.offsetWidth > 0 && !e.value)")}
    except Exception:  # noqa: BLE001 - the page is between two addresses
        return {"busy": True}


# Filling -----------------------------------------------------------------------

def _same(a: str, b: str) -> bool:
    return " ".join(str(a).split()).lower() == " ".join(str(b).split()).lower()


def _best_option(options: list[str], value: str) -> int | None:
    """Returns the index of the option that says `value`, or None when none does."""
    for test in (lambda o: _same(o, value), lambda o: o.lower().startswith(str(value).lower()),
                 lambda o: str(value).lower() in o.lower()):
        for i, option in enumerate(options):
            if test(option):
                return i
    close = difflib.get_close_matches(str(value), options, n=1, cutoff=0.8)
    return options.index(close[0]) if close else None


def _pick(page, frame, box, hid: str, value: str, typeable: bool) -> None:
    """Chooses `value` in a dropdown: opens it, types to narrow a long or searched list, presses the option."""
    frame.evaluate(STALE_JS)
    _open_list(box)
    options = _listed(page, frame, hid)
    if not options:
        _open_list(box)
        options = _listed(page, frame, hid, waits=2)
    index = _best_option(options, value)
    if index is None and typeable:
        # A list too long to show whole, or one the site searches as the applicant types.
        # "London, United Kingdom" may be wanted as a city or as a country.
        parts = [part.strip() for part in str(value).split(",") if part.strip()]
        for query in dict.fromkeys([str(value), *parts, str(value).split()[0].strip(",")]):
            box.fill("")
            box.press_sequentially(query, delay=25)
            for _ in range(8):
                page.wait_for_timeout(400)
                options = frame.evaluate(OPTIONS_JS, hid)
                index = _best_option(options, value)
                if index is None and options and query != str(value):
                    index = _best_option(options, query)
                if index is not None:
                    break
            if index is not None:
                break
    if index is None:
        _close_list(page, box)
        shown = f"; it offers {', '.join(options[:6])}" if options else ""
        raise RuntimeError(f"no option matching {value!r}{shown}")
    frame.locator(f"[data-hal-opt='{index}']").first.click(timeout=4_000)
    page.wait_for_timeout(250)


def _set_choice(frame, el, want: bool, native: bool) -> None:
    """Ticks or clears one radio or checkbox, by its label when the control itself is hidden."""
    if not native:
        on = el.evaluate("e => e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-pressed') === 'true'"
                         " || /(^|[\\s_-])(active|selected|checked)/i.test(e.className)")
        if on != want:
            el.click(timeout=4_000)
        return
    if el.is_checked() == want:
        return
    try:
        el.set_checked(want, timeout=3_000)
    except Exception:  # noqa: BLE001 - a styled control: its label takes the click instead
        try:
            el.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e.parentElement).click()")
        except Exception:  # noqa: BLE001
            pass
        if el.is_checked() != want:
            el.set_checked(want, force=True, timeout=3_000)


def _date(value: str) -> str:
    """Returns a date as the yyyy-mm-dd a date input takes, when it can be read as one."""
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y", "%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def _fill_one(page, frame, f: dict, now: dict, value) -> None:
    """Enters one value. `f` is the field as approved; `now` is the same field as the page shows it."""
    hid, kind = now["hid"], now["kind"]
    if kind in ("radio", "checkgroup"):
        wanted = value if isinstance(value, list) else [value]
        missing = [w for w in wanted if not any(_same(w, o) for o in now["options"])]
        if missing:
            raise RuntimeError(f"no option {missing[0]!r}")
        for i, opt in enumerate(now["options"]):
            el = frame.locator(f"[data-hal-id='{hid}:{i}']")
            chosen = any(_same(w, opt) for w in wanted)
            if chosen or kind == "checkgroup":
                _set_choice(frame, el, chosen, not now.get("custom"))
        return
    el = frame.locator(f"[data-hal-id='{hid}']")
    if kind == "file":
        el.set_input_files(value)
        # The site reads the file before the form is ready to go on.
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:  # noqa: BLE001 - a control that cannot scroll is filled where it is
        pass
    if kind == "checkbox":
        _set_choice(frame, el, bool(value), not now.get("custom"))
    elif kind == "select":
        index = _best_option(now.get("options") or [], str(value))
        if index is None:
            raise RuntimeError(f"no option {value!r}")
        el.select_option(label=now["options"][index])
    elif kind == "combobox":
        _pick(page, frame, el, hid, str(value), bool(now.get("typeable")))
    else:
        text = _date(str(value)) if now.get("type") == "date" else str(value)
        if now.get("maxlength"):
            text = text[:now["maxlength"]]
        if now.get("custom"):
            el.click(timeout=3_000)
            el.press("ControlOrMeta+a")
            el.press_sequentially(text, delay=2)
        else:
            try:
                el.fill(text, timeout=5_000)
            except Exception:  # noqa: BLE001 - a masked input takes keystrokes, not a pasted value
                el.click(timeout=3_000)
                el.press("ControlOrMeta+a")
                el.press_sequentially(text, delay=15)
        el.evaluate("e => e.blur()")


def _held(now: dict, value) -> bool:
    """True when the page shows the value Hal entered in a field."""
    current, kind = now.get("current"), now["kind"]
    if kind == "file":
        return True  # upload widgets empty their input once the file is taken
    if kind == "checkbox":
        return bool(current) == bool(value)
    if kind == "checkgroup":
        return {str(v).lower() for v in value} == {str(c).lower() for c in current or []}
    if kind in ("radio", "select"):
        return _same(current or "", value) or (kind == "select" and _best_option([current or ""], str(value)) == 0)
    if kind == "combobox":
        return bool(current)
    if now.get("type") in ("tel", "date", "number"):
        return bool(current)  # sites reformat these as they are typed
    try:
        if float(current) == float(value):
            return True  # "0" shown back as "0.00"
    except (TypeError, ValueError):
        pass
    return _same(current or "", value) or (bool(now.get("maxlength")) and bool(current))


def _holds(now: dict, value) -> bool:
    """True when a field already shows exactly this answer, so entering it again would change nothing."""
    current, kind = now.get("current"), now["kind"]
    if kind == "file" or current in (None, "", []):
        return False
    if kind == "checkbox":
        return bool(current) == bool(value)
    if kind == "checkgroup":
        return {str(v).lower() for v in value} == {str(c).lower() for c in current}
    return _same(current, value)


def _match(fresh: list[dict], f: dict) -> dict | None:
    """Finds a field among those the page shows now: by its tag, else by its name and label."""
    by_hid = next((n for n in fresh if n["hid"] == f["hid"]), None)
    if by_hid and (by_hid["sig"] == f.get("sig") or not f.get("sig")):
        return by_hid
    return next((n for n in fresh if n["sig"] == f.get("sig")), by_hid if by_hid and by_hid["kind"] == f["kind"] else None)


def _fill(b: Browser, job_id: str, fields: list[dict], shot: str) -> dict:
    """Enters the approved values, reads each back, and reports what would not take.

    Returns the errors by field, a screenshot, the form's Next and Submit
    buttons, and `new`: questions that appeared only once others were answered.
    """
    page = b.tab(job_id)
    frame, fresh = _read(page, options=False)
    errors, filled = {}, []
    for f in fields:
        value = f.get("value")
        if value in (None, "", []) or f.get("skip"):
            continue
        now = _match(fresh, f)
        if now is None:
            errors[f["hid"]] = "this question is no longer on the page"
            continue
        if _holds(now, value):
            continue  # already holds the answer
        try:
            _fill_one(page, frame, f, now, value)
            filled.append(f)
        except Exception as e:  # noqa: BLE001 - report the field and carry on with the rest
            if "has been closed" in str(e):
                raise
            errors[f["hid"]] = str(e).splitlines()[0][:200]
    page.wait_for_timeout(500)
    frame, after = _read(page)
    for f in filled:
        now = _match(after, f)
        if now is not None and not _held(now, f["value"]):
            errors[f["hid"]] = f"the form did not keep this answer (it shows {str(now.get('current'))[:60]!r})"
    known = {f.get("sig") for f in fields}
    Path(shot).parent.mkdir(parents=True, exist_ok=True)
    _shoot_form(page, shot)
    nxt, sub, _ = _buttons(page, frame)
    return {"errors": errors, "screenshot": shot, "next": nxt is not None and sub is None,
            "submit": sub is not None, "new": [f for f in after if f["sig"] not in known],
            "password": _survey(frame)["password"]}


def _shoot_form(page, shot: str) -> None:
    """Screenshots the whole page with the form on it."""
    try:
        page.screenshot(path=shot, full_page=True)
    except Exception as e:  # noqa: BLE001 - a page taller than the browser allows falls back to what is on screen
        if "has been closed" in str(e):
            raise
        page.screenshot(path=shot)


# Moving on ---------------------------------------------------------------------

def _problems(fields: list[dict], survey: dict) -> list[dict]:
    """Returns what the form objects to: each refused field with the site's words, then page-wide messages."""
    out = [{"hid": f["hid"], "label": f["label"], "message": f["invalid"]} for f in fields if f.get("invalid")]
    said = {p["message"] for p in out}
    out += [{"hid": None, "label": "", "message": a} for a in survey["alerts"] if a not in said]
    return out


# Empty boxes that appeared after the press, in a form that now asks for an emailed code.
CODE_BOXES_JS = """() => [...document.querySelectorAll('input[data-hal-id]:not([data-hal-step])')]
    .filter(e => e.offsetWidth > 0 && !e.value && /^(text|tel|number|password|)$/.test(e.type))
    .map(e => e.getAttribute('data-hal-id'))"""


def _after_press(b: Browser, job_id: str, page, final: bool) -> dict:
    """Reads what a form shows once its Next or Submit button was pressed."""
    if final:
        try:
            page.wait_for_function(
                "() => /thank|received|submitted|success|verification code|security code/i.test(document.body.innerText)"
                " || document.querySelectorAll('[aria-invalid=true]').length > 0", timeout=20_000)
        except Exception:  # noqa: BLE001 - judged from the page below
            pass
    else:
        page.wait_for_timeout(1_200)
    _settle(page)
    page = b.page(job_id) or page
    frame, fields = _read(page, options=False)
    survey = _survey(frame)
    whole = survey if frame is page.main_frame else _survey(page.main_frame)
    stayed = survey["stayed"]
    needs = _needs(whole, fields)
    boxes = frame.evaluate(CODE_BOXES_JS) if stayed and CODE_WALL.search(survey["text"]) else []
    if boxes or needs == "code":
        # The site wants the code it has just emailed before it takes the application.
        return {"stayed": stayed, "sent": False, "problems": [], "challenge": False, "url": page.url,
                "needs": "code", "code": True, "fields": []}
    if stayed and not needs:
        return {"stayed": True, "sent": False, "problems": _problems(fields, survey),
                "challenge": whole["challenge"] or survey["challenge"], "url": page.url, "needs": ""}
    if _is_form(fields) and not needs:
        frame, fields = _read(page)
    report = _report(page, frame, fields, whole, needs, pressed=True)
    # A form that has gone, leaving the site's thanks, is an application sent.
    report["sent"] = not _is_form(fields) and not needs and (
        SUCCESS.search(whole["text"]) is not None
        or re.search(r"thank|confirm|success|submitted|complete", urlparse(page.url).path, re.I) is not None)
    return {"stayed": False, "problems": [], "challenge": whole["challenge"], **report}


def _press_step(b: Browser, job_id: str, final: bool, shot: str | None = None) -> dict:
    """Presses the form's Next button, or its Submit button when `final`, and reads what follows."""
    page = b.tab(job_id)
    frame, _fields = _extract(page)
    nxt, sub, where = _buttons(page, frame)
    control = sub if final else nxt
    if control is None:
        kind = "submit" if final else "Next"
        raise RuntimeError(f"no {kind} button found; press it in the browser window, then read the page there")
    # Marks this step's controls so a form that refuses to move on can be told from a new step.
    frame.evaluate("() => document.querySelectorAll('[data-hal-id]').forEach(e => e.setAttribute('data-hal-step', ''))")
    where.locator(f"[data-hal-nav='{control['id']}']").first.click(timeout=10_000)
    out = _after_press(b, job_id, page, final)
    if shot:
        Path(shot).parent.mkdir(parents=True, exist_ok=True)
        _shoot_form(b.page(job_id) or page, shot)
        out["screenshot"] = shot
    out["pressed"] = control["name"]
    return out


def _next_step(b: Browser, job_id: str) -> dict:
    return _press_step(b, job_id, False)


def _submit(b: Browser, job_id: str, shot: str) -> dict:
    """Presses the form's submit button and reports what the page says afterwards."""
    out = _press_step(b, job_id, True, shot)
    confirmed = SUCCESS.search(_survey(b.tab(job_id).main_frame)["text"]) if out.get("sent") else None
    return {**out, "submitted": bool(out.get("sent")), "invalid": len(out["problems"]),
            "excerpt": confirmed.group(0) if confirmed else ""}


def _enter_code(b: Browser, job_id: str, code: str, shot: str) -> dict:
    """Types an emailed security code into the boxes the form opened for it, and submits again."""
    page = b.tab(job_id)
    frame, fields = _extract(page)
    boxes = frame.evaluate(CODE_BOXES_JS) or [f["hid"] for f in fields if f["kind"] == "text" and not f.get("current")]
    if not boxes:
        raise RuntimeError("no box found for the security code; type it in the browser window")
    first = frame.locator(f"[data-hal-id='{boxes[0]}']")
    if len(boxes) == 1:
        first.fill(code)
    else:
        # One box a character: typing into the first moves along the row.
        first.click()
        page.keyboard.type(code, delay=40)
        if frame.evaluate(CODE_BOXES_JS):
            for hid, char in zip(boxes, code):
                frame.locator(f"[data-hal-id='{hid}']").fill(char)
    return _submit(b, job_id, shot)


def _read_here(b: Browser, job_id: str) -> dict:
    """Reads the form on whatever page the job's tab shows now."""
    page = b.tab(job_id)
    _dismiss_banners(page)
    frame, fields = _read(page)
    return _report(page, frame, fields, _survey(page.main_frame), "", False)


# Searching and showing ---------------------------------------------------------

def _search(b: Browser, queries: list[str], pause: float) -> tuple[list[list[dict]], bool]:
    """Runs web searches in one tab and returns each query's results, and whether the engine refused.

    The searches run here because search engines refuse requests that do not
    come from a browser window.
    """
    opened = b._ctx is None
    page = b.context().new_page()
    found, refused = [], False
    try:
        for i, query in enumerate(queries):
            if i:
                page.wait_for_timeout(pause * 1000)
            page.goto(SEARCH + quote_plus(query), wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            try:
                page.wait_for_selector("article[data-testid='result']", timeout=10_000)
            except Exception:  # noqa: BLE001 - no results, or the engine's bot check
                refused = "anomaly" in page.content()
                if refused:
                    break
            found.append(page.evaluate(RESULTS_JS))
    finally:
        page.close()
        if opened:
            # A window opened only to search has nothing else to show.
            b._ctx.close()
    return found, refused


def _look(b: Browser, job_id: str, shot: str) -> dict:
    """Describes what the job's tab shows now: its buttons, any open dialog, and a picture."""
    page = b.tab(job_id)
    Path(shot).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=shot)

    def texts(selector: str, limit: int) -> list[str]:
        return [" ".join(t.split())[:limit] for t in page.locator(selector).all_inner_texts() if t.strip()]

    return {"url": page.url, "title": page.title(), "screenshot": shot,
            "buttons": texts("button:visible, [role=button]:visible, input[type=submit]:visible", 60)[:40],
            "dialogs": texts("[role=dialog]:visible, dialog:visible", 1500)[:3]}


def _show(b: Browser, job_id: str) -> bool:
    page = b.page(job_id)
    if page is None:
        return False
    page.bring_to_front()
    return True


def _close(b: Browser, job_id: str) -> None:
    b.close(job_id)


def mode() -> str:
    """Returns where forms are driven: Hal's own "window" or "headless" browser, or the "extension"."""
    return os.environ.get("JOBHUNT_APPLY_BROWSER") or CONFIG.get("apply_browser", "window")


def where() -> str:
    """Names the place the applicant looks for the form, for messages."""
    return "the job's tab" if mode() == "extension" else "Hal's window"


def _via(task, *args):
    """Runs a task in Hal's own browser, or in the applicant's through the extension."""
    if mode() == "extension":
        from . import extension
        return getattr(extension, task.__name__.lstrip("_"))(*args)
    return BROWSER.call(task, *args)


def open_form(job_id: str, url: str) -> dict:
    return _via(_open_form, job_id, url)


def fill(job_id: str, fields: list[dict], shot: Path) -> dict:
    return _via(_fill, job_id, fields, str(shot))


def submit(job_id: str, shot: Path) -> dict:
    return _via(_submit, job_id, str(shot))


def read_here(job_id: str) -> dict:
    return _via(_read_here, job_id)


def resume(job_id: str, passed: str, wall: str) -> dict:
    return _via(_resume, job_id, passed, wall)


def enter_code(job_id: str, code: str, shot: Path) -> dict:
    return _via(_enter_code, job_id, code, str(shot))


def press_control(job_id: str, name: str) -> dict:
    return _via(_press_control, job_id, name)


def peek(job_id: str) -> dict:
    return _via(_peek, job_id)


def next_step(job_id: str) -> dict:
    return _via(_next_step, job_id)


def search(queries: list[str], pause: float = 0) -> tuple[list[list[dict]], bool]:
    return _via(_search, queries, pause)


def look(job_id: str, shot: Path) -> dict:
    return _via(_look, job_id, str(shot))


def show(job_id: str) -> bool:
    return _via(_show, job_id)


def close(job_id: str) -> None:
    if mode() != "extension":
        BROWSER.call(_close, job_id)

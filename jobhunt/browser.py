"""Drives a real browser through job application forms with Playwright.

One thread owns the browser; other threads queue work with `BROWSER.call`.
Between calls that thread keeps Playwright serviced, so the window keeps up
while Hal is idle (a person's own Submit there is never held up).
A form's controls are tagged with data-hal-id attributes when read, so the
values approved in the web interface land in the controls they were proposed
for, and every value entered is read back from the page. Requires the
playwright package (see .venv).

Hal never presses Submit: every press goes through `apply.guard`,
only Next is ever pressed on a form, nothing here presses Enter, and for the
whole of each command that acts on a page a block in the page's own world
cancels any submit the page or a press would make. The person presses Submit
on the employer's site.

Notes about job sites drive the hooks
in both engines: `open_url`, `press_first`, `cookie_button` and
`field_transform` here (the helper reads the same notes through
`extension.py`). With no note of a kind, each hook does what Hal did before
notes existed. Each note used is recorded with `sites.notes.used` and
returned as a trace line for the Form step.
"""
import collections
import concurrent.futures
import contextlib
import difflib
import importlib.util
import json
import logging
import os
import queue
import re
import threading
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from .apply import guard
from .core import paths

log = logging.getLogger(__name__)

# PROFILE, SHOTS and SIGNED_IN (sites the applicant has signed in to in Hal's browser, where Apply may send a
# stored CV at once) are resolved when used, so tests and the data move point them elsewhere.
_LIVE = {"PROFILE": paths.browser_profile, "SHOTS": paths.shots_dir,
         "SIGNED_IN": lambda: paths.data_dir() / "signed-in.json"}


def __getattr__(name):  # old path constants, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    raise AttributeError(name)


def _live(name: str):
    """An old path constant: the value a test assigned to the module, else the live folder."""
    return globals()[name] if name in globals() else _LIVE[name]()


NAV_TIMEOUT = 45_000  # ms

# The page readers are shared with the extension, which runs them in the applicant's own browser.
PAGE_JS = (paths.code_dir() / "extension" / "page.js").read_text()
EXTRACT_JS = "() => {\n" + PAGE_JS + "\nreturn halExtract();\n}"
SURVEY_JS = "() => {\n" + PAGE_JS + "\nreturn halSurvey();\n}"
OPTIONS_JS = "(hid) => {\n" + PAGE_JS + "\nreturn halOptions(hid);\n}"
STALE_JS = "() => {\n" + PAGE_JS + "\nreturn halStale();\n}"
PAGE_TEXT_JS = "() => {\n" + PAGE_JS + "\nreturn halText();\n}"

RESULTS_JS = "() => {\n" + PAGE_JS + "\nreturn halResults();\n}"
POPUP_JS = "() => {\n" + PAGE_JS + "\nreturn halPopup();\n}"
POPUP_GONE_MS = 400  # a closed pop-up fades out before the page under it takes clicks
# Apply controls on an advert. Ones that hand the application to another account are left alone.
APPLY_BUTTON = re.compile(
    r"^\s*((easy|quick|1[- ]click) )?apply\b(?!.{0,40}\b(linkedin|indeed|seek|xing|filters?|later|coupon|code)\b).{0,40}$|"
    r"^\s*(start|begin|continue|complete|resume) (your |my |the )?application\s*$|^\s*i'?m interested\s*$", re.I)
# The words live in the guard, which both engines share.
NEXT_BUTTON = guard.NEXT_WORDS
SUBMIT_BUTTON = guard.FINAL_WORDS
SUCCESS = re.compile(r"thank(s| you)[^.]{0,60}(appl|submi|interest)|"
                     r"application (has been |was |is )?(successfully )?(received|submitted|sent|complete)|"
                     r"we('ve| have) (received|got) your application|successfully (submitted|applied)|"
                     r"your application is (in|on its way)", re.I)
APPLIED = re.compile(r"you(('ve| have) already| have|'ve)? applied (for|to) this (job|role|position|vacancy)", re.I)
SIGN_IN = re.compile(r"\b(sign|log)[ -]?in\b|\blogin\b|\bregister\b|create (an |your )?account", re.I)
# The host of an account service, which may say nothing of signing in on the page: account.ycombinator.com asked
# only for an email address, and Hal typed it in and pressed on (testers, 2026-11-05).
SIGN_IN_HOST = re.compile(r"^(accounts?|login|auth|signin|sso|id|identity)\.", re.I)
SIGN_IN_TEXT = 600   # characters of the page's opening words that may say it is for signing in
# What a sign-in page says in its opening words; a bare "login" link in a site's menu does not count (every Hacker
# News page starts "... submit login", and each job post there was taken for a sign-in, testers 2026-11-05).
SIGN_IN_WORDS = re.compile(r"\b(sign|log)[ -]?in (to|with)\b|forgot (your )?password|don'?t have an account|"
                           r"create (an |your )?account|welcome back", re.I)
REGISTER_FIELDS = 5  # questions beyond which a page with a password box is an application, not an account page
# A session on the page's site: "Sign out", or an account's own pages, which Totaljobs shows in place of it; its
# plain Apply then sent the saved CV at once (testers, 2026-11-05).
SIGN_OUT = re.compile(r"^\s*((sign|log) ?out|my (account|profile|jobs|applications|dashboard))\s*$", re.I)
CLOSED = re.compile(r"(no longer|not) (available|accepting applications|open|exists?|active|live)|not available anymore|"
                    r"(job|position|vacancy|role|posting|advert|page) (you requested )?(has |was |is )?"
                    r"(been )?(filled|expired|closed|removed|not found|unavailable)|"
                    r"(job|position|vacancy|page) not found|applications? (are|is|have|has) (now )?closed|\b404\b|"
                    # "Applications Closed", "Applications for this position have now closed" (ProductionBase,
                    # testers 2026-11-04)
                    r"\bapplications? closed\b|applications? for this (position|role|job|vacancy) (have|has) "
                    r"(now )?closed|"
                    # "This listing went offline", "the listing that you're looking for is expired" (Totaljobs,
                    # testers 2026-11-05)
                    r"(listing|job|advert|vacancy|posting) went offline|"
                    r"(listing|job|advert|vacancy|posting)( that you'?re looking for)? (is|has) (now )?(expired|gone)",
                    re.I)
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
# docs.google.com/forms is a form that employers embed, so it is the one part of google.com that is read.
NOT_FORM_FRAME = re.compile(r"recaptcha|hcaptcha|challenges\.cloudflare|googletagmanager|doubleclick|"
                            r"google\.com/(?<!docs\.google\.com/)|docs\.google\.com/(?!forms/)|"
                            r"facebook|intercom|hotjar|about:blank|youtube|vimeo|stripe|cookie", re.I)
# Frames the helper opens on their own to read, since it reads the top page of a tab: ATS_FRAME and these.
FORM_FRAME = ("docs.google.com/forms",)
MAX_PRESSES = 6  # Apply controls followed before giving up on finding the form
# Human checks: reCAPTCHA, hCaptcha and Cloudflare Turnstile, whose frame hides in a closed shadow root.
CAPTCHA = ("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], "
           "input[name='cf-turnstile-response'], .cf-turnstile")
SEARCH = "https://duckduckgo.com/?q="
GONE = "the form's tab was closed"
BLOCK_RETRY_MS = 500  # a page between two addresses takes the block once it has settled
IDLE_WAIT_S = 0.25    # how long the browser thread waits for work before it lets Playwright catch up
PUMP_MS = 50          # how long it lets Playwright read the browser each time

# Installed in the page's own world for the whole of each command that acts on a page: a submit
# the page or a press would make is cancelled and noted, as are the form's own send methods, unless
# the submitter is the vetted Next control marked `data-hal-allow`. Many multi-page forms move on with a
# real submit, which is why that one control is let through. `UNBLOCK_JS` takes the block away, so the
# person's own Submit always works, and returns what it cancelled. Each command holds the block: two
# commands in one tab (a watch's read beside a fill) share it, and it goes only when the last lets go.
BLOCK_JS = """() => {
  if (window.__halUnblock) { window.__halHolders = (window.__halHolders || 1) + 1; return true; }
  const proto = HTMLFormElement.prototype, submit = proto.submit, request = proto.requestSubmit;
  const cancelled = [];
  const allowed = el => Boolean(el && el.hasAttribute && el.hasAttribute('data-hal-allow'));
  const onSubmit = e => {
    if (allowed(e.submitter)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    cancelled.push('the page tried to send the form');
  };
  window.addEventListener('submit', onSubmit, true);
  proto.submit = function () { cancelled.push('a script on the page tried to send the form'); };
  proto.requestSubmit = function (by) {
    if (allowed(by)) return request.call(this, by);
    cancelled.push('a script on the page asked to send the form');
  };
  window.__halHolders = 1;
  window.__halUnblock = () => {
    if (--window.__halHolders > 0) return [];   // the last holder reports what was cancelled
    window.removeEventListener('submit', onSubmit, true);
    proto.submit = submit;
    proto.requestSubmit = request;
    delete window.__halUnblock;
    delete window.__halHolders;
    return cancelled;
  };
  return true;
}"""
UNBLOCK_JS = "() => window.__halUnblock ? window.__halUnblock() : []"


NO_PLAYWRIGHT = ("Hal's own window needs the browser part, which is not installed here. Install it again with "
                 "the browser option, or let the browser helper fill in forms in your own browser.")


class BrowserMissing(RuntimeError):
    """Playwright is not installed, so Hal's own browser window cannot open."""

    def __init__(self) -> None:
        super().__init__(NO_PLAYWRIGHT)


class TabGone(RuntimeError):
    """The job's tab, or the whole browser window, was closed while Hal was using it."""

    def __init__(self) -> None:
        super().__init__(GONE)


@contextlib.contextmanager
def _guarded(page, job_id: str):
    """Holds the page-world submit block on every frame of the page while Hal acts on it."""
    for frame in _frames(page):
        try:
            frame.evaluate(BLOCK_JS)
        except Exception:  # a frame that went away; the main frame is retried below
            if frame is not page.main_frame:
                continue
            page.wait_for_timeout(BLOCK_RETRY_MS)
            page.main_frame.evaluate(BLOCK_JS)
    try:
        yield
    finally:
        if not page.is_closed():
            for frame in _frames(page):
                try:
                    cancelled = frame.evaluate(UNBLOCK_JS)
                except Exception as e:  # a page that moved on took its block with it
                    log.debug("could not lift a page's block: %s", e)
                    continue
                for what in dict.fromkeys(cancelled or []):
                    guard.record(job_id, frame.url, what)


def _facts(survey: dict, fields: list[dict]) -> dict:
    """What the guard needs to know about a page: whether it holds a form, is final, and is signed in."""
    form = _is_form(fields)
    nxt = _step_control(survey, NEXT_BUTTON) if form else None
    return {"form": form, "final": form and nxt is None, "signed_in": _signed_in(survey)}


# Notes about job sites -------------------------------------------

def notes_for(url: str, job_id: str | None = None):
    """The notes about job sites that apply to this page (`sites.notes.for_page`).

    With only the built-in notes on, each hook below does what Hal did before notes existed.
    """
    from .sites import notes

    return notes.for_page(url or "", job_id=job_id)


def note_used(note, job_id: str | None, field: str, what: str) -> dict:
    """Records one use of a note (`sites.notes.used`) and returns its trace line for the form.

    The line also carries the note's `id`, `text` and `origin`, the shape autofill lists a note in, so the Form
    step's "Notes Hal used on this site" names every note, whichever part of Hal used it.
    """
    from .sites import notes

    notes.used(note.id, job_id or "", ok=True, detail=what)
    return {"note": note.id, "field": field, "what": what, "id": note.id, "text": note.text, "origin": note.origin}


def _first(notes, kind: str):
    return notes.first(kind) if notes is not None else None


def _city(value: str) -> str:
    return value.split(",")[0].strip()


def _uk_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return "0" + digits[2:] if digits.startswith("44") else digits


def transformed(value, how: str):
    """The answer as a `field_transform` note says to type it: city_only, digits_only, uk_phone, date:FORMAT
    or max_len:N. Raises ValueError for a transform no engine knows, so a bad note is never half applied."""
    from datetime import datetime

    text = str(value)
    if how == "city_only":
        return _city(text)
    if how == "digits_only":
        return re.sub(r"\D", "", text)
    if how == "uk_phone":
        return _uk_phone(text)
    if how.startswith("date:"):
        iso = _date(text)
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime(how[len("date:"):])
        except ValueError:
            return text
    if how.startswith("max_len:") and how[len("max_len:"):].isdigit():
        return text[:int(how[len("max_len:"):])]
    raise ValueError(f"no transform {how!r}")


def with_notes(job_id: str, url: str, fields: list[dict]) -> tuple[list[dict], list[dict]]:
    """The fields with each `field_transform` note applied to its answer, and the trace of what changed.

    Applied on the server, before the values reach either engine, so both modes type the same thing.
    """
    notes = notes_for(url, job_id)
    if notes is None:
        return fields, []
    out, trace = [], []
    for f in fields:
        value = f.get("value")
        note = next((n for n in notes.for_field(f.get("label") or "") if n.kind == "field_transform"), None)
        if note is None or f.get("kind") in ("file", "checkbox", "radio", "checkgroup") or value in (None, "", []):
            out.append(f)
            continue
        new = transformed(value, note.act.get("transform") or "")
        if new != value:
            what = (f"Typed {new!r} for {f.get('label') or 'this question'}, because of the note "
                    f"\"{note.text}\". Without it Hal would type {str(value)!r}.")
            line = note_used(note, job_id, f.get("label") or "", what)
            f = {**f, "value": new, "notes_used": [*(f.get("notes_used") or []), line]}
            trace.append(line)
        out.append(f)
    return out, trace


def application_url(job) -> str:
    """Returns the page holding the application form for a job, as the `open_url` notes about its site say
    (`sites.behaviour.application_url`): the built-in ones open Ashby's and Lever's forms directly and the
    Greenhouse form on an employer's own careers page; a person's note about the site wins over them."""
    from .sites import behaviour

    return behaviour.application_url(job, notes_for(job["url"] or "", job["id"]))


class Browser:
    """Owns the Playwright browser on a dedicated thread."""

    def __init__(self) -> None:
        self._work: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ctx = None
        self.pages: dict[str, list] = {}

    def call(self, fn, *args, timeout: float = 600):
        """Runs fn(browser, *args) on the browser thread and returns its result.

        Raises `BrowserMissing` at once without Playwright: the thread would die on its import and leave the
        caller waiting out `timeout` (the WSL2 CI job, installed without the browser extra, 2026-11-05).
        """
        if importlib.util.find_spec("playwright") is None:
            raise BrowserMissing
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
                try:
                    fn, args, fut = self._work.get(timeout=IDLE_WAIT_S)
                except queue.Empty:
                    self._pump()
                    continue
                try:
                    fut.set_result(fn(self, *args))
                except Exception as e:  # errors are re-raised in the caller's thread
                    # A closed tab is reported as such, whichever step it interrupted.
                    fut.set_exception(TabGone() if "has been closed" in str(e) else e)

    def _pump(self) -> None:
        """Lets Playwright read the browser while no work is queued.

        Playwright's sync API reads its driver only while its thread is inside a call. Left unread, the
        driver stopped reading the browser on Windows, and a page's own form post (the person's Submit in
        Hal's window) waited until Hal next used the browser (seen 2026-10-23). A tab the page
        opens is followed here too, as soon as it opens.
        """
        ctx = self._ctx
        if ctx is None:
            return
        try:
            pages = ctx.pages
            if pages:
                pages[0].wait_for_timeout(PUMP_MS)
        except Exception as e:  # a window closed while idle is launched again when next used
            log.debug("the idle window stopped answering: %s", e)

    def context(self):
        """Returns the browser context, launching it (again, if the window was closed)."""
        if self._ctx is not None:
            try:
                self._ctx.pages  # noqa: B018 - raises once the browser has gone
                return self._ctx
            except Exception:  # the browser is gone for any reason; the next call starts a new one
                self._ctx = None
        _live("PROFILE").mkdir(parents=True, exist_ok=True)
        headless = mode() == "headless"
        # Wayland refuses a window's request to raise itself, so "Show browser
        # window" only works when the window runs through XWayland.
        args = ["--ozone-platform=x11"] if not headless and os.environ.get("DISPLAY") else []
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(_live("PROFILE")), headless=headless, viewport={"width": 1280, "height": 900},
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
            except Exception as e:  # already closed
                log.debug("could not close a tab: %s", e)


BROWSER = Browser()


# A job board's own job search and its job-alert sign-up sit beside the advert, and are never the application:
# Adzuna's "What?", "Where?", "Salaries" and "Receive similar jobs by email" were filled as the form (testers,
# 2026-11-04). The search words match whole labels only, so a form's own "Where are you based?" stays.
# A job board's own search: "Job title, skill or company" and "Town, city or postcode" on Totaljobs, and a box known
# only by its name "q", as Hacker News's is (testers, 2026-11-05).
SITE_SEARCH = re.compile(r"^\s*((what|where)\s*\??|keywords?|q|query|search( for)?( jobs)?|"
                         r"job title,? (skills?|keywords?|company)\b.*|(town|city)[ ,/]*((town|city)[ ,/]*)?or "
                         r"(post|zip) ?code|salaries|distance|radius|sort( by)?|date posted)\s*:?\s*$", re.I)
JOB_ALERT = re.compile(r"job alerts?|similar (new )?(jobs|roles|vacancies)|jobs like this|newsletter|"
                       r"(send|email) me (new |similar |more )?(jobs|roles|vacancies)|new jobs by email", re.I)


# A site's own comment box, known only as "text" or "comment": Hacker News shows one under every post to a signed-in
# reader, and Hal typed an application answer into it, one press from being posted (testers, 2026-11-05).
COMMENT_BOX = re.compile(r"^\s*(text|comments?|reply|(add|leave|write|post) a (comment|reply)|your (comment|reply))"
                         r"\s*:?\s*$", re.I)


def not_the_application(field: dict) -> bool:
    """True for a box that belongs to the site rather than the application: its job search, job alerts or
    comment box, by its words, or by where it sits on the page (`site_box`, from page.js)."""
    if field.get("site_box"):
        return True
    label = field.get("label") or ""
    if field.get("kind") == "textarea" and COMMENT_BOX.search(label):
        return True
    return bool(SITE_SEARCH.search(label) or JOB_ALERT.search(f"{label} {field.get('help') or ''}"))


def application_fields(fields: list[dict]) -> list[dict]:
    """The fields of a page that could be the application's, without the site's search and job-alert boxes."""
    return [f for f in fields if not not_the_application(f)]


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


# A button inside a form is the form's own: "I agree" there is consent the person gives, never a banner.
IN_FORM_JS = "e => Boolean(e.closest('form'))"


def banner_words(notes) -> tuple[list, object]:
    """The cookie-banner buttons to try, in order, and the words never to press, after any `cookie_button` note.

    `{text}` puts that button first; `{never: [..]}` keeps those words from ever being pressed.
    """
    patterns, never = list(COOKIE_BUTTONS), None
    note = _first(notes, "cookie_button")
    if note is not None:
        if note.act.get("text"):
            patterns.insert(0, re.compile(rf"^\s*{re.escape(note.act['text'])}\s*$", re.I))
        if note.act.get("never"):
            never = re.compile(r"^\s*(" + "|".join(re.escape(w) for w in note.act["never"]) + r")\s*$", re.I)
    return patterns, never


def _dismiss_banners(page, notes=None) -> None:
    """Closes a cookie banner, refusing optional cookies where it can, then a job-alert or similar pop-up
    (`halPopup` in page.js); never presses a button inside a form."""
    _cookie_banner(page, notes)
    from .core import settings

    if not settings.get("applying.close_popups"):
        return
    try:
        if page.main_frame.evaluate(POPUP_JS):
            page.wait_for_timeout(POPUP_GONE_MS)
    except Exception as e:  # the page moved on mid-look; the next read looks again
        log.debug("could not look for a pop-up: %s", e)


def _cookie_banner(page, notes=None) -> None:
    patterns, never = banner_words(notes)
    for pattern in patterns:
        try:
            for button in page.get_by_role("button", name=pattern).all():
                if button.evaluate(IN_FORM_JS) or (never and never.search(button.inner_text(timeout=1_000))):
                    continue
                button.click(timeout=3_000)
                page.wait_for_timeout(500)
                return
        except Exception as e:  # a banner that will not close may not block anything
            log.debug("could not close a banner: %s", e)


def _settle(page) -> None:
    if "adzuna." in urlparse(page.url).netloc and "/land/" in page.url:
        # Adzuna links show a holding page that hands over to the advertiser a few seconds later.
        try:
            page.wait_for_url(lambda u: "adzuna." not in urlparse(u).netloc, timeout=15_000)
        except Exception as e:  # some adverts are hosted on Adzuna itself
            log.debug("the advert stayed on Adzuna: %s", e)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception as e:  # some pages poll forever; the DOM is ready regardless
        log.debug("the page never went quiet: %s", e)


def site_key(url: str) -> str:
    """The site a page belongs to, such as reed.co.uk for secure.reed.co.uk (`sites.notes.site_key`).

    `acme.wd3.myworkdayjobs.com` and `boards.greenhouse.io/acme` are each their own site, so signing in to one
    Workday employer never marks every other as signed in. One rule for the engines, the notes and the sites
    table, so a sign-in and a note about an employer are kept under the same key.
    """
    from .sites import notes

    return notes.site_key(url)


_site = site_key   # the old name, kept for callers


def signed_in_at(url: str) -> bool:
    """True when the person has said they signed in to this page's site (its employer, on a shared system)."""
    return site_key(url) in _signed_in_sites()


def note_signed_in(url: str) -> None:
    """Remembers that the person signed in to this page's site, so Apply there is left to them."""
    _live("SIGNED_IN").write_text(json.dumps(sorted(_signed_in_sites() | {site_key(url)})))


def _signed_in_sites() -> set[str]:
    try:
        return set(json.loads(_live("SIGNED_IN").read_text()))
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
            fields = application_fields(frame.evaluate(EXTRACT_JS))
        except Exception:  # a frame that has gone, or that the site forbids reading
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
    except Exception as e:  # the page moved to another address mid-read
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
    except Exception as e:  # mid-navigation; an empty survey is retried by the caller's loop
        if "has been closed" in str(e):
            raise
        return {"url": frame.url, "title": "", "controls": [], "headings": [], "text": "", "alerts": [],
                "password": False, "invalid": 0, "captcha": False, "challenge": False, "stayed": False}


# Presses the form or dialog around a control, outside its open list; returns false when there is none.
PRESS_AROUND_JS = """e => {
  const around = e.closest("[role=dialog], [aria-modal=true], dialog, form");
  if (!around) return false;
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"])
    around.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
  return true;
}"""


def _close_list(page, box) -> None:
    """Closes an open dropdown without disturbing the form around it.

    Escape also closes the dialog a form may sit in (Workable's did, 2026-10-20), so it is
    only the last resort, for a list in neither a form nor a dialog.
    """
    try:
        # A box without focus never loses it, so its list stays open (react-select's, testers 2026-11-05).
        box.evaluate("e => { if (document.activeElement !== e) e.focus(); e.blur(); }")
        # A list showing "No results" has no options but is still open, and may hide the rest of the form (Ashby's).
        still_open = box.get_attribute("aria-expanded") == "true"
        if (page.locator("[data-hal-opt]:visible").count() or still_open) and not box.evaluate(PRESS_AROUND_JS):
            box.press("Escape")
    except Exception as e:  # nothing was open
        log.debug("no list to close: %s", e)


def _open_list(box) -> None:
    """Opens a dropdown: by a click, or from the keyboard when a styled layer lies over the control."""
    try:
        box.click(timeout=1_500)
    except Exception:  # a covered control: the next try forces the click
        try:
            box.click(timeout=1_500, force=True)
        except Exception:  # still covered: fall back to the keyboard
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
    except Exception:  # an option list that will not open is filled by typing
        return []


def _needs(survey: dict, fields: list[dict]) -> str:
    """Names what only the applicant can do on this page: "sign-in", "check", "code", or "" for nothing."""
    head = f"{survey['title']} {' '.join(survey['headings'])}"
    form = _is_form(fields)
    if BOT_CHECK.search(survey["title"]) or (not form and BOT_CHECK.search(survey["text"][:400])):
        return "check"
    if only_code_boxes(fields, survey["text"][:3000]):
        return "code"
    if survey["password"]:
        # A registration form that also asks the usual questions is filled in, apart from its password.
        return "" if form and len(fields) > REGISTER_FIELDS else "sign-in"
    if not form and 0 < len(fields) <= 3 and CODE_WALL.search(survey["text"][:3000]):
        return "code"
    if form:
        return ""
    # Sign-in pages that ask for the email address first show no password box yet.
    if 0 < len(fields) <= 2 and _apply_control(survey) is None and sign_in_page(head, survey["url"], survey["text"]):
        return "sign-in"
    return ""


def sign_in_page(head: str, url: str, text: str) -> bool:
    """True when a page's title and headings (`head`), its address or its opening words say it is for signing in."""
    where = urlparse(url)
    return bool(SIGN_IN_HOST.search(where.hostname or "") or SIGN_IN.search(f"{head} {where.path}")
                or SIGN_IN_WORDS.search(text[:SIGN_IN_TEXT]))


def _signed_in(survey: dict) -> bool:
    """True when the applicant has an account session on the page's site."""
    return signed_in_at(survey["url"]) or any(SIGN_OUT.search(c["name"]) for c in survey["controls"])


def _apply_control(survey: dict) -> dict | None:
    """Returns the page's Apply link or button, preferring one in the advert over one in a menu."""
    # Never one in the page's footer, such as Hacker News's "Apply to YC" (testers, 2026-11-05).
    found = [c for c in survey["controls"] if APPLY_BUTTON.search(c["name"]) and not c.get("inFooter")]
    found.sort(key=lambda c: (c["inNav"], not any(h in c["href"] for h in ATS_FRAME)))
    return found[0] if found else None


def _press_first(survey: dict, notes, job_id: str | None) -> dict | None:
    """The control a `press_first` note names ("Press \"Express your interest\" before looking for the
    form"), when the page shows it; it is pressed once, through the guard like any Apply."""
    note = _first(notes, "press_first")
    want = " ".join(str((note.act if note else {}).get("control") or "").split()).lower()
    if not want:
        return None
    control = next((c for c in survey["controls"] if " ".join(c["name"].split()).lower() == want), None)
    if control is not None:
        note_used(note, job_id, "", f"Pressed \"{control['name']}\" first, because of the note \"{note.text}\".")
    return control


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


CLOSED_TEXT = 600   # characters at the top of a page where a board's "no longer available" banner sits


def _closed(survey: dict, status: int | None) -> bool:
    """True when the advert has been taken down: said in its title or headings, by a 404 or 410, or in a
    banner at the top of a page that has no Apply control. Adzuna says "Unfortunately, this job is no longer
    available" in a banner, not a heading, and Hal reported no form instead (testers, 2026-11-04)."""
    head = f"{survey['title']} {' '.join(survey['headings'][:4])}"
    if CLOSED.search(head) is not None or status in (404, 410) \
            or ("greenhouse.io" in survey["url"] and "error=true" in survey["url"]):
        return True
    has_apply = survey["apply"] if "apply" in survey else _apply_control({"controls": survey.get("controls", [])}) \
        is not None
    return not has_apply and CLOSED.search(survey.get("text", "")[:CLOSED_TEXT]) is not None


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

def _press(page, frame, control: dict, facts: dict, kind: str, job_id: str | None = None) -> None:
    """Presses a control the survey found, once the guard allows it; a link that will not take the click is
    followed by address. Raises `guard.WouldSubmit` for a control that could send the application."""
    guard.check(job_id, page.url, control, facts, kind)
    target = frame.locator(f"[data-hal-nav='{control['id']}']").first
    try:
        target.click(timeout=5_000)
    except Exception:  # usually a pop-up lying over the control
        if control["href"].startswith("http"):
            page.goto(control["href"], wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        else:
            target.click(timeout=5_000, force=True)


def _advance(b: Browser, job_id: str, pressed: bool = False, status: int | None = None) -> dict:
    """Presses through advert pages towards the application form and reports where it stopped.

    Stops at the form, at a page only the applicant can pass (`needs`), or when
    no Apply control is left. On a site the applicant is signed in to, Apply
    may send the saved CV at once, so Hal never presses it there: it stops with
    `needs = "apply"` for the person to press it. `pressed` says the person has
    just pressed it, where a confirmation on the page means the application went out.
    """
    page = b.tab(job_id)
    needs = ""
    first_done = False
    for presses in range(MAX_PRESSES + 1):
        notes = notes_for(page.url, job_id)
        with _guarded(page, job_id):
            _settle(page)
            _dismiss_banners(page, notes)
            frame, fields = _read(page, options=False)
            survey = _survey(page.main_frame)
            needs = _needs(survey, fields)
            if needs or _is_form(fields) or presses == MAX_PRESSES or _closed(survey, status):
                break
            control = None if first_done else _press_first(survey, notes, job_id)
            first_done = first_done or control is not None
            control = control or _apply_control(survey)
            if control is None:
                break
            if _signed_in(survey):
                needs = "apply"
                break
            _press(page, page.main_frame, control, _facts(survey, fields), "apply", job_id)
            page.wait_for_timeout(1_500)
        page = b.page(job_id) or page  # the press may have opened the form in a new tab
        status = None
    if _is_form(fields) and not needs:
        with _guarded(page, job_id):
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
    """Carries on towards the form once the applicant has done what the page at `wall` needed.

    After "apply" the person pressed the site's Apply button; Hal follows without pressing it again.
    """
    if passed == "sign-in":
        note_signed_in(wall)
    return _advance(b, job_id, pressed=passed == "apply")


def _press_control(b: Browser, job_id: str, name: str) -> dict:
    """Presses the control with this name, chosen from a page where no Apply control was recognised.

    Raises `guard.WouldSubmit` when the control could send the application.
    """
    page = b.tab(job_id)
    with _guarded(page, job_id):
        frame, fields = _extract(page)
        survey = _survey(page.main_frame)
        control = next((c for c in survey["controls"] if c["name"] == name), None)
        if control is None:
            raise RuntimeError(f"no control named {name!r} on the page")
        _press(page, page.main_frame, control, _facts(survey, fields), "named", job_id)
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
                "challenge": survey["challenge"], "values": {f["sig"]: f.get("current") for f in fields},
                "code": code_boxes(fields, text),
                "code_sigs": [f["sig"] for f in fields if f["hid"] in code_boxes(fields, text)],
                # A password box still empty, on a form Hal has filled in around it.
                "password": frame.evaluate("() => [...document.querySelectorAll('input[type=password]')]"
                                           ".some(e => e.offsetWidth > 0 && !e.value)")}
    except Exception:  # the page is between two addresses
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
            box.press_sequentially(one_line(query), delay=25)
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
    """Ticks or clears one radio or checkbox, by its label when the control itself is hidden.

    A styled choice is often a button; one whose words could send the form is never pressed.
    """
    if not native:
        said = el.evaluate("e => (e.getAttribute('aria-label') || e.innerText || e.value || '').trim()")
        reason = guard.never_press({"name": said, "submit": False}, {"form": True, "final": False}, "named")
        if reason:
            raise guard.WouldSubmit(reason)
        on = el.evaluate("e => e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-pressed') === 'true'"
                         " || /(^|[\\s_-])(active|selected|checked)/i.test(e.className)")
        if on != want:
            el.click(timeout=4_000)
        return
    if el.is_checked() == want:
        return
    try:
        el.set_checked(want, timeout=3_000)
    except Exception:  # a styled control: its label takes the click instead
        try:
            el.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e.parentElement).click()")
        except Exception as e:  # the label click is a second try; the state is checked below
            log.debug("could not click the label: %s", e)
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
        except Exception:  # a page that never goes quiet still has its file; carry on
            pass
        return
    try:
        el.scroll_into_view_if_needed(timeout=2_000)
    except Exception:  # a control that cannot scroll is filled where it is
        pass
    if kind == "checkbox":
        _set_choice(frame, el, bool(value), not now.get("custom"))
    elif kind == "select":
        # A list that takes several answers gets every one of them, not their names joined into one.
        labels = []
        for wanted in (value if isinstance(value, list) else [value]):
            index = _best_option(now.get("options") or [], str(wanted))
            if index is None:
                raise RuntimeError(f"no option {wanted!r}")
            labels.append(now["options"][index])
        el.select_option(label=labels if now.get("multiple") else labels[0])
    elif kind == "combobox":
        _pick(page, frame, el, hid, str(value), bool(now.get("typeable")))
    else:
        text = _date(str(value)) if now.get("type") == "date" else str(value)
        if kind != "textarea" and not now.get("custom"):
            text = one_line(text)
        if now.get("maxlength"):
            text = text[:now["maxlength"]]
        if now.get("custom"):
            el.click(timeout=3_000)
            el.press("ControlOrMeta+a")
            el.press_sequentially(text, delay=2)
        else:
            try:
                el.fill(text, timeout=5_000)
            except Exception:  # a masked input takes keystrokes, not a pasted value
                el.click(timeout=3_000)
                el.press("ControlOrMeta+a")
                el.press_sequentially(text, delay=15)
        el.evaluate("e => e.blur()")


UPLOAD_FAILED = ("the site did not take the file: its name is not on the page. "
                 "Add it in the form yourself")


def uploaded(path, now: dict | None, text: str) -> bool:
    """True when the page shows the file Hal gave it: in the input, or by name where the site lists it.

    Upload widgets often empty their input once they have taken the file and show its name instead; one that
    drops the file silently shows neither, and is reported, never counted as done.
    """
    name = Path(str(path)).name
    if not name:
        return False
    shown = str((now or {}).get("current") or "")
    return name.lower() in shown.lower() or name.lower() in (text or "").lower()


def _held(now: dict, value) -> bool:
    """True when the page shows the value Hal entered in a field (a file is checked by `uploaded`)."""
    current, kind = now.get("current"), now["kind"]
    if kind == "file":
        return bool(current)
    if kind == "select" and now.get("multiple"):
        wanted = value if isinstance(value, list) else [value]
        return {str(v).lower() for v in wanted} == {str(c).lower() for c in current or []}
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
    if kind == "checkgroup" or (kind == "select" and now.get("multiple")):
        wanted = value if isinstance(value, list) else [value]
        return {str(v).lower() for v in wanted} == {str(c).lower() for c in current}
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
    fields, trace = with_notes(job_id, page.url, fields)
    with _guarded(page, job_id):
        out = _fill_page(page, job_id, fields, shot)
    out["notes_used"] = trace
    return out


def _fill_page(page, job_id: str, fields: list[dict], shot: str) -> dict:
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
        except Exception as e:  # report the field and carry on with the rest
            if "has been closed" in str(e):
                raise
            if isinstance(e, guard.WouldSubmit):
                guard.record(job_id, page.url, e.reason)
            errors[f["hid"]] = str(e).splitlines()[0][:200]
    page.wait_for_timeout(500)
    frame, after = _read(page)
    text = _page_text(frame) if any(f["kind"] == "file" for f in filled) else ""
    for f in filled:
        now = _match(after, f)
        if f["kind"] == "file":
            if not uploaded(f["value"], now, text):
                errors[f["hid"]] = UPLOAD_FAILED
        elif now is not None and not _held(now, f["value"]):
            errors[f["hid"]] = f"the form did not keep this answer (it shows {str(now.get('current'))[:60]!r})"
    known = {f.get("sig") for f in fields}
    Path(shot).parent.mkdir(parents=True, exist_ok=True)
    _shoot_form(page, shot)
    nxt, sub, _ = _buttons(page, frame)
    return {"errors": errors, "screenshot": shot, "next": nxt is not None and sub is None,
            "submit": sub is not None, "new": [f for f in after if f["sig"] not in known],
            "password": _survey(frame)["password"]}


def _page_text(frame) -> str:
    """The words the frame shows, including those inside page components (open shadow roots)."""
    try:
        return frame.evaluate(PAGE_TEXT_JS)
    except Exception as e:  # a frame mid-navigation shows no words yet
        if "has been closed" in str(e):
            raise
        return ""


def _shoot_form(page, shot: str) -> None:
    """Screenshots the whole page with the form on it."""
    try:
        page.screenshot(path=shot, full_page=True)
    except Exception as e:  # a page taller than the browser allows falls back to what is on screen
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


def _after_press(b: Browser, job_id: str, page) -> dict:
    """Reads what a form shows once its Next button was pressed."""
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
        # The site wants the code it has just emailed before it goes on.
        return {"stayed": stayed, "sent": False, "problems": [], "challenge": False, "url": page.url,
                "needs": "code", "code": True, "fields": [], "site": _site(page.url)}
    if stayed and not needs:
        return {"stayed": True, "sent": False, "problems": _problems(fields, survey),
                "challenge": whole["challenge"] or survey["challenge"], "url": page.url, "needs": ""}
    if _is_form(fields) and not needs:
        frame, fields = _read(page)
    report = _report(page, frame, fields, whole, needs, pressed=True)
    # A form that has gone, leaving the site's thanks, means the site took Next as the last step.
    report["sent"] = not _is_form(fields) and not needs and (
        SUCCESS.search(whole["text"]) is not None
        or re.search(r"thank|confirm|success|submitted|complete", urlparse(page.url).path, re.I) is not None)
    return {"stayed": False, "problems": [], "challenge": whole["challenge"], **report}


def _press_step(b: Browser, job_id: str, shot: str | None = None) -> dict:
    """Presses the form's Next button, once the guard allows it, and reads what follows.

    Only Next is ever pressed. The block stays on during the press and lets through only a submit made
    by this control, marked `data-hal-allow`, since many multi-page forms move on with a real submit.
    """
    page = b.tab(job_id)
    with _guarded(page, job_id):
        frame, _fields = _extract(page)
        nxt, sub, where = _buttons(page, frame)
        if nxt is None:
            raise RuntimeError("no Next button found; press it in the browser window, then read the page there")
        # A page that also shows a Submit-like button is judged final; Hal stops there.
        guard.check(job_id, page.url, nxt, {"form": True, "final": sub is not None}, "next")
        # Marks this step's controls so a form that refuses to move on can be told from a new step.
        frame.evaluate("() => document.querySelectorAll('[data-hal-id]').forEach(e => e.setAttribute('data-hal-step', ''))")
        target = where.locator(f"[data-hal-nav='{nxt['id']}']").first
        target.evaluate("e => e.setAttribute('data-hal-allow', '')")
        try:
            target.click(timeout=10_000)
        finally:
            with contextlib.suppress(Exception):  # the page may already have moved on
                target.evaluate("e => e.removeAttribute('data-hal-allow')", timeout=1_000)
    out = _after_press(b, job_id, page)
    if shot:
        Path(shot).parent.mkdir(parents=True, exist_ok=True)
        _shoot_form(b.page(job_id) or page, shot)
        out["screenshot"] = shot
    out["pressed"] = nxt["name"]
    return out


def _next_step(b: Browser, job_id: str) -> dict:
    return _press_step(b, job_id)


# A box for the code a site emails: a visible, empty one-line box labelled as a code.
CODE_FIELD = re.compile(r"\bcode\b|\botp\b|one[- ]time|passcode|verification", re.I)
NOT_CODE_TYPES = ("email", "date", "url", "search")


CODE_DIGIT_BOXES = 4   # a code split into one box a digit has at least this many boxes


def only_code_boxes(fields: list[dict], text: str) -> bool:
    """True when every box on the page is for an emailed code, however many there are: Oracle's six boxes, one
    a digit, counted as a form, and Hal said "press Submit" on a page that wanted a code (testers, 2026-11-04).
    The page must say a code was sent, or split it a digit a box; a lone "Code" box may be a discount code."""
    if not fields or not all(f["kind"] == "text" and f.get("type") not in NOT_CODE_TYPES
                             and CODE_FIELD.search(f.get("label") or "") for f in fields):
        return False
    return len(fields) >= CODE_DIGIT_BOXES or CODE_WALL.search(text or "") is not None


def code_boxes(fields: list[dict], text: str) -> list[str]:
    """The tags of the empty boxes a page asks the emailed code in, or [] when it asks for none."""
    if not CODE_WALL.search(text or "") and not only_code_boxes(fields, text):
        return []
    return [f["hid"] for f in fields if f["kind"] == "text" and not f.get("current")
            and f.get("type") not in NOT_CODE_TYPES and CODE_FIELD.search(f.get("label") or "")]


def one_line(text: str) -> str:
    """The text with its line breaks as spaces: a line break typed into a one-line box is Enter."""
    return " ".join(str(text).splitlines())


def _type_code(b: Browser, job_id: str, code: str, boxes: list[str]) -> dict:
    """Types an emailed code into the boxes the page asks it in, and presses nothing.

    The person presses the site's button next to it. Raises when no box is found.
    """
    page = b.tab(job_id)
    with _guarded(page, job_id):
        frame, fields = _extract(page)
        known = {f["hid"] for f in fields}
        boxes = [h for h in boxes if h in known] or code_boxes(fields, _survey(frame)["text"])
        if not boxes:
            raise RuntimeError("no box found for the code; type it in the browser window")
        code = one_line(code).strip()
        if len(boxes) == 1:
            frame.locator(f"[data-hal-id='{boxes[0]}']").fill(code)
        else:
            # One box a character, as some sites lay the code out.
            for hid, char in zip(boxes, code):
                frame.locator(f"[data-hal-id='{hid}']").fill(char)
    return {"typed": len(boxes)}


def _read_here(b: Browser, job_id: str) -> dict:
    """Reads the form on whatever page the job's tab shows now."""
    page = b.tab(job_id)
    with _guarded(page, job_id):
        _dismiss_banners(page, notes_for(page.url, job_id))
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
            except Exception:  # no results, or the engine's bot check
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
    from .core import settings

    mode = os.environ.get("JOBHUNT_APPLY_BROWSER") or settings.get("applying.mode")
    return "extension" if mode == "helper" else mode   # the engines still call the helper "extension"


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
    """Opens the application form for a job."""
    return _via(_open_form, job_id, url)


def fill(job_id: str, fields: list[dict], shot: Path) -> dict:
    """Enters the approved values, after any `field_transform` note about the page.

    The result's `notes_used` lists the trace line of each note used, for the Form step.
    """
    return _via(_fill, job_id, fields, str(shot))


def read_here(job_id: str) -> dict:
    """Reads the form on the page the browser is on."""
    return _via(_read_here, job_id)


def resume(job_id: str, passed: str, wall: str) -> dict:
    """Continues after the person passed a sign-in or other wall by hand."""
    return _via(_resume, job_id, passed, wall)


def type_code(job_id: str, code: str, boxes: list[str]) -> dict:
    """Types an emailed code into its boxes; the person presses the site's button."""
    return _via(_type_code, job_id, code, boxes)


def press_control(job_id: str, name: str) -> dict:
    """Presses a control by name; raises `guard.WouldSubmit` when it could send the application."""
    return _via(_press_control, job_id, name)


def peek(job_id: str) -> dict:
    """Reads the page without changing it."""
    return _via(_peek, job_id)


def next_step(job_id: str) -> dict:
    """Moves to the form's next step."""
    return _via(_next_step, job_id)


def search(queries: list[str], pause: float = 0) -> tuple[list[list[dict]], bool]:
    """Runs searches in the browser and returns the result pages and whether a site refused."""
    return _via(_search, queries, pause)


def show(job_id: str) -> bool:
    """Brings a job's tab or window to the front."""
    return _via(_show, job_id)


def mark(job_id: str, fields: list[dict], panel: dict) -> None:
    """Outlines the filled answers in the applicant's tab; Hal's own window shows them in the web interface."""
    if mode() == "extension":
        from . import extension
        extension.mark(job_id, fields, panel)


def close(job_id: str) -> None:
    """Closes a job's tab or window."""
    if mode() != "extension":
        BROWSER.call(_close, job_id)

"""Drives a real browser through job application forms with Playwright.

One thread owns the browser; other threads queue work with `BROWSER.call`.
A form's controls are tagged with data-hal-id attributes when read, so the
values approved in the web interface land in the controls they were proposed
for. Nothing is submitted except by `submit`, which the web interface calls
only on an explicit request. Requires the playwright package (see .venv).
"""
import collections
import concurrent.futures
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

# Reads every visible control into a field list, grouping radio and checkbox
# options under their question. Hidden helper inputs (react-select's required
# shadow input, captcha responses, phone-country search boxes) are skipped.
EXTRACT_JS = r"""
() => {
  const clean = t => (t || '').replace(/\s+/g, ' ').replace(/^\s*\*\s*|\s*\*\s*$/g, '').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
      && parseFloat(s.opacity || '1') > 0.05;
  };
  const textOf = ids => (ids || '').split(/\s+/).map(i => document.getElementById(i))
    .filter(Boolean).map(e => e.innerText).join(' ');
  const ownLabel = el => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(l => l.innerText).join(' '));
    if (el.getAttribute('aria-labelledby')) return clean(textOf(el.getAttribute('aria-labelledby')));
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    return '';
  };
  const LABELISH = 'label, legend, h3, h4, [class*="label" i], [class*="question" i], [class*="title" i]';
  const question = el => {
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg && clean(lg.innerText)) return clean(lg.innerText);
    }
    const grp = el.closest('[role=radiogroup], [role=group]');
    if (grp) {
      const t = clean(textOf(grp.getAttribute('aria-labelledby')) || grp.getAttribute('aria-label'));
      if (t) return t;
    }
    let node = el.parentElement;
    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
      for (const cand of node.querySelectorAll(':scope > ' + LABELISH.split(', ').join(', :scope > '))) {
        const optionLabel = cand.tagName === 'LABEL' && cand.control
          && ['radio', 'checkbox'].includes(cand.control.type);
        if (!cand.contains(el) && !optionLabel && !cand.querySelector('input') && clean(cand.innerText))
          return clean(cand.innerText);
      }
    }
    return '';
  };
  const required = el => el.required || el.getAttribute('aria-required') === 'true'
    || /\*\s*$/.test((el.labels && el.labels[0] ? el.labels[0].innerText : '') || '');

  const fields = [], groups = {};
  let n = 0;
  const controls = document.querySelectorAll('input, textarea, select');
  for (const el of controls) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(type)) continue;
    if (/recaptcha|captcha|honeypot/i.test(el.name + ' ' + el.id)) continue;
    // Styled radios and checkboxes hide the input and show its label instead.
    const shown = visible(el) || ((type === 'radio' || type === 'checkbox')
      && ((el.labels && el.labels[0] && visible(el.labels[0])) || (el.parentElement && visible(el.parentElement))));
    if (type !== 'file' && !shown) continue;
    if (el.closest('[aria-hidden=true]') && type !== 'file') continue;
    if (el.closest('[role=search]')) continue;
    const owner = el.form ? [...document.forms].indexOf(el.form) : -1;
    if (type === 'radio' || type === 'checkbox') {
      const key = el.name ? type + ':' + el.name : null;
      const q = question(el);
      const own = ownLabel(el);
      const opt = own || clean(el.value);
      if (key && document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
        let g = groups[key];
        if (!g) {
          g = groups[key] = { hid: 'h' + (n++), kind: type === 'radio' ? 'radio' : 'checkgroup',
                              label: q, required: false, options: [], current: [], form: owner };
          fields.push(g);
        }
        el.setAttribute('data-hal-id', g.hid + ':' + g.options.length);
        g.options.push(opt);
        g.required = g.required || required(el);
        if (el.checked) g.current.push(opt);
        continue;
      }
      const hid = 'h' + (n++);
      el.setAttribute('data-hal-id', hid);
      const label = (own && !/^(yes|on|i agree)$/i.test(own)) ? own : (q || opt);
      fields.push({ hid, kind: 'checkbox', label, context: q, required: required(el), current: el.checked,
                    form: owner });
      continue;
    }
    const hid = 'h' + (n++);
    el.setAttribute('data-hal-id', hid);
    let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : 'text';
    if (type === 'file') kind = 'file';
    if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list'
        || /select__input/.test(el.className)) kind = 'combobox';
    let label = ownLabel(el);
    // Upload widgets label the button ("Attach", "Choose file"); the question names the document.
    if (!label || (kind === 'file' && /^(attach|choose files?|upload( files?)?|browse|select files?)$/i.test(label)))
      label = question(el) || label;
    const f = { hid, kind, type, label, required: required(el), form: owner,
                name: el.name || el.id || '', current: kind === 'file' ? '' : el.value };
    if (kind === 'file') f.context = clean([el.id, el.name, question(el), el.accept || ''].join(' '));
    if (kind === 'select') f.options = [...el.options].map(o => clean(o.text))
      .filter(t => t && !/^(select|choose|please select)/i.test(t));
    if (el.maxLength > 0) f.maxlength = el.maxLength;
    fields.push(f);
  }
  return fields;
}
"""

APPLY_BUTTON = re.compile(r"^\s*(easy )?(apply|apply now|apply for (this|the) (job|role|position|vacancy)|"
                          r"apply (on|at) (the )?(company|employer)('s)? (web)?site|"
                          r"(start|begin|continue) (your |my )?application|i'?m interested)[\s\u00bb\u203a>]*$", re.I)
NEXT_BUTTON = re.compile(r"^\s*(next|next step|continue|proceed|save (and|&) (continue|next))\s*$", re.I)
SUBMIT_BUTTON = re.compile(r"^\s*(submit( application| my application)?|send application|apply|"
                           r"complete application)\s*$", re.I)
SUCCESS = re.compile(r"thank(s| you)[^.]{0,40}(appl|submi|interest)|application (has been )?(received|submitted)|"
                     r"we('ve| have) received|successfully submitted", re.I)
APPLIED = re.compile(r"you(('ve| have) already| have|'ve)? applied (for|to) this (job|role|position|vacancy)", re.I)
SIGN_IN = re.compile(r"\b(sign|log)[ -]?in\b|\blogin\b|\bregister\b|create (an |your )?account", re.I)
SIGN_OUT = re.compile(r"^\s*(sign|log) ?out\s*$", re.I)
CLOSED = re.compile(r"(no longer|not) (available|accepting applications|open)|not available anymore|"
                    r"(job|position|vacancy|role) (has )?(been filled|expired|closed)", re.I)
BOT_CHECK = re.compile(r"just a moment|verify (that )?you are (a )?human|human verification|are you a robot|"
                       r"attention required", re.I)
# Application forms that employers embed in a frame on their own careers page.
ATS_FRAME = ("greenhouse.io", "ashbyhq.com", "lever.co", "workable.com", "smartrecruiters.com", "recruitee.com",
             "personio.", "teamtailor.com", "bamboohr.com", "jobvite.com", "icims.com", "pinpointhq.com")
MAX_PRESSES = 4  # Apply controls followed before giving up on finding the form
SEARCH = "https://duckduckgo.com/?q="
RESULTS_JS = r"""
() => [...document.querySelectorAll("article[data-testid='result']")].map(a => {
  const title = a.querySelector("a[data-testid='result-title-a']"), snippet = a.querySelector("[data-result='snippet']");
  return { url: title ? title.href : '', title: title ? title.innerText.trim() : '',
           snippet: snippet ? snippet.innerText.replace(/\s+/g, ' ').trim() : '' };
}).filter(r => r.url)
"""


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
                    fut.set_exception(e)

    def context(self):
        """Returns the browser context, launching it (again, if the window was closed)."""
        if self._ctx is not None:
            try:
                self._ctx.pages  # noqa: B018 - raises once the browser has gone
                return self._ctx
            except Exception:  # noqa: BLE001
                self._ctx = None
        PROFILE.mkdir(parents=True, exist_ok=True)
        mode = os.environ.get("JOBHUNT_APPLY_BROWSER") or CONFIG.get("apply_browser", "window")
        headless = mode == "headless"
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

    def follow(self, job_id: str, page) -> None:
        """Makes a tab the job's current one, along with any tab it opens later."""
        tabs = self.pages.setdefault(job_id, [])
        if page not in tabs:
            page.on("popup", lambda popup: self.follow(job_id, popup))
        else:
            tabs.remove(page)
        tabs.append(page)


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
    re.compile(r"^\s*(reject all|reject( all)? (optional|non-essential)( cookies)?|decline( all)?|"
               r"(use |allow )?(only )?(strictly )?(necessary|essential)( cookies)?( only)?|deny)\s*$", re.I),
    re.compile(r"^\s*(accept( all)?( cookies)?|allow all( cookies)?|i agree|agree|got it|ok)\s*$", re.I),
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


def _needs(page, fields: list[dict]) -> str:
    """Names what only the applicant can do on this page: "sign-in", "check", or "" for nothing."""
    if _is_form(fields):
        return ""
    if BOT_CHECK.search(page.title()):
        return "check"
    if page.locator("input[type=password]:visible").count():
        return "sign-in"
    # Sign-in pages that ask for the email address first show no password box yet.
    if 0 < len(fields) <= 2 and _apply_control(page) is None \
            and SIGN_IN.search(f"{page.title()} {urlparse(page.url).path}"):
        return "sign-in"
    return ""


def _signed_in(page) -> bool:
    """True when the applicant has an account session on the page's site."""
    return _site(page.url) in _signed_in_sites() \
        or page.locator("a, button").filter(has_text=SIGN_OUT).filter(visible=True).count() > 0


def _read(page) -> list[dict]:
    try:
        return page.evaluate(EXTRACT_JS)
    except Exception:  # noqa: BLE001 - the page moved to another address mid-read
        page.wait_for_timeout(1_500)
        _settle(page)
        return page.evaluate(EXTRACT_JS)


def _apply_control(page):
    """Returns the first visible Apply link or button on the page, or None."""
    found = page.get_by_role("link", name=APPLY_BUTTON).or_(page.get_by_role("button", name=APPLY_BUTTON))
    found = found.filter(visible=True)
    return found.first if found.count() else None


def _report(page, fields: list[dict], needs: str, pressed: bool) -> dict:
    form = _is_form(fields)
    if needs:
        # A sign-in page's inputs are not questions to answer; the applicant signs in by hand.
        fields = []
    for f in fields:
        if f["kind"] == "combobox":
            f["options"] = _combobox_options(page, f["hid"])
    text = "" if form else page.locator("body").inner_text()
    sent = pressed and SUCCESS.search(text) is not None
    headings = " ".join([page.title(), *page.locator("h1, h2").all_inner_texts()[:4]])
    return {"url": page.url, "title": page.title(), "fields": fields, "needs": needs, "sent": sent,
            "site": _site(page.url), "closed": not form and CLOSED.search(headings) is not None,
            "applied": APPLIED.search(text) is not None,
            "captcha": page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0}


def _advance(b: Browser, job_id: str, pressed: bool = False) -> dict:
    """Presses through advert pages towards the application form and reports where it stopped.

    Stops at the form, at a page only the applicant can pass (`needs`), or when
    no Apply control is left. `pressed` says Apply was just pressed on a site
    the applicant is signed in to, where a confirmation on the page means the
    application went out.
    """
    page = b.page(job_id)
    if page is None:
        raise RuntimeError("the form's tab was closed; ask Hal to open the form again")
    for presses in range(MAX_PRESSES + 1):
        _settle(page)
        _dismiss_banners(page)
        fields = _read(page)
        needs = _needs(page, fields)
        if needs or _is_form(fields) or presses == MAX_PRESSES:
            break
        frame = page.locator(", ".join(f"iframe[src*='{host}']" for host in ATS_FRAME))
        if frame.count():
            # A form inside a frame cannot be read or filled from the page around it.
            page.goto(frame.first.evaluate("e => e.src"), wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            continue
        control = _apply_control(page)
        if control is None:
            break
        if _signed_in(page):
            # Signed in, Apply may send the stored CV at once; only a site known to ask first is pressed.
            if _site(page.url) not in CONFIG.get("signed_in_apply", []):
                needs = "apply"
                break
            pressed = True
        try:
            control.click(timeout=5_000)
        except Exception:  # noqa: BLE001 - usually a newsletter pop-up lying over the control
            link = control.get_attribute("href")
            if not link:
                break
            page.goto(urljoin(page.url, link), wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(1_500)
        page = b.page(job_id) or page  # the press may have opened the form in a new tab
    return _report(page, fields, needs, pressed)


def _open(b: Browser, job_id: str, url: str) -> dict:
    """Opens the job's application page and works through to its form."""
    page = b.page(job_id) or b.context().new_page()
    b.follow(job_id, page)
    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    return _advance(b, job_id)


def _resume(b: Browser, job_id: str, passed: str, wall: str) -> dict:
    """Carries on towards the form once the applicant has done what the page at `wall` needed."""
    if passed == "sign-in":
        SIGNED_IN.write_text(json.dumps(sorted(_signed_in_sites() | {_site(wall)})))
    return _advance(b, job_id, pressed=passed == "apply")


def _peek(b: Browser, job_id: str) -> dict:
    """Reports the state of the job's tab without pressing anything."""
    page = b.page(job_id)
    if page is None:
        return {"gone": True}
    try:
        fields = page.evaluate(EXTRACT_JS)
        return {"needs": _needs(page, fields), "form": _is_form(fields), "url": page.url,
                "sent": SUCCESS.search(page.locator("body").inner_text()) is not None}
    except Exception:  # noqa: BLE001 - the page is between two addresses
        return {"busy": True}


def _step_button(page, pattern):
    """Returns the visible button of a multi-page form matching the pattern, or None."""
    found = page.get_by_role("button", name=pattern).filter(visible=True)
    return found.last if found.count() else None


def _next(b: Browser, job_id: str) -> dict:
    """Presses the form's Next button and reads the step that follows."""
    page = b.page(job_id)
    if page is None:
        raise RuntimeError("the form's tab was closed; ask Hal to open the form again")
    button = _step_button(page, NEXT_BUTTON)
    if button is None:
        raise RuntimeError("no Next button found; move on in the browser window, then read the page there")
    # Marks this step's controls so a form that refuses to move on can be told from a new step.
    page.evaluate("() => document.querySelectorAll('[data-hal-id]').forEach(e => e.setAttribute('data-hal-step', ''))")
    button.click()
    page.wait_for_timeout(1_000)
    _settle(page)
    page = b.page(job_id) or page
    stayed = page.locator("[data-hal-step]:visible").count() > 0
    if stayed:
        return {"stayed": True, "invalid": page.locator("[aria-invalid=true]:visible").count()}
    fields = _read(page)
    return {"stayed": False, **_report(page, fields, _needs(page, fields), False)}


def _combobox_options(page, hid: str) -> list[str]:
    """Opens a combobox, reads its options, and closes it again."""
    box = page.locator(f"[data-hal-id='{hid}']")
    try:
        box.click(timeout=3_000)
        page.wait_for_timeout(300)
        options = [t.strip() for t in page.locator("[role=option]:visible").all_inner_texts() if t.strip()]
        box.press("Escape")
        return options[:400]
    except Exception:  # noqa: BLE001 - an option list that will not open is filled by typing
        return []


def _fill_one(page, f: dict, value) -> None:
    hid, kind = f["hid"], f["kind"]
    if kind in ("radio", "checkgroup"):
        wanted = value if isinstance(value, list) else [value]
        for i, opt in enumerate(f["options"]):
            el = page.locator(f"[data-hal-id='{hid}:{i}']")
            if kind == "radio" and opt in wanted:
                el.check(force=True)
            elif kind == "checkgroup":
                el.set_checked(opt in wanted, force=True)
        return
    el = page.locator(f"[data-hal-id='{hid}']")
    if kind == "checkbox":
        el.set_checked(bool(value), force=True)
    elif kind == "file":
        el.set_input_files(value)
    elif kind == "select":
        el.select_option(label=value)
    elif kind == "combobox":
        el.click()
        el.fill(str(value))
        page.wait_for_timeout(400)
        option = page.locator("[role=option]:visible").filter(has_text=re.compile(rf"^\s*{re.escape(str(value))}\s*$"))
        if option.count() == 0:
            option = page.locator("[role=option]:visible")
        option.first.click(timeout=4_000)
    else:
        el.fill(str(value))


def _fill(b: Browser, job_id: str, fields: list[dict], shot: str) -> dict:
    """Enters the approved values and returns per-field errors and a screenshot path."""
    page = b.page(job_id)
    if page is None:
        raise RuntimeError("the form's tab was closed; ask Hal to open the form again")
    errors = {}
    for f in fields:
        value = f.get("value")
        if value in (None, "", []) or f.get("skip"):
            continue
        try:
            _fill_one(page, f, value)
        except Exception as e:  # noqa: BLE001 - report the field and carry on with the rest
            errors[f["hid"]] = str(e).splitlines()[0][:200]
    Path(shot).parent.mkdir(parents=True, exist_ok=True)
    _shoot_form(page, shot)
    return {"errors": errors, "screenshot": shot, "next": _step_button(page, NEXT_BUTTON) is not None
            and _step_button(page, SUBMIT_BUTTON) is None}


def _shoot_form(page, shot: str) -> None:
    """Screenshots the form holding the tagged fields, or the whole page if there is no form element."""
    form = page.locator("form").filter(has=page.locator("[data-hal-id]"))
    try:
        if form.count():
            form.first.screenshot(path=shot)
            return
    except Exception:  # noqa: BLE001 - a form taller than the browser allows falls back to the page
        pass
    page.screenshot(path=shot, full_page=True)


def _submit(b: Browser, job_id: str, shot: str) -> dict:
    """Presses the form's submit button and reports what the page says afterwards."""
    page = b.page(job_id)
    if page is None:
        raise RuntimeError("the form's tab was closed; ask Hal to open the form again")
    button = page.get_by_role("button", name=SUBMIT_BUTTON).or_(page.locator("button[type=submit], input[type=submit]"))
    if button.count() == 0:
        raise RuntimeError("no submit button found; submit it in the browser window")
    button.last.click()
    try:
        page.wait_for_function(
            "() => /thank|received|submitted|success/i.test(document.body.innerText)", timeout=20_000)
    except Exception:  # noqa: BLE001 - judged from the page text below
        pass
    _settle(page)
    page.screenshot(path=shot, full_page=True)
    text = page.locator("body").inner_text()
    invalid = page.locator("[aria-invalid=true]:visible").count()
    challenge = page.locator("iframe[src*='recaptcha/api2/bframe'], iframe[src*='hcaptcha']:visible").count()
    confirmed = SUCCESS.search(text)
    return {"submitted": bool(confirmed), "invalid": invalid, "challenge": challenge > 0,
            "screenshot": shot, "url": page.url, "excerpt": confirmed.group(0) if confirmed else ""}


def _read_here(b: Browser, job_id: str) -> dict:
    """Reads the form on whatever page the job's tab shows now."""
    page = b.page(job_id)
    if page is None:
        raise RuntimeError("no browser tab is open for this job; open the form first")
    _dismiss_banners(page)
    return _report(page, _read(page), "", False)


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
    page = b.page(job_id)
    if page is None:
        raise RuntimeError("no browser tab is open for this job; open the form first")
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


def open_form(job_id: str, url: str) -> dict:
    return BROWSER.call(_open, job_id, url)


def fill(job_id: str, fields: list[dict], shot: Path) -> dict:
    return BROWSER.call(_fill, job_id, fields, str(shot))


def submit(job_id: str, shot: Path) -> dict:
    return BROWSER.call(_submit, job_id, str(shot))


def read_here(job_id: str) -> dict:
    return BROWSER.call(_read_here, job_id)


def resume(job_id: str, passed: str, wall: str) -> dict:
    return BROWSER.call(_resume, job_id, passed, wall)


def peek(job_id: str) -> dict:
    return BROWSER.call(_peek, job_id)


def next_step(job_id: str) -> dict:
    return BROWSER.call(_next, job_id)


def search(queries: list[str], pause: float = 0) -> tuple[list[list[dict]], bool]:
    return BROWSER.call(_search, queries, pause)


def look(job_id: str, shot: Path) -> dict:
    return BROWSER.call(_look, job_id, str(shot))


def show(job_id: str) -> bool:
    return BROWSER.call(_show, job_id)

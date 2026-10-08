"""Drives a real browser through job application forms with Playwright.

One thread owns the browser; other threads queue work with `BROWSER.call`.
A form's controls are tagged with data-hal-id attributes when read, so the
values approved in the web interface land in the controls they were proposed
for. Nothing is submitted except by `submit`, which the web interface calls
only on an explicit request. Requires the playwright package (see .venv).
"""
import concurrent.futures
import os
import queue
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from .config import CONFIG, ROOT

PROFILE = ROOT / "data" / "browser-profile"
SHOTS = ROOT / "data" / "apply"
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
    if (type === 'radio' || type === 'checkbox') {
      const key = el.name ? type + ':' + el.name : null;
      const q = question(el);
      const own = ownLabel(el);
      const opt = own || clean(el.value);
      if (key && document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
        let g = groups[key];
        if (!g) {
          g = groups[key] = { hid: 'h' + (n++), kind: type === 'radio' ? 'radio' : 'checkgroup',
                              label: q, required: false, options: [], current: [] };
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
      fields.push({ hid, kind: 'checkbox', label, context: q, required: required(el), current: el.checked });
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
    const f = { hid, kind, type, label, required: required(el),
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

APPLY_BUTTON = re.compile(r"^\s*(apply|apply now|apply for (this|the) (job|role|position)|"
                          r"start (your )?application|i'?m interested)\s*$", re.I)
SUBMIT_BUTTON = re.compile(r"^\s*(submit( application| my application)?|send application|apply|"
                           r"complete application)\s*$", re.I)
SUCCESS = re.compile(r"thank(s| you)[^.]{0,40}(appl|submi|interest)|application (has been )?(received|submitted)|"
                     r"we('ve| have) received|successfully submitted", re.I)


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
        self.pages: dict[str, object] = {}

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
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE), headless=headless, viewport={"width": 1280, "height": 900},
            locale="en-GB", timezone_id="Europe/London")
        self._ctx.on("close", lambda _: setattr(self, "_ctx", None))
        self.pages = {}
        return self._ctx

    def page(self, job_id: str):
        """Returns the open tab for a job's application, or None."""
        p = self.pages.get(job_id)
        if p is not None and not p.is_closed():
            return p
        self.pages.pop(job_id, None)
        return None


BROWSER = Browser()


def _is_form(fields: list[dict]) -> bool:
    """True when the fields look like an application form rather than a search box or banner."""
    labelled = [f for f in fields if f.get("label")]
    return len(labelled) >= 3 or any(f["kind"] == "file" for f in fields)


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
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 - some pages poll forever; the DOM is ready regardless
        pass


def _open(b: Browser, job_id: str, url: str) -> dict:
    """Opens the application form and returns its fields."""
    page = b.page(job_id) or b.context().new_page()
    b.pages[job_id] = page
    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    _settle(page)
    _dismiss_banners(page)
    # Job pages often hold only the advert; follow up to two "Apply" buttons to the form.
    fields = page.evaluate(EXTRACT_JS)
    for _ in range(2):
        if _is_form(fields):
            break
        button = page.get_by_role("link", name=APPLY_BUTTON).or_(page.get_by_role("button", name=APPLY_BUTTON))
        if button.count() == 0:
            break
        try:
            with page.context.expect_page(timeout=5_000) as popup:
                button.first.click()
            page = popup.value
            b.pages[job_id] = page
        except Exception:  # noqa: BLE001 - the button navigated the same tab or opened a dialog
            pass
        _settle(page)
        _dismiss_banners(page)
        fields = page.evaluate(EXTRACT_JS)
    for f in fields:
        if f["kind"] == "combobox":
            f["options"] = _combobox_options(page, f["hid"])
    return {"url": page.url, "title": page.title(), "fields": fields,
            "captcha": page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0}


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
    return {"errors": errors, "screenshot": shot}


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
    page = page.context.pages[-1] if page.context.pages else page
    b.pages[job_id] = page
    _dismiss_banners(page)
    fields = page.evaluate(EXTRACT_JS)
    for f in fields:
        if f["kind"] == "combobox":
            f["options"] = _combobox_options(page, f["hid"])
    return {"url": page.url, "title": page.title(), "fields": fields,
            "captcha": page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0}


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


def show(job_id: str) -> bool:
    return BROWSER.call(_show, job_id)

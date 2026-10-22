"""Drives job application forms in the applicant's own browser through the Hal extension.

The extension (extension/) asks the web helper for work and carries it out in
tabs it opened for a job. This module holds the queue it reads from, and the
steps of browser.py expressed as commands to it. A browser that nobody is
remote-controlling keeps the applicant's sign-ins and passes sites' human
checks, which Hal's own browser cannot; the applicant presses Submit.

Hal never presses Submit. Every command carries the guard's
patterns, so content.js refuses the same controls `apply.guard` refuses, and
the helper keeps a block in the page's own world for the whole of each command
that cancels any submit the page or a press would make. There is no submit
command, and no command presses Enter.
"""
import concurrent.futures
import json
import queue
import time
import uuid
from urllib.parse import urlparse

from . import browser
from .apply import guard
from .browser import (
    APPLIED,
    APPLY_BUTTON,
    ATS_FRAME,
    BLOCKED,
    BOT_CHECK,
    CAPTCHA,
    CLOSED,
    COOKIE_BUTTONS,
    MAX_PRESSES,
    NEXT_BUTTON,
    SIGN_IN,
    SIGN_OUT,
    SUBMIT_BUTTON,
    SUCCESS,
    _is_form,
    _signed_in_sites,
    _site,
    code_boxes,
)
from .config import CONFIG

POLL_WAIT = 20  # seconds a poll is held open; the browser stops a worker whose request takes 30
PATTERNS = {"apply": APPLY_BUTTON.pattern, "next": NEXT_BUTTON.pattern, "submit": SUBMIT_BUTTON.pattern,
            "signOut": SIGN_OUT.pattern, "frames": list(ATS_FRAME), "captcha": CAPTCHA}
FILL_WAIT = 180  # seconds a fill may take: uploads and searched lists are slow

_commands: queue.Queue = queue.Queue()
_waiting: dict[str, concurrent.futures.Future] = {}
_seen = 0.0


def poll() -> dict:
    """Hands the extension its next command, waiting a while for one to arrive."""
    global _seen
    _seen = time.time()
    try:
        return _commands.get(timeout=POLL_WAIT)
    except queue.Empty:
        return {}
    finally:
        _seen = time.time()


def deliver(result: dict) -> None:
    """Takes the extension's answer to a command."""
    global _seen
    _seen = time.time()
    waiting = _waiting.pop(result.get("id"), None)
    if waiting:
        waiting.set_result(result)


def connected() -> bool:
    """True when the extension has been in touch recently or is working on a command."""
    return time.time() - _seen < 2 * POLL_WAIT or bool(_waiting)


def _ask(cmd: str, timeout: float = 90, **args) -> dict:
    if not connected():
        raise RuntimeError("the Hal extension is not connected; open Brave with the extension switched on")
    ident = uuid.uuid4().hex
    _waiting[ident] = concurrent.futures.Future()
    # Every command carries the guard's words, so content.js refuses what the guard refuses.
    _commands.put({"id": ident, "cmd": cmd, "args": {**args, "guard": guard.PATTERNS}})
    try:
        result = _waiting[ident].result(timeout)
    except concurrent.futures.TimeoutError:
        _waiting.pop(ident, None)
        raise RuntimeError("the Hal extension did not answer; check Brave is still open") from None
    if result["ok"]:
        value = result["value"]
        # Submits the page tried while the helper acted on it, cancelled by the helper's block.
        for what in dict.fromkeys((value or {}).get("blocked", []) if isinstance(value, dict) else []):
            guard.record(args.get("job"), (value or {}).get("url") or "", what)
        return value
    # A tab closed between finding it and acting in it is reported by the browser in its own words.
    if result["error"] == "gone" or result["error"].startswith("No tab with id"):
        raise RuntimeError("the form's tab was closed; press \"Fill in the form\" to open it again")
    raise RuntimeError(result["error"])


def _read(job_id: str, banners: bool = True, options: bool = False) -> dict:
    """Returns what the job's tab shows: its fields and the facts the steps below decide on."""
    facts = _ask("read", job=job_id, patterns={
        **PATTERNS, "banners": [p.pattern for p in COOKIE_BUTTONS] if banners else None, "options": options})
    if not facts:
        # The page was swapped out while it was read, or is one the browser will not let a script into.
        raise RuntimeError("the job's tab could not be read; it may still be loading or be a browser error page")
    return facts


def _needs(facts: dict) -> str:
    """Names what only the applicant can do on this page: "sign-in", "check", or "" for nothing."""
    fields = facts["fields"]
    if _is_form(fields):
        return ""
    if BOT_CHECK.search(facts["title"]):
        return "check"
    if facts["password"]:
        # A registration form that also asks the usual questions is filled in, apart from its password.
        return "" if _is_form(fields) and len(fields) > 5 else "sign-in"
    # Sign-in pages that ask for the email address first show no password box yet.
    if 0 < len(fields) <= 2 and not facts["apply"] \
            and SIGN_IN.search(f"{facts['title']} {urlparse(facts['url']).path}"):
        return "sign-in"
    return ""


def _report(facts: dict, needs: str, pressed: bool) -> dict:
    form = _is_form(facts["fields"])
    text = "" if form else facts["text"]
    return {"url": facts["url"], "title": facts["title"], "fields": [] if needs else facts["fields"],
            "needs": needs, "sent": pressed and SUCCESS.search(text) is not None, "site": _site(facts["url"]),
            "closed": not form and CLOSED.search(f"{facts['title']} {facts['headings']}") is not None,
            "blocked": not form and not needs and BLOCKED.search(f"{facts['title']} {facts['text'][:300]}") is not None,
            "applied": APPLIED.search(text) is not None, "captcha": facts["captcha"],
            "alerts": facts.get("alerts", []), "next": form and facts["next"] and not facts["submit"],
            "submit": form and facts["submit"], "password": form and facts["password"],
            "controls": [] if form or needs else [c for c in facts.get("controls", []) if not c["inNav"]][:60]}


def _press(job_id: str, facts: dict, kind: str, **how) -> bool:
    """Asks the helper to press a control; True when it did. Raises `guard.WouldSubmit` when it refused one."""
    page = {"form": _is_form(facts["fields"]), "final": facts.get("final", True), "signed_in": facts.get("signOut")}
    out = _ask("press", job=job_id, kind=kind, page=page, **how)
    if out.get("refused"):
        raise guard.refuse(job_id, facts["url"], out["refused"])
    return bool(out["pressed"])


def _final(facts: dict) -> bool:
    """True when the page is judged the form's last: no Next, or a Submit-like button beside it."""
    return not facts["next"] or bool(facts["submit"])


def _advance(job_id: str, pressed: bool = False) -> dict:
    """Presses through advert pages towards the application form and reports where it stopped.

    On a site the applicant is signed in to, Apply may send the saved CV at once, so the helper never
    presses it there: it stops with `needs = "apply"` for the person to press it. `pressed` says the
    person has just pressed it.
    """
    for presses in range(MAX_PRESSES + 1):
        facts = _read(job_id)
        needs = _needs(facts)
        if needs or _is_form(facts["fields"]) or presses == MAX_PRESSES:
            break
        if facts["frame"]:
            # A form inside a frame cannot be read or filled from the page around it.
            _ask("goto", job=job_id, url=facts["frame"])
            continue
        if not facts["apply"]:
            break
        if facts["signOut"] or _site(facts["url"]) in _signed_in_sites():
            needs = "apply"
            break
        if not _press(job_id, {**facts, "final": False}, "apply", pattern=APPLY_BUTTON.pattern):
            break
    if not needs and any(f["kind"] == "combobox" for f in facts["fields"]):
        facts = _read(job_id, banners=False, options=True)
    return _report(facts, needs, pressed)


def open_form(job_id: str, url: str) -> dict:
    _ask("open", timeout=120, job=job_id, url=url, window=CONFIG.get("apply_window", "separate"))
    return _advance(job_id)


def press_control(job_id: str, name: str) -> dict:
    """Presses the control with this name; raises `guard.WouldSubmit` when it could send the application."""
    facts = _read(job_id, banners=False)
    control = next((c for c in facts.get("controls", []) if c["name"] == name), {"name": name})
    form = _is_form(facts["fields"])
    guard.check(job_id, facts["url"], control, {"form": form, "final": form and _final(facts),
                                                "signed_in": facts["signOut"]}, "named")
    if not _press(job_id, {**facts, "final": form and _final(facts)}, "named", name=name):
        raise RuntimeError(f"no control named {name!r} on the page")
    return _advance(job_id)


def type_code(job_id: str, code: str, boxes: list[str]) -> dict:
    """Types an emailed code into its boxes in the job's tab; the person presses the site's button."""
    out = _ask("type_code", job=job_id, code=code, boxes=boxes)
    if not out.get("typed"):
        raise RuntimeError("no box found for the code; type it in the job's tab")
    return out


def resume(job_id: str, passed: str, wall: str) -> dict:
    if passed == "sign-in":
        # Read when used, so a test or the data move points it elsewhere.
        signed_in = browser._live("SIGNED_IN")
        signed_in.write_text(json.dumps(sorted(_signed_in_sites() | {_site(wall)})))
    return _advance(job_id, pressed=passed == "apply")


def peek(job_id: str) -> dict:
    try:
        facts = _read(job_id, banners=False)
    except RuntimeError as e:
        return {"gone": True} if "tab was closed" in str(e) else {"busy": True}
    return {"needs": _needs(facts), "form": _is_form(facts["fields"]), "url": facts["url"],
            "sent": SUCCESS.search(facts["text"]) is not None, "applied": APPLIED.search(facts["text"]) is not None,
            "challenge": facts["unchecked"], "password": False,
            "values": {f["sig"]: f.get("current") for f in facts["fields"]},
            "code": code_boxes(facts["fields"], facts["text"]),
            "code_sigs": [f["sig"] for f in facts["fields"] if f["hid"] in code_boxes(facts["fields"], facts["text"])]}


def read_here(job_id: str) -> dict:
    return _report(_read(job_id, options=True), "", False)


def fill(job_id: str, fields: list[dict], shot) -> dict:
    """Enters the approved values; there is no picture, since the form is in the applicant's own tab."""
    wanted = [{k: f.get(k) for k in ("hid", "kind", "value", "options")} for f in fields
              if f.get("value") not in (None, "", []) and not f.get("skip")]
    paths = [f["value"] for f in wanted if f["kind"] == "file"]
    errors = _ask("fill", timeout=FILL_WAIT, job=job_id, fields=wanted, paths=paths)["errors"]
    facts = _read(job_id, banners=False, options=True)
    known = {f.get("sig") for f in fields}
    return {"errors": errors, "screenshot": None, "next": facts["next"] and not facts["submit"],
            "submit": facts["submit"], "password": facts["password"],
            "new": [f for f in facts["fields"] if f.get("sig") not in known]}


def next_step(job_id: str) -> dict:
    """Presses the form's Next button, never any other; raises `guard.WouldSubmit` on a final page."""
    facts = _read(job_id, banners=False)
    if not _press(job_id, {**facts, "final": _final(facts)}, "next", pattern=NEXT_BUTTON.pattern):
        raise RuntimeError("no Next button found; move on in the job's tab, then read the page there")
    facts = _read(job_id, options=True)
    if facts["stayed"]:
        problems = [{"hid": f["hid"], "label": f["label"], "message": f["invalid"]} for f in facts["fields"] if f.get("invalid")]
        problems += [{"hid": None, "label": "", "message": a} for a in facts.get("alerts", [])]
        return {"stayed": True, "sent": False, "problems": problems, "challenge": facts["unchecked"],
                "url": facts["url"], "needs": ""}
    return {"stayed": False, "problems": [], "challenge": False, **_report(facts, _needs(facts), False)}


def mark(job_id: str, fields: list[dict], panel: dict) -> None:
    _ask("mark", job=job_id, fields=fields, panel=panel)


def show(job_id: str) -> bool:
    return _ask("show", job=job_id)["shown"]


def look(job_id: str, shot) -> dict:
    facts = _read(job_id, banners=False)
    return {"url": facts["url"], "title": facts["title"], "screenshot": None, "text": facts["text"][:1500],
            "apply": facts["apply"], "next": facts["next"], "submit": facts["submit"], "unchecked": facts["unchecked"]}


def search(queries: list[str], pause: float = 0) -> tuple[list[list[dict]], bool]:
    out = _ask("search", timeout=60 + (pause + 20) * len(queries), queries=queries, pause=pause)
    return out["pages"], out["refused"]

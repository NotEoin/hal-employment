"""Drives job application forms in the applicant's own browser through the Hal extension.

The extension (extension/) asks the web helper for work and carries it out in
tabs it opened for a job. This module holds the queue it reads from, and the
steps of browser.py expressed as commands to it. A browser that nobody is
remote-controlling keeps the applicant's sign-ins and passes sites' human
checks, which Hal's own browser cannot; the applicant presses Submit.
"""
import concurrent.futures
import json
import queue
import time
import uuid
from urllib.parse import urlparse

from .browser import (APPLIED, APPLY_BUTTON, ATS_FRAME, BLOCKED, BOT_CHECK, CAPTCHA, CLOSED, COOKIE_BUTTONS,
                      MAX_PRESSES, NEXT_BUTTON, SIGN_IN, SIGN_OUT, SIGNED_IN, SUBMIT_BUTTON, SUCCESS, _is_form,
                      _signed_in_sites, _site)
from .config import CONFIG

POLL_WAIT = 20  # seconds a poll is held open; the browser stops a worker whose request takes 30
PATTERNS = {"apply": APPLY_BUTTON.pattern, "next": NEXT_BUTTON.pattern, "submit": SUBMIT_BUTTON.pattern,
            "signOut": SIGN_OUT.pattern, "frames": list(ATS_FRAME), "captcha": CAPTCHA}

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
    _commands.put({"id": ident, "cmd": cmd, "args": args})
    try:
        result = _waiting[ident].result(timeout)
    except concurrent.futures.TimeoutError:
        _waiting.pop(ident, None)
        raise RuntimeError("the Hal extension did not answer; check Brave is still open") from None
    if result["ok"]:
        return result["value"]
    # A tab closed between finding it and acting in it is reported by the browser in its own words.
    if result["error"] == "gone" or result["error"].startswith("No tab with id"):
        raise RuntimeError("the form's tab was closed; press \"Fill in the form\" to open it again")
    raise RuntimeError(result["error"])


def _read(job_id: str, banners: bool = True, options: bool = False) -> dict:
    """Returns what the job's tab shows: its fields and the facts the steps below decide on."""
    return _ask("read", job=job_id, patterns={
        **PATTERNS, "banners": [p.pattern for p in COOKIE_BUTTONS] if banners else None, "options": options})


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


def _advance(job_id: str, pressed: bool = False) -> dict:
    """Presses through advert pages towards the application form and reports where it stopped."""
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
            # Signed in, Apply may send the stored CV at once; only a site known to ask first is pressed.
            if _site(facts["url"]) not in CONFIG.get("signed_in_apply", []):
                needs = "apply"
                break
            pressed = True
        if not _ask("press", job=job_id, kind="apply", pattern=APPLY_BUTTON.pattern)["pressed"]:
            break
    if not needs and any(f["kind"] == "combobox" for f in facts["fields"]):
        facts = _read(job_id, banners=False, options=True)
    return _report(facts, needs, pressed)


def open_form(job_id: str, url: str) -> dict:
    _ask("open", timeout=120, job=job_id, url=url, window=CONFIG.get("apply_window", "separate"))
    return _advance(job_id)


def press_control(job_id: str, name: str) -> dict:
    if not _ask("press", job=job_id, kind="named", name=name)["pressed"]:
        raise RuntimeError(f"no control named {name!r} on the page")
    return _advance(job_id)


def enter_code(job_id: str, code: str, shot) -> dict:
    raise RuntimeError("type the security code in the job's tab, then press Submit there")


def resume(job_id: str, passed: str, wall: str) -> dict:
    if passed == "sign-in":
        SIGNED_IN.write_text(json.dumps(sorted(_signed_in_sites() | {_site(wall)})))
    return _advance(job_id, pressed=passed == "apply")


def peek(job_id: str) -> dict:
    try:
        facts = _read(job_id, banners=False)
    except RuntimeError as e:
        return {"gone": True} if "tab was closed" in str(e) else {"busy": True}
    return {"needs": _needs(facts), "form": _is_form(facts["fields"]), "url": facts["url"],
            "sent": SUCCESS.search(facts["text"]) is not None, "applied": APPLIED.search(facts["text"]) is not None,
            "challenge": facts["unchecked"], "password": False,
            "values": {f["sig"]: f.get("current") for f in facts["fields"]}}


def read_here(job_id: str) -> dict:
    return _report(_read(job_id, options=True), "", False)


def fill(job_id: str, fields: list[dict], shot) -> dict:
    """Enters the approved values; there is no picture, since the form is in the applicant's own tab."""
    wanted = [{k: f.get(k) for k in ("hid", "kind", "value", "options")} for f in fields
              if f.get("value") not in (None, "", []) and not f.get("skip")]
    paths = [f["value"] for f in wanted if f["kind"] == "file"]
    errors = _ask("fill", timeout=180, job=job_id, fields=wanted, paths=paths)["errors"]
    facts = _read(job_id, banners=False, options=True)
    known = {f.get("sig") for f in fields}
    return {"errors": errors, "screenshot": None, "next": facts["next"] and not facts["submit"],
            "submit": facts["submit"], "password": facts["password"],
            "new": [f for f in facts["fields"] if f.get("sig") not in known]}


def next_step(job_id: str) -> dict:
    if not _ask("press", job=job_id, kind="next", pattern=NEXT_BUTTON.pattern)["pressed"]:
        raise RuntimeError("no Next button found; move on in the job's tab, then read the page there")
    facts = _read(job_id, options=True)
    if facts["stayed"]:
        problems = [{"hid": f["hid"], "label": f["label"], "message": f["invalid"]} for f in facts["fields"] if f.get("invalid")]
        problems += [{"hid": None, "label": "", "message": a} for a in facts.get("alerts", [])]
        return {"stayed": True, "sent": False, "problems": problems, "challenge": facts["unchecked"],
                "url": facts["url"], "needs": ""}
    return {"stayed": False, "problems": [], "challenge": False, **_report(facts, _needs(facts), False)}


def submit(job_id: str, shot) -> dict:
    raise RuntimeError("press Submit yourself in the job's tab; Hal marks it applied when the site confirms it")


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

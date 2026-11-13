"""Drives job application forms in the applicant's own browser through the Hal browser helper.

The helper (extension/) asks the server for work and carries it out in tabs it opened for a job. This
module holds a queue per paired helper, and the steps of browser.py expressed as commands to it. A browser
that nobody is remote-controlling keeps the applicant's sign-ins and passes sites' human checks, which
Hal's own browser cannot; the applicant presses Submit.

Each application goes to one helper: the one that already holds its tab, else the one
paired in the browser of the window that started it, else the most recently seen helper of the same kind.
Queues are per helper, so a desktop browser and a laptop browser never take each other's commands.

Hal never presses Submit. Every command carries the guard's patterns, so content.js refuses
the same controls `apply.guard` refuses, and the helper keeps a block in the page's own world for the whole
of each command that cancels any submit the page or a press would make. There is no submit command, and no
command presses Enter.
"""
import concurrent.futures
import queue
import re
import threading
import time
import uuid

from . import browser
from .apply import guard
from .browser import (
    APPLIED,
    APPLY_BUTTON,
    ATS_FRAME,
    BLOCKED,
    BOT_CHECK,
    CAPTCHA,
    CODE_WALL,
    FORM_FRAME,
    MAX_PRESSES,
    NEXT_BUTTON,
    REGISTER_FIELDS,
    SIGN_OUT,
    SUBMIT_BUTTON,
    SUCCESS,
    UPLOAD_FAILED,
    _closed,
    _is_form,
    _site,
    code_boxes,
    note_signed_in,
    only_code_boxes,
    signed_in_at,
)
from .core import settings

POLL_WAIT = 20  # seconds a poll is held open; the browser stops a worker whose request takes 30
PATTERNS = {"apply": APPLY_BUTTON.pattern, "next": NEXT_BUTTON.pattern, "submit": SUBMIT_BUTTON.pattern,
            "signOut": SIGN_OUT.pattern, "frames": [*ATS_FRAME, *FORM_FRAME], "captcha": CAPTCHA}
FILL_WAIT = 180  # seconds a fill of a few fields may take: uploads and searched lists are slow
# A dropdown takes about 15 s to open, search and pick on a slow site (a form of a dozen lists once hit
# 180 s), so the limit grows with the fields of the fill.
FILL_PER_FIELD_S = 20
NOT_CONNECTED = "Open Hal in the browser that has the helper"
CONNECTED_S = 2 * POLL_WAIT  # a helper in touch this recently is connected: it polls again as each poll ends
READ_AGAIN_S = 2  # a pop-up's own link to the job (Adzuna's "No thanks, take me to the job") leaves the page

_lock = threading.Lock()
_queues: dict[str, queue.Queue] = {}
_waiting: dict[str, tuple[str, concurrent.futures.Future]] = {}   # command id -> (helper id, its answer)
_seen: dict[str, float] = {}
_carriers: dict[str, str] = {}       # job id -> helper id, for jobs whose tab a helper holds
_kept: set[str] = set()              # jobs whose carrier is written on their application row
_told_reload: dict[str, str] = {}    # helper id -> the version it was asked to reload from
_still_older: set[str] = set()       # helpers that reloaded and still came back older than Hal
_last_url: dict[str, str] = {}       # job id -> the address its tab showed last, for the notes about that site
# Bumped by `reset`: a poll still held open when everything was forgotten must not mark its helper as in touch
# again when it ends (a stopped test server's last poll made the next test class see a helper that had gone).
_generation = 0


def _queue(helper_id: str) -> queue.Queue:
    with _lock:
        return _queues.setdefault(helper_id, queue.Queue())


def _touch(helper_id: str, generation: int | None = None) -> bool:
    """Notes contact from a helper; True when it was not connected before (so setup's tick can follow).

    With `generation`, the contact counts only when nothing was forgotten (`reset`) since it began.
    """
    with _lock:
        if generation is not None and generation != _generation:
            return False
        was = _connected_locked(helper_id)
        _seen[helper_id] = time.time()
        return not was


def _connected_locked(helper_id: str) -> bool:
    busy = any(h == helper_id for h, _ in _waiting.values())
    return time.time() - _seen.get(helper_id, 0.0) < CONNECTED_S or busy


def _take(helper_id: str, wait: float) -> dict:
    """The helper's next command still waited for, within `wait` seconds; {} when none comes.

    A command whose ask timed out is dropped here: handed out, it would reach the helper before the next real
    one, which would then wait behind it until its own timeout (seen in a full run on 2026-10-23).
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            command = _queue(helper_id).get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            return {}
        with _lock:
            live = command.get("id") in _waiting
        if live:
            return command


def poll_helper(helper_id: str, version: str | None, protocol: int | None, wait: float | None = None) -> dict:
    """A paired helper's long poll (POST /helpers/poll): its next command, or a request to reload itself.

    A helper older than Hal is asked to reload once (it re-reads the files git updated); one still older
    after that is noted for "Is Hal working?".
    """
    from . import db
    from .apply import helpers
    from .core import events

    began = _generation
    first = _touch(helper_id)
    con = db.connect()
    try:
        helpers.seen(con, helper_id, version, protocol)
    finally:
        con.close()
    if first:
        events.publish("helper", id=helper_id, connected=True)
    wanted = helpers.wanted_version()
    reply = {"commands": [], "reload": False, "wanted_version": wanted}
    if helpers.older(version, wanted):
        with _lock:
            told = _told_reload.get(helper_id)
            if told is None:
                _told_reload[helper_id] = version or ""
                reply["reload"] = True
            else:
                _still_older.add(helper_id)
        if reply["reload"]:
            return reply
    else:
        with _lock:
            _told_reload.pop(helper_id, None)
            _still_older.discard(helper_id)
    try:
        command = _take(helper_id, POLL_WAIT if wait is None else wait)
    finally:
        _touch(helper_id, began)
    if command:
        reply["commands"] = [command]
    return reply


def deliver(result: dict, helper_id: str) -> None:
    """Takes a helper's answer to a command; an answer to another helper's command is ignored."""
    _touch(helper_id)
    with _lock:
        waiting = _waiting.get(result.get("id"))
        if waiting is None or waiting[0] != helper_id:
            return
        del _waiting[result.get("id")]
    waiting[1].set_result(result)


def connected(helper_id: str | None = None) -> bool:
    """True when that helper (or, with no id, any helper) has been in touch recently or is working on a command."""
    with _lock:
        if helper_id is not None:
            return _connected_locked(helper_id)
        return any(_connected_locked(h) for h in set(_seen) | {h for h, _ in _waiting.values()})


def still_older(helper_id: str) -> bool:
    """True when the helper reloaded itself and is still older than Hal ("Is Hal working?" says so)."""
    with _lock:
        return helper_id in _still_older


def assign(job_id: str, helper_id: str) -> None:
    """Gives a job's commands to this helper: the one that holds its tab (a tab handed to Hal)."""

    with _lock:
        _carriers[job_id] = helper_id
        _kept.discard(job_id)
    _keep(job_id, helper_id)


def _keep(job_id: str, helper_id: str) -> None:
    """Writes the carrier on the application row, once there is one (a handed-over tab gets its row later)."""
    from . import db
    from .apply import helpers

    if job_id in _kept:
        return
    con = db.connect()
    try:
        if helpers.carry(con, job_id, helper_id):
            with _lock:
                _kept.add(job_id)
    finally:
        con.close()


def _carrier(job_id: str | None, cmd: str) -> str:
    """The helper a command goes to; raises RuntimeError when no helper can take it."""
    from . import db
    from .apply import helpers

    with _lock:
        live = {h for h in set(_seen) | {h for h, _ in _waiting.values()} if _connected_locked(h)}
        kept = _carriers.get(job_id) if job_id else None
    if kept:
        if kept in live:
            _keep(job_id, kept)
            return kept
        if cmd != "open":
            # the form's tab is in that helper's browser; another browser cannot reach it
            raise RuntimeError(f"the browser helper that has this form's tab is not connected. {NOT_CONNECTED}.")
    con = db.connect()
    try:
        chosen = helpers.choose(con, job_id, live)
    finally:
        con.close()
    if chosen is None:
        raise RuntimeError(f"the browser helper is not connected. {NOT_CONNECTED}.")
    if job_id:
        assign(job_id, chosen)
    return chosen


def _ask(cmd: str, timeout: float = 90, **args) -> dict:
    helper_id = _carrier(args.get("job"), cmd)
    ident = uuid.uuid4().hex
    answer: concurrent.futures.Future = concurrent.futures.Future()
    with _lock:
        _waiting[ident] = (helper_id, answer)
    # Every command carries the guard's words, so content.js refuses what the guard refuses.
    _queue(helper_id).put({"id": ident, "cmd": cmd, "args": {**args, "guard": guard.PATTERNS}})
    try:
        result = answer.result(timeout)
    except concurrent.futures.TimeoutError:
        with _lock:
            _waiting.pop(ident, None)
        raise RuntimeError("the browser helper did not answer; check the browser is still open") from None
    if result["ok"]:
        value = result["value"]
        # Submits the page tried while the helper acted on it, cancelled by the helper's block.
        for what in dict.fromkeys((value or {}).get("blocked", []) if isinstance(value, dict) else []):
            guard.record(args.get("job"), (value or {}).get("url") or "", what)
        return value
    raise RuntimeError(plain_error(result["error"]))


# The browser's words for a page swapped out while the helper reached into it, after the helper's own retry.
FRAME_GONE = re.compile(r"frame with id \d+ was removed|no frame with id|frame was removed", re.I)


def plain_error(error: str) -> str:
    """A helper error in the applicant's words: the browser's own wording for a tab or page that went away
    ("Frame with ID 0 was removed" reached the Form step as it was, testers 2026-11-05)."""
    if error == "gone" or error.startswith("No tab with id"):
        return "the form's tab was closed; press \"Fill in the form\" to open it again"
    if FRAME_GONE.search(error):
        return "the page changed while Hal was reading it; press \"Fill it in again\""
    return error


def reset() -> None:
    """Forgets every queue, carrier and helper contact (tests start each class clean)."""
    global _generation
    with _lock:
        _generation += 1
        _queues.clear()
        _waiting.clear()
        _seen.clear()
        _carriers.clear()
        _kept.clear()
        _told_reload.clear()
        _still_older.clear()


def _read(job_id: str, banners: bool = True, options: bool = False, front: bool = True) -> dict:
    """Returns what the job's tab shows: its fields and the facts the steps below decide on.

    The tab is brought forward first (`front`), in either window setting, since a hidden tab's dropdowns
    never open; a look that only watches (`peek`) leaves the tabs where they are.
    """
    words, never = browser.banner_words(browser.notes_for(_last_url.get(job_id, ""), job_id))
    patterns = {
        **PATTERNS, "banners": [p.pattern for p in words] if banners else None,
        "popups": banners and bool(settings.get("applying.close_popups")),
        "bannersNever": never.pattern if never is not None and banners else None, "options": options}
    facts = _ask("read", job=job_id, front=front, patterns=patterns)
    if not facts and patterns["popups"]:
        # Closing a pop-up can follow its link away mid-read; the page it leads to is read instead.
        time.sleep(READ_AGAIN_S)
        facts = _ask("read", job=job_id, front=front, patterns={**patterns, "popups": False})
    if not facts:
        # The page was swapped out while it was read, or is one the browser will not let a script into.
        raise RuntimeError("the job's tab could not be read; it may still be loading or be a browser error page")
    _last_url[job_id] = facts["url"]
    facts["fields"] = browser.application_fields(facts["fields"])
    return facts


def _needs(facts: dict) -> str:
    """Names what only the applicant can do on this page: "sign-in", "check", "code", or "" for nothing.

    The same rules as Hal's own window (`browser._needs`): a bot check is seen in the page's words as well as
    its title, and a page that asks only for an emailed code is a code wall.
    """
    fields = facts["fields"]
    if only_code_boxes(fields, facts["text"][:3000]):
        return "code"
    if facts["password"] and not (_is_form(fields) and len(fields) > REGISTER_FIELDS):
        # An account page (email, password, confirm) is a sign-in, form or not; Adzuna's register page was filled
        # as the form's last page (testers, 2026-11-05). A registration form that also asks the usual questions
        # is filled in, apart from its password, as in Hal's own window.
        return "sign-in"
    if _is_form(fields):
        return ""
    if BOT_CHECK.search(facts["title"]) or BOT_CHECK.search(facts["text"][:400]):
        return "check"
    if 0 < len(fields) <= 3 and CODE_WALL.search(facts["text"][:3000]):
        return "code"
    # Sign-in pages that ask for the email address first show no password box yet.
    if 0 < len(fields) <= 2 and not facts["apply"] \
            and browser.sign_in_page(f"{facts['title']} {facts['headings']}", facts["url"], facts["text"]):
        return "sign-in"
    return ""


def _report(facts: dict, needs: str, pressed: bool) -> dict:
    form = _is_form(facts["fields"])
    text = "" if form else facts["text"]
    return {"url": facts["url"], "title": facts["title"], "fields": [] if needs else facts["fields"],
            "needs": needs, "sent": pressed and SUCCESS.search(text) is not None, "site": _site(facts["url"]),
            "closed": not form and _closed({"title": facts["title"], "headings": [facts["headings"]],
                                            "url": facts["url"], "text": facts["text"],
                                            "apply": facts.get("apply", False)}, None),
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
    first_done = False
    for presses in range(MAX_PRESSES + 1):
        facts = _read(job_id)
        needs = _needs(facts)
        if needs or _is_form(facts["fields"]) or presses == MAX_PRESSES:
            break
        first = None if first_done else browser._press_first(facts, browser.notes_for(facts["url"], job_id), job_id)
        if first is not None:
            # A `press_first` note: that control before any Apply, once, through the same guard.
            first_done = True
            exact = rf"^\s*{re.escape(first['name'])}\s*$"
            if _press(job_id, {**facts, "final": False}, "apply", pattern=exact):
                continue
        if facts["frame"]:
            # A form inside a frame cannot be read or filled from the page around it.
            _ask("goto", job=job_id, url=facts["frame"])
            continue
        if not facts["apply"]:
            break
        if facts["signOut"] or signed_in_at(facts["url"]):
            needs = "apply"
            break
        if not _press(job_id, {**facts, "final": False}, "apply", pattern=APPLY_BUTTON.pattern):
            break
    if not needs and any(f["kind"] == "combobox" for f in facts["fields"]):
        facts = _read(job_id, banners=False, options=True)
    return _report(facts, needs, pressed)


def open_form(job_id: str, url: str) -> dict:
    """Opens the application form in the applicant's own tab."""
    _last_url[job_id] = url

    _ask("open", timeout=120, job=job_id, url=url, window=settings.get("applying.window"),
         front=bool(settings.get("applying.bring_forward")))
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
    """Continues after the person passed a sign-in or other wall by hand."""
    if passed == "sign-in":
        note_signed_in(wall)
    return _advance(job_id, pressed=passed == "apply")


def peek(job_id: str) -> dict:
    """Reads the page without changing it."""
    try:
        facts = _read(job_id, banners=False, front=False)
    except RuntimeError as e:
        return {"gone": True} if "tab was closed" in str(e) else {"busy": True}
    return {"needs": _needs(facts), "form": _is_form(facts["fields"]), "url": facts["url"],
            "sent": SUCCESS.search(facts["text"]) is not None, "applied": APPLIED.search(facts["text"]) is not None,
            "challenge": facts["unchecked"], "password": False,
            "values": {f["sig"]: f.get("current") for f in facts["fields"]},
            "code": code_boxes(facts["fields"], facts["text"]),
            "code_sigs": [f["sig"] for f in facts["fields"] if f["hid"] in code_boxes(facts["fields"], facts["text"])]}


def read_here(job_id: str) -> dict:
    """Reads the form on the page the tab is on."""
    return _report(_read(job_id, options=True), "", False)


def fill(job_id: str, fields: list[dict], shot) -> dict:
    """Enters the approved values, after any `field_transform` note; there is no picture, since the form is in
    the applicant's own tab."""
    fields, trace = browser.with_notes(job_id, _last_url.get(job_id, ""), fields)
    # `typeable` lets the helper type into a list that offers its options only once you type (Ashby's places).
    wanted = [{k: f.get(k) for k in ("hid", "kind", "value", "options", "typeable")} for f in fields
              if f.get("value") not in (None, "", []) and not f.get("skip")]
    paths = [f["value"] for f in wanted if f["kind"] == "file"]
    out = _ask("fill", timeout=max(FILL_WAIT, FILL_PER_FIELD_S * len(wanted)), job=job_id, fields=wanted, paths=paths)
    errors = out["errors"]
    for hid in out.get("notTaken") or []:
        # A file the site dropped is never reported as done.
        errors[hid] = UPLOAD_FAILED
    facts = _read(job_id, banners=False, options=True)
    known = {f.get("sig") for f in fields}
    return {"errors": errors, "screenshot": None, "next": facts["next"] and not facts["submit"],
            "submit": facts["submit"], "password": facts["password"], "notes_used": trace,
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
    """Outlines the filled answers in the applicant's tab."""
    _ask("mark", job=job_id, fields=fields, panel=panel)


def show(job_id: str) -> bool:
    """Brings a job's tab to the front."""
    return _ask("show", job=job_id)["shown"]


def search(queries: list[str], pause: float = 0) -> tuple[list[list[dict]], bool]:
    """Runs searches in the applicant's browser and returns the result pages and whether a site refused."""
    out = _ask("search", timeout=60 + (pause + 20) * len(queries), queries=queries, pause=pause)
    return out["pages"], out["refused"]

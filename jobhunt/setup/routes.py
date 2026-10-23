"""API v2 routes of the setup guide: the steps, About you, the record folder, the
email test and phone alerts. The steps owned by other areas (CV, goal, AI, helper, updates) use their own
routes.

Design notes:
- About you saves only what the person changed in the step: the page sends the values it showed (`seen`)
  with the values it has now, and a field whose value is unchanged is never written. So going through the
  step again never resets a value changed in Settings meanwhile, and right to work keeps its saved value
  (the Windows build's setup reset it on every run).
- Finishing the guide starts the first search once; its action id is kept on All set's row, so Home can
  show its progress and pressing Go to Home again starts nothing new.
- The email test signs in to Gmail while the request waits (at most `email_test.LOGIN_TIMEOUT_S`), because
  the page needs the answer before it can say "Connected"; the route is marked slow for that reason.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sqlite3
from typing import Any

from ..core import clock, runner, settings
from ..core.settings import secrets
from ..core.settings.registry import SettingError
from ..web.router import BadRequest, Conflict, Request, route
from . import email_test, phone, record_folder, state

log = logging.getLogger(__name__)

FIRST_SEARCH = "find.discover"
# The fields of About you, in the step's order, with the step's own labels.
ABOUT = (("you.name", "Your name"), ("you.email", "Email"), ("you.phone", "Phone"), ("you.city", "Town or city"),
         ("you.right_to_work", "Can you work in the UK?"), ("goal.min_pay", "Lowest pay you would accept"))
ABOUT_KEYS = {k for k, _ in ABOUT}
FROM_CV = ("you.name", "you.email", "you.phone", "you.city")
# The step's own words for each right-to-work choice; the stored values are the setting's.
RIGHT_TO_WORK_WORDS = {"uk_no_visa": "Yes, no visa needed", "uk_visa": "Yes, with a visa",
                       "need_sponsorship": "I would need sponsorship"}
# Who provides each "found" line, when that package is installed: (module, function).
FINDERS = {"usb_install": ("jobhunt.core.importer_usb", "find"), "old_helper": ("jobhunt.apply.helpers", "old_helper")}


def _session_id(req: Request) -> str | None:
    return req.session.id if req.session else None


def _found() -> dict:
    out: dict[str, Any] = {}
    for key, (module, name) in FINDERS.items():
        out[key] = None
        if importlib.util.find_spec(module) is None:
            continue
        fn = getattr(importlib.import_module(module), name, None)
        if fn is None:
            continue
        try:
            out[key] = fn()
        except (OSError, RuntimeError, ValueError) as e:
            log.warning("could not look for %s: %s", key, e)
    return out


@route("GET", "/api/v2/setup")
def overview(req: Request) -> dict:
    """The steps, their states, the step to open at, and an older Hal or helper found on this computer."""
    return state.overview(req.con, _found())


def start_first_search() -> str | None:
    """Starts the first search now; None when one is already running."""
    from ..core import scheduler

    try:
        return scheduler.run_now(FIRST_SEARCH)
    except (LookupError, RuntimeError) as e:
        log.info("the first search did not start: %s", e)
        return None


def _finish(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT data FROM setup_steps WHERE id = 'done'").fetchone()
    data = json.loads(row["data"]) if row and row["data"] else {}
    if data.get("started"):
        return
    action_id = start_first_search()
    con.execute("""INSERT INTO setup_steps (id, state, updated, data) VALUES ('done', 'done', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET data = excluded.data""",
                (clock.iso(), json.dumps({"first_search": action_id, "started": clock.iso()}, ensure_ascii=True)))
    con.commit()


@route("PUT", "/api/v2/setup/{step_id}")
def change(req: Request) -> dict:
    """Marks a step done, skipped or current; finishing All set starts the first search."""
    wanted = str((req.body or {}).get("state") or "")
    step_id = req.params["step_id"]
    state.set_state(req.con, step_id, wanted)
    if step_id == "done" and wanted == "done":
        runner.flush()
        _finish(req.con)
    return state.overview(req.con, _found())


@route("POST", "/api/v2/setup/later")
def later(req: Request) -> dict:
    """"Save and finish later": the guide stays closed until the person opens it."""
    state.later(req.con)
    return state.overview(req.con, _found())


def _from_cv() -> dict[str, str]:
    try:
        from ..cvstudio import parse, store

        _, doc = store.current()
        return parse.prefill(doc)
    except (LookupError, ValueError, OSError, sqlite3.Error) as e:
        log.info("no CV to compare About you with: %s", e)
        return {}


def _field(key: str, label: str, cv: dict[str, str]) -> dict:
    item = settings.setting(key)
    value = settings.get(key)
    out = {"key": key, "label": label, "value": value, "kind": item.kind,
           "from_cv": key in FROM_CV and bool(value) and str(cv.get(key, "")).strip() == str(value).strip()}
    if key == "you.right_to_work":
        out["choices"] = [[v, RIGHT_TO_WORK_WORDS.get(v, words)] for v, words in item.choices]
    if key == "goal.min_pay":
        out["display"] = settings.display(key, value) if value else ""
    return out


@route("GET", "/api/v2/setup/about")
def about(req: Request) -> dict:
    """About you as it is saved now, each field marked when it matches the CV."""
    cv = _from_cv()
    return {"fields": [_field(k, label, cv) for k, label in ABOUT]}


def save_about(values: dict, seen: dict, *, session_id: str | None = None) -> dict:
    """Saves each About you field whose value differs from what the page showed; words for each one."""
    out: dict[str, dict] = {}
    for key, value in values.items():
        if key not in ABOUT_KEYS:
            raise BadRequest("About you has no field by that name.", field=key)
        if key in seen and seen[key] == value:
            continue
        try:
            result = settings.set(key, value, by="setup", session_id=session_id)
        except SettingError as e:
            out[key] = {"ok": False, "words": str(e)}
            continue
        out[key] = {"ok": True, "value": result.value, "words": result.display}
    return out


@route("PUT", "/api/v2/setup/about/fields")
def save_about_fields(req: Request) -> dict:
    """Saves the fields the person changed: `{values, seen}`; a field Hal cannot read says why."""
    body = req.body if isinstance(req.body, dict) else {}
    values, seen = body.get("values"), body.get("seen") or {}
    if not isinstance(values, dict) or not isinstance(seen, dict):
        raise BadRequest("Send the values to save.")
    return {"saved": save_about(values, seen, session_id=_session_id(req))}


@route("POST", "/api/v2/setup/record")
def make_record(req: Request) -> dict:
    """Makes the record folder with its starter folders, or finds the one already there."""
    return record_folder.ensure()


@route("POST", "/api/v2/email/test", slow=True)
def test_email(req: Request) -> dict:
    """Tries a Gmail app password and saves it, with the address, only when Gmail accepts it."""
    body = req.body if isinstance(req.body, dict) else {}
    return email_test.try_and_save(str(body.get("address") or ""), str(body.get("password") or "")).as_dict()


@route("POST", "/api/v2/notify/phone/topic")
def phone_topic(req: Request) -> dict:
    """The QR code for phone alerts; makes the private code first when none is saved, or `{new: true}`."""
    try:
        return phone.topic_qr(new=bool((req.body or {}).get("new")))
    except secrets.SecretsLocked as e:
        raise Conflict("Hal could not keep the code: your computer's password store is locked. Unlock it, then "
                       "try again.") from e


@route("POST", "/api/v2/notify/phone/test", slow=True)
def phone_test(req: Request) -> dict:
    """Sends a test alert to the phone."""
    return phone.send_test()

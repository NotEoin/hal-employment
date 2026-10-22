"""Activity: one plain-words row for everything Hal did, each with its why.

`record(...)` writes a row and refuses an empty `why` (the table also has a CHECK). Text fields pass the
private-answer guard first: when `ai.guard.contains_private` finds a saved private answer in one, that
field is replaced by `LEFT_OUT`, so a private answer can never reach Activity, events or exports.
`item(row)` shapes a row as the API's ActivityItem; `undo(id)` runs the handler registered for the row's
undo type with `@undo_handler(type)`, found in modules named `undo.py` or `*_undo.py`.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from .. import db
from . import clock, events, plugins

log = logging.getLogger(__name__)

GUARD_MODULE = "jobhunt.ai.guard"
LEFT_OUT = "(left out: it held one of your private answers)"
AREA_LABELS = {"find": "Finding jobs", "documents": "Documents", "applying": "Applying", "email": "Email",
               "learning": "Learning", "settings": "Settings", "cv": "CV", "system": "Hal"}
PROVIDER_WORDS = {"claude": "through your Claude subscription", "ollama": "on this computer",
                  "fake": "a stand-in model for tests"}
PAGE_LIMIT = 50
MAX_LIMIT = 200
UNDO_WHY = "You pressed Undo."
MINUTE_S = 60
HOUR_S = 60 * MINUTE_S

UndoFn = Callable[[sqlite3.Connection, dict, dict], str | None]
UNDO: dict[str, UndoFn] = {}
_guard: Callable[[str], bool] | None = None
_guard_looked = False


def undo_handler(kind: str) -> Callable[[UndoFn], UndoFn]:
    """Decorator: `fn(con, spec, row) -> words | None` undoes an Activity row whose undo type is `kind`."""
    def register(fn: UndoFn) -> UndoFn:
        if kind in UNDO and UNDO[kind] is not fn:
            raise ValueError(f"undo type {kind!r} has two handlers")
        UNDO[kind] = fn
        return fn
    return register


def _find_guard() -> Callable[[str], bool] | None:
    global _guard, _guard_looked
    if not _guard_looked:
        _guard_looked = True
        if importlib.util.find_spec(GUARD_MODULE) is not None:
            _guard = getattr(importlib.import_module(GUARD_MODULE), "contains_private", None)
    return _guard


def guard_text(text: str | None) -> str | None:
    """`text`, or `LEFT_OUT` when it holds a saved private answer."""
    if not text:
        return text
    check = _find_guard()
    if check is not None and check(text):
        return LEFT_OUT
    return text


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def record(area: str, kind: str, text: str, why: str, *, used: list[dict] | None = None, ai: dict | None = None,
           took_ms: int | None = None, changed: str | None = None, undo: dict | None = None,
           job_id: str | None = None, action_id: str | None = None, icon: str | None = None,
           tone: str | None = None, session_id: str | None = None, remote: bool = False,
           detail: str | None = None, con: sqlite3.Connection | None = None) -> int:
    """Adds an Activity row and returns its id; ValueError on an empty `why`.

    With `con`, the row is written on that connection and the caller commits; without it, on a connection
    of its own, committed at once.
    """
    if not (why or "").strip():
        raise ValueError(f"an Activity row needs a why ({kind}: {text!r})")
    if not (text or "").strip():
        raise ValueError(f"an Activity row needs words ({kind})")
    if undo is not None and not undo.get("type"):
        raise ValueError("an undo needs a type")
    clean_used = [{**u, "label": guard_text(u.get("label", ""))} for u in used] if used else None
    row = (clock.iso(), area, kind, icon, tone, guard_text(text), guard_text(why), _json(clean_used), _json(ai),
           took_ms, guard_text(changed), _json(undo), job_id, action_id, session_id, int(remote), guard_text(detail))
    sql = """INSERT INTO activity (at, area, kind, icon, tone, text, why, used, ai, took_ms, changed, undo,
                                   job_id, action_id, session_id, remote, detail)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    if con is not None:
        return con.execute(sql, row).lastrowid
    own = db.connect()
    try:
        new_id = own.execute(sql, row).lastrowid
        own.commit()
    finally:
        own.close()
    events.publish("activity", id=new_id)
    return new_id


UPDATABLE = ("text", "why", "used", "ai", "took_ms", "changed", "undo", "undone", "icon", "tone", "running", "detail")


def update(activity_id: int, con: sqlite3.Connection | None = None, **fields: Any) -> None:
    """Changes fields of one row; `why` may never become empty."""
    bad = set(fields) - set(UPDATABLE)
    if bad:
        raise ValueError(f"Activity rows have no field {', '.join(sorted(bad))}")
    if "why" in fields and not (fields["why"] or "").strip():
        raise ValueError("an Activity row needs a why")
    values = {k: (_json(v) if k in ("used", "ai", "undo") else guard_text(v) if isinstance(v, str) else v)
              for k, v in fields.items()}
    if not values:
        return
    sets = ", ".join(f"{k} = ?" for k in values)
    own = con is None
    con = con or db.connect()
    try:
        cur = con.execute(f"UPDATE activity SET {sets} WHERE id = ?", (*values.values(), activity_id))
        if not cur.rowcount:
            raise LookupError("That line is not in Activity.")
        if own:
            con.commit()
    finally:
        if own:
            con.close()


def _row(con: sqlite3.Connection, activity_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM activity WHERE id = ?", (activity_id,)).fetchone()
    if row is None:
        raise LookupError("That line is not in Activity.")
    return row


def undo(activity_id: int, *, session_id: str | None = None) -> int:
    """Undoes one row through its handler; returns the id of the new row that says so.

    RuntimeError when the row has no Undo, was undone already, or its handler is missing.
    """
    plugins.modules("undo")
    con = db.connect()
    try:
        row = _row(con, activity_id)
        if not row["undo"]:
            raise RuntimeError("This cannot be undone.")
        if row["undone"]:
            raise RuntimeError("This was undone already.")
        spec = json.loads(row["undo"])
        handler = UNDO.get(spec.get("type"))
        if handler is None:
            raise RuntimeError("Hal cannot undo this kind of change yet.")
        words = handler(con, spec, dict(row))
        con.execute("UPDATE activity SET undone = ? WHERE id = ?", (clock.iso(), activity_id))
        new_id = record(row["area"], f"{row['kind']}.undo", words or f"Undid: {row['text']}", UNDO_WHY,
                        job_id=row["job_id"], icon="undo", tone="info", session_id=session_id, con=con)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    events.publish("activity", id=activity_id)
    events.publish("activity", id=new_id)
    return new_id


# ------------------------------------------------------------------------------------------- the shape

def took_words(ms: int | None, running: bool = False) -> str:
    """"6 seconds", "3 minutes", "1 hour 5 minutes"; "... so far" while it runs."""
    if ms is None:
        return ""
    s = max(0, round(ms / 1000))
    if s < MINUTE_S:
        words = "under a second" if s == 0 else f"{s} second{'s' if s != 1 else ''}"
    elif s < HOUR_S:
        m = round(s / MINUTE_S)
        words = f"{m} minute{'s' if m != 1 else ''}"
    else:
        h, m = divmod(round(s / MINUTE_S), MINUTE_S)
        words = f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minute{'s' if m != 1 else ''}" if m else "")
    return f"{words} so far" if running else words


def ai_words(ai: dict | None) -> tuple[str, str]:
    """The AI line for people ("llama3, on this computer") and the one under Details."""
    if not ai:
        return "", ""
    provider = ai.get("provider") or ""
    model = ai.get("model") or provider or "AI"
    calls = int(ai.get("calls") or 0)
    words = model + (f", {PROVIDER_WORDS[provider]}" if provider in PROVIDER_WORDS else "")
    if calls > 1:
        words += f" ({calls} requests)"
    if ai.get("mixed"):
        words += ", and others"
    detail = (f"provider {provider}, model {ai.get('model') or '-'}, role {ai.get('role') or '-'}, "
              f"{calls} calls, {int(ai.get('ms') or 0)} ms")
    return words, detail


def day_words(at: str, today: Any = None) -> str:
    """"Today", "Yesterday", else "Thursday 22 October", in the person's own time zone."""
    day = clock.parse(at).astimezone().date()
    today = today or clock.local_day()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return f"{day:%A} {day.day} {day:%B}"


def item(row: sqlite3.Row | dict) -> dict:
    """One row as the API's ActivityItem."""
    r = dict(row)
    used = json.loads(r["used"]) if r.get("used") else []
    ai = json.loads(r["ai"]) if r.get("ai") else None
    words, detail = ai_words(ai)
    running = bool(r.get("running"))
    took_ms = r.get("took_ms")
    if running and took_ms is None:
        took_ms = int((clock.now_utc() - clock.parse(r["at"])).total_seconds() * 1000)
    return {"id": r["id"], "t": clock.parse(r["at"]).astimezone().strftime("%H:%M"), "day": day_words(r["at"]),
            "at": r["at"], "area": r["area"], "area_label": AREA_LABELS.get(r["area"], r["area"].title()),
            "kind": r["kind"], "icon": r.get("icon") or "hal", "tone": r.get("tone") or "", "text": r["text"],
            "running": running, "why": r["why"],
            "used": [{"kind": u.get("kind", ""), "label": u.get("label", "")} for u in used],
            "ai_words": words, "ai_detail": detail, "took": took_words(took_ms, running),
            "changed": r.get("changed") or "", "undo": bool(r.get("undo")) and not r.get("undone"),
            "undone": bool(r.get("undone")), "remote": bool(r.get("remote")), "job_id": r.get("job_id"),
            "action_id": r.get("action_id"), "detail": r.get("detail") or ""}


def one(con: sqlite3.Connection, activity_id: int) -> dict:
    """One row as an ActivityItem; LookupError when there is none."""
    return item(_row(con, activity_id))


def page(con: sqlite3.Connection, *, area: str | None = None, before: int | None = None,
         limit: int = PAGE_LIMIT, job_id: str | None = None) -> dict:
    """The Activity page: newest first, grouped by day; `more` is the `before` for the next page, or None."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    where, args = [], []
    if area:
        if area not in AREA_LABELS:
            raise ValueError(f"There is no Activity area called {area!r}.")
        where.append("area = ?")
        args.append(area)
    if before is not None:
        where.append("id < ?")
        args.append(int(before))
    if job_id:
        where.append("job_id = ?")
        args.append(job_id)
    sql = "SELECT * FROM activity" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    rows = con.execute(sql, (*args, limit + 1)).fetchall()
    more = rows[limit - 1]["id"] if len(rows) > limit else None
    days: list[dict] = []
    for r in rows[:limit]:
        it = item(r)
        if not days or days[-1]["day"] != it["day"]:
            days.append({"day": it["day"], "items": []})
        days[-1]["items"].append(it)
    return {"days": days, "more": more}

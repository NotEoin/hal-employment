"""The status line's words: what Hal is doing now, or when it works next.

The most urgent action wins: one waiting for the person first, then running before queued, then the lowest
priority number (0 is something the person just asked for), then one not waiting for a record update, then
the lowest work class (`core.work`: what the person waits for, record updates, document writes, the rest),
then the newest. A document write waiting for a record update says so after its label, so the line never
claims Hal is writing while it waits. The rest comes from other areas when they are
there, each optional so the line works on its own: `core.scheduler.next_runs()` and `paused()`,
`ai.budget.today()` and `core.health.problem_count()`.

`eta_s` is the time left for a running action that counts items, shown only once it has run `ETA_AFTER_S`:
before that the pace is a guess and the line would jump. The line is published on every item, so the
estimate is worked out at most every `ETA_REFRESH_S` and counts down from its end time in between. A kind
with its own estimate (`ETA_BY_KIND`) uses it, so the line and that kind's page never disagree.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, NotRequired, TypedDict

from jobhunt.core import clock, runner, work

log = logging.getLogger(__name__)

NEXT_SHOWN = 2
PAUSED_META = "schedule:paused"
# The schedule's labels are verbs ("Search for jobs"); the line reads "Next job search at 13:30"
NEXT_WORDS = {"find.discover": "job search", "mail.check": "email check", "notify.digest": "daily summary",
              "record.sync": "read of your record folder", "system.backup": "backup", "system.tidy": "tidy-up",
              "learn.patterns": "look for patterns", "updates.check": "check for updates"}

ETA_AFTER_S = 30
ETA_REFRESH_S = 10
# reading files into the record mixes AI reads (seconds each) with rule reads (a few ms), so one pace for both
# said 21 hours for an ingest that took two (2026-11-05); its own estimate paces each kind apart
ETA_BY_KIND = {"record.ingest": ("jobhunt.ingest.items", "eta_for")}
_eta_cache: dict[str, tuple[float, datetime | None]] = {}


class NextRun(TypedDict):
    """One of the next scheduled jobs, as the status line names it."""

    what: str
    at: str


class StatusReply(TypedDict):
    """The `/status` reply; `state` is there only while Hal is busy."""

    busy: bool
    text: str
    step_n: int
    step_total: int | None
    eta_s: int | None
    action_id: str | None
    why_activity_id: int | None
    next: list[NextRun]
    paused: bool
    problems: int
    ai_limited_until: str | None
    reconnecting: bool
    state: NotRequired[str]


def _optional(module: str, name: str) -> Any | None:
    """`module.name` when that module exists and defines it, else None."""
    try:
        if importlib.util.find_spec(module) is None:
            return None
    except (ImportError, ValueError):
        return None
    return getattr(importlib.import_module(module), name, None)


def _call(module: str, name: str, *args: Any, default: Any = None) -> Any:
    fn = _optional(module, name)
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception:  # one area's failure must not blank the status line
        log.exception("%s.%s failed for the status line", module, name)
        return default


STATE_ORDER = {"waiting_for_you": 0, "running": 1, "queued": 2}


def _waits_for_record(row: sqlite3.Row) -> bool:
    return row["state"] == "running" and row["message"] == work.RECORD_WAIT_WORDS


def _urgency(row: sqlite3.Row) -> tuple[int, int, bool, int]:
    kind = runner.KINDS.get(row["kind"])
    cls = kind.work_class if kind is not None else work.for_kind(row["kind"], row["priority"])
    return STATE_ORDER[row["state"]], row["priority"], _waits_for_record(row), cls


def busiest(con: sqlite3.Connection) -> sqlite3.Row | None:
    """The action the status line shows, or None when Hal is idle."""
    rows = con.execute(
        """SELECT a.*, v.text AS activity_text FROM actions a LEFT JOIN activity v ON v.id = a.activity_id
           WHERE a.state IN ('running', 'waiting_for_you', 'queued') ORDER BY a.created DESC""").fetchall()
    return min(rows, key=_urgency) if rows else None     # `min` keeps the first, the newest, of equals


def _paused(con: sqlite3.Connection) -> bool:
    fn = _optional("jobhunt.core.scheduler", "paused")
    if fn is not None:
        return bool(_call("jobhunt.core.scheduler", "paused", default=False))
    row = con.execute("SELECT v FROM meta WHERE k = ?", (PAUSED_META,)).fetchone()
    return bool(row and row[0] == "1")


def _next(con: sqlite3.Connection) -> list[NextRun]:
    runs = _call("jobhunt.core.scheduler", "next_runs", default=[]) or []
    return [{"what": NEXT_WORDS.get(r.get("id", "")) or r.get("label", "").lower() or r.get("id", ""),
             "at": r.get("at")} for r in runs[:NEXT_SHOWN] if r.get("at")]


def _eta(con: sqlite3.Connection, row: sqlite3.Row) -> int | None:
    """Seconds left for `row`, or None when there is no fair estimate yet."""
    now = clock.now_utc()
    if row["state"] != "running" or not row["started"] or not row["done_total"]:
        return None
    if (now - clock.parse(row["started"])).total_seconds() < ETA_AFTER_S:
        return None
    kept = _eta_cache.get(row["id"])
    if kept is None or time.monotonic() - kept[0] >= ETA_REFRESH_S:
        own = ETA_BY_KIND.get(row["kind"])
        left = _call(*own, con, row) if own else runner.eta_s(row, now=now)
        kept = (time.monotonic(), None if left is None else now + timedelta(seconds=left))
        _eta_cache.clear()           # only the action on the line is kept
        _eta_cache[row["id"]] = kept
    return None if kept[1] is None else max(0, round((kept[1] - now).total_seconds()))


def _step_total(row: sqlite3.Row) -> int | None:
    """The steps' total, or None once the step number has passed it, and the line then shows no step count (as it
    does for an action with no total). An action's total is its own guess of how many steps it takes, and "Step
    2 of 1" is false. The total is dropped, not raised: a raised one would say "Step 2 of 2" and claim the work
    was done while more steps come (2026-11-18)."""
    total = row["step_total"]
    return None if total is not None and row["step_n"] > total else total


def status(con: sqlite3.Connection) -> StatusReply:
    """The `/status` reply."""
    row = busiest(con)
    budget = _call("jobhunt.ai.budget", "today", default={}) or {}
    out: StatusReply = {
        "busy": False, "text": "", "step_n": 0, "step_total": None, "eta_s": None, "action_id": None,
        "why_activity_id": None, "next": _next(con), "paused": _paused(con),
        "problems": int(_call("jobhunt.core.health", "problem_count", con, default=0) or 0),
        "ai_limited_until": budget.get("limited_until"), "reconnecting": False}
    if row is not None:
        text = row["activity_text"] or row["message"] or row["kind"]
        if (row["state"] == "waiting_for_you" or _waits_for_record(row)) and row["message"]:
            text = f"{text}. {row['message']}" if row["message"] != text else text
        out.update(busy=True, text=text, step_n=row["step_n"], step_total=_step_total(row),
                   eta_s=_eta(con, row), action_id=row["id"], why_activity_id=row["activity_id"], state=row["state"])
    return out

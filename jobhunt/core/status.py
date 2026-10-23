"""The status line's words: what Hal is doing now, or when it works next (`/status`).

The most urgent action wins: one waiting for the person first, then the lowest priority number (0 is
something the person just asked for), then the newest. The rest comes from other areas when they are
there, each optional so the line works on its own: `core.scheduler.next_runs()` and `paused()`,
`ai.budget.today()` and `core.health.problem_count()`.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sqlite3
from typing import Any

log = logging.getLogger(__name__)

NEXT_SHOWN = 2
PAUSED_META = "schedule:paused"
# The schedule's labels are verbs ("Search for jobs"); the line reads "Next job search at 13:30"
NEXT_WORDS = {"find.discover": "job search", "mail.check": "email check", "notify.digest": "daily summary",
              "record.sync": "read of your record folder", "system.backup": "backup", "system.tidy": "tidy-up",
              "learn.patterns": "look for patterns", "updates.check": "check for updates"}


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


def busiest(con: sqlite3.Connection) -> sqlite3.Row | None:
    """The action the status line shows, or None when Hal is idle."""
    return con.execute(
        """SELECT a.*, v.text AS activity_text FROM actions a LEFT JOIN activity v ON v.id = a.activity_id
           WHERE a.state IN ('running', 'waiting_for_you', 'queued')
           ORDER BY CASE a.state WHEN 'waiting_for_you' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
                    a.priority, a.created DESC LIMIT 1""").fetchone()


def _paused(con: sqlite3.Connection) -> bool:
    fn = _optional("jobhunt.core.scheduler", "paused")
    if fn is not None:
        return bool(_call("jobhunt.core.scheduler", "paused", default=False))
    row = con.execute("SELECT v FROM meta WHERE k = ?", (PAUSED_META,)).fetchone()
    return bool(row and row[0] == "1")


def _next(con: sqlite3.Connection) -> list[dict]:
    runs = _call("jobhunt.core.scheduler", "next_runs", default=[]) or []
    return [{"what": NEXT_WORDS.get(r.get("id", "")) or r.get("label", "").lower() or r.get("id", ""),
             "at": r.get("at")} for r in runs[:NEXT_SHOWN] if r.get("at")]


def status(con: sqlite3.Connection) -> dict:
    """The `/status` reply."""
    row = busiest(con)
    budget = _call("jobhunt.ai.budget", "today", default={}) or {}
    out = {"busy": False, "text": "", "step_n": 0, "step_total": None, "action_id": None, "why_activity_id": None,
           "next": _next(con), "paused": _paused(con),
           "problems": int(_call("jobhunt.core.health", "problem_count", con, default=0) or 0),
           "ai_limited_until": budget.get("limited_until"), "reconnecting": False}
    if row is not None:
        text = row["activity_text"] or row["message"] or row["kind"]
        if row["state"] == "waiting_for_you" and row["message"]:
            text = f"{text}. {row['message']}" if row["message"] != text else text
        out.update(busy=True, text=text, step_n=row["step_n"], step_total=row["step_total"],
                   action_id=row["id"], why_activity_id=row["activity_id"], state=row["state"])
    return out

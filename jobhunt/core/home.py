"""Home's reply, composed from every area's `home.py`, and the core's own "needs you" items.

`compose(con)` answers `GET /api/v2/home`: the date and greeting, the "needs you" list merged from every
area's `needs(con)` by urgency, and the optional sections (`new_matches`, `goal`, `setup`, `week`) areas
give through `sections(con)`. One area that fails is logged and left out, so Home always draws.

The core's own items: Hal is paused (with Carry on), and a developer checkout still on the old data layout
(with the command that moves it). The route lives in `schedule_routes.py`.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import clock, paths, plugins

log = logging.getLogger(__name__)

MORNING_ENDS = 12
AFTERNOON_ENDS = 18
URGENT = 90
NORMAL = 50
TODAY_SHOWN = 5
SECTIONS = ("new_matches", "first_search", "week", "goal", "setup")


def _get(key: str) -> Any:
    from . import settings

    return settings.get(key)


def greeting(now=None) -> str:
    """"Good morning, Robin": the part of the day and the name Hal calls the person."""
    local = (now or clock.now_utc()).astimezone()
    part = "morning" if local.hour < MORNING_ENDS else "afternoon" if local.hour < AFTERNOON_ENDS else "evening"
    name = (_get("you.preferred_name") or "").strip()
    return f"Good {part}, {name}" if name else f"Good {part}"


def date_words(now=None) -> str:
    """"Thursday 22 October"."""
    local = (now or clock.now_utc()).astimezone()
    return f"{local:%A} {local.day} {local:%B}"


def needs(con: sqlite3.Connection) -> list[dict]:
    """The core's own "needs you" items."""
    from . import scheduler

    out = []
    if scheduler.paused(con):
        out.append({"id": "core.paused", "icon": "pause", "tone": "warn", "title": "Hal is paused",
                    "detail": "Searches and the daily summary wait until you carry on. Email checks carry on.",
                    "due": None, "act": {"label": "Carry on", "action": "/status/pause"}, "urgency": NORMAL})
    if paths.home_override() is None and paths.is_legacy_layout():
        out.append({"id": "core.move_data", "icon": "folder", "tone": "warn",
                    "title": "Move your data into Hal's own folder",
                    "detail": "Hal still reads the data folder inside its code. "
                              "Run: python -m jobhunt import --dry-run",
                    "due": None, "act": None, "urgency": URGENT})
    return out


def _counts(con: sqlite3.Connection) -> dict:
    def one(sql: str) -> int:
        try:
            return int(con.execute(sql).fetchone()[0])
        except sqlite3.Error:
            return 0
    return {"jobs": one("SELECT COUNT(*) FROM jobs WHERE status = 'new' AND active = 1"),
            "apps": one("SELECT COUNT(*) FROM applications WHERE stage != 'submitted'")}


def _today(con: sqlite3.Connection) -> list[dict]:
    from . import activity

    day = clock.local_day().isoformat()
    rows = con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (TODAY_SHOWN * 4,)).fetchall()
    items = [activity.item(r) for r in rows if clock.parse(r["at"]).astimezone().date().isoformat() == day]
    return items[:TODAY_SHOWN]


def compose(con: sqlite3.Connection) -> dict:
    """The whole `/home` reply."""
    merged: list[dict] = []
    sections: dict[str, Any] = {}
    for module in plugins.modules("home"):
        name = module.__name__
        try:
            if hasattr(module, "needs"):
                merged.extend(module.needs(con) or [])
            if hasattr(module, "sections"):
                sections.update(module.sections(con) or {})
        except Exception:  # one area's failure must not blank Home
            log.exception("%s could not add to Home", name)
    merged.sort(key=lambda n: -int(n.get("urgency") or 0))
    out = {"date_words": date_words(), "greeting": greeting(), "summary": sections.pop("summary", ""),
           "needs": merged, "today": _today(con), "counts": {**_counts(con), **sections.pop("counts", {})}}
    for key in SECTIONS:
        out[key] = sections.pop(key, [] if key == "new_matches" else None)
    out.update(sections)
    return out

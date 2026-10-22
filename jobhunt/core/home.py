"""The core's own "needs you" items on Home: Hal is paused, and data still in the old layout.

A Home provider like every area's `home.py`; `core/home_routes.py` composes the `/home` reply and sorts the
needs by `urgency` on its scale, where a lower number is more urgent.
"""
from __future__ import annotations

import sqlite3

from . import paths

MOVE_DATA_URGENCY = 5      # before anything else: Hal still reads the data folder inside its code
PAUSED_URGENCY = 35        # after what waits for you and the emails, before the to-dos


def needs(con: sqlite3.Connection) -> list[dict]:
    """The core's own "needs you" items."""
    from . import scheduler

    out = []
    if scheduler.paused(con):
        out.append({"id": "core.paused", "icon": "pause", "tone": "warn", "title": "Hal is paused",
                    "detail": "Searches and the daily summary wait until you carry on. Email checks carry on.",
                    "due": None, "act": {"label": "Carry on", "action": "/status/pause"},
                    "urgency": PAUSED_URGENCY})
    if paths.home_override() is None and paths.is_legacy_layout():
        out.append({"id": "core.move_data", "icon": "folder", "tone": "warn",
                    "title": "Move your data into Hal's own folder",
                    "detail": "Hal still reads the data folder inside its code. "
                              "Run: python -m jobhunt import --dry-run",
                    "due": None, "act": None, "urgency": MOVE_DATA_URGENCY})
    return out

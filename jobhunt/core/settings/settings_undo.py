"""Undo in Activity for a setting change.

The settings store writes each change's Activity row itself, with `undo = {"type": "setting", "key", "value"}`;
this handler lets `activity.undo` put the old value back. It goes through `settings.undo`, so the old value is
written as a new change with its own history row, and is checked again against the registry.
"""
from __future__ import annotations

import sqlite3

from jobhunt.core import activity


@activity.undo_handler("setting")
def undo_setting(con: sqlite3.Connection, spec: dict, row: dict) -> str | None:
    """Puts back the value a setting had before the change in `row`; `None` keeps Activity's own words."""
    from jobhunt.core import settings

    settings.undo(spec, session_id=row.get("session_id"))
    return None

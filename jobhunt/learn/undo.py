"""Activity's Undo for "Yes, do this" on one of Hal's suggestions."""
from __future__ import annotations

import sqlite3

from jobhunt.core import activity
from jobhunt.learn import items


@activity.undo_handler("learned_item")
def _undo_learned(con: sqlite3.Connection, spec: dict, row: dict) -> str:
    return items.undo_yes(con, spec)

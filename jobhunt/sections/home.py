"""What the sections of Your record put on Home: the rail's badge on Your record while a sort waits to be checked.

A Home provider like every area's `home.py` (`core/home_routes.py`). A finished sort whose list nobody has
checked yet gave no sign outside Overview, and a graduate did not find **Check the list** (2026-11-09), so the
menu counts the sorts that wait (`sorts.waiting`).
"""
from __future__ import annotations

import sqlite3

from jobhunt.sections import sorts


def sections(con: sqlite3.Connection) -> dict:
    """The rail's count for Your record: the sorts ready and not yet worked through."""
    return {"counts": {"record": len(sorts.waiting(con))}}

"""The setup guide's parts of Home: the "Finish setting up" card and the first search's progress.

The card lists every step not done once the guide was opened, so a skipped step stays on Home until the
person does it. The first search is the one All set starts; while it runs and reports how many
jobs it has to check, Home shows "Checked 40 of 300 jobs" in place of the new matches.
"""
from __future__ import annotations

import json
import sqlite3

from . import state

FIRST_SEARCH_ROW = "done"     # All set's row keeps the first search's action id in its data


def _first_search(con: sqlite3.Connection) -> dict | None:
    row = con.execute("SELECT data FROM setup_steps WHERE id = ?", (FIRST_SEARCH_ROW,)).fetchone()
    try:
        action_id = (json.loads(row["data"]) or {}).get("first_search") if row and row["data"] else None
    except ValueError:
        return None
    if not action_id:
        return None
    a = con.execute("SELECT state, step_n, step_total FROM actions WHERE id = ?", (action_id,)).fetchone()
    if a is None or a["state"] not in ("queued", "running") or not a["step_total"]:
        return None
    return {"checked": a["step_n"], "total": a["step_total"]}


def needs(con: sqlite3.Connection) -> list[dict]:
    """Nothing urgent: what is left of setup is its own card."""
    return []


def sections(con: sqlite3.Connection) -> dict:
    """Home's "setup" card and, while it runs, the first search's progress."""
    out: dict = {}
    left = state.left(con)
    if left:
        total = len(state.LISTED)
        out["setup"] = {"done": total - len(left), "total": total,
                        "todo": [{"step": s.id, "title": s.name, "detail": s.home} for s in left]}
    first = _first_search(con)
    if first:
        out["first_search"] = first
    return out

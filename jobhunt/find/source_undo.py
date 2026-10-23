"""Undo for "You added a job site": the site leaves Where Hal looks; jobs it already found stay in Matches,
where the person can hide any of them as usual."""

from __future__ import annotations

import sqlite3

from jobhunt.core import activity


@activity.undo_handler("source_added")
def undo_source_added(con: sqlite3.Connection, spec: dict, row: dict) -> str | None:
    """Removes the added site; the caller commits."""
    found = con.execute("SELECT name FROM sources WHERE id = ?", (spec.get("id"),)).fetchone()
    if found is None:
        return "That site had already been removed"
    con.execute("DELETE FROM sources WHERE id = ?", (spec["id"],))
    return f"Removed {found['name']} from Where Hal looks"

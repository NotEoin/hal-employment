"""The find area's parts of Home: the new matches and the goal card.

New matches are the best jobs found since yesterday that Matches would show, so Home and Jobs never disagree.
The closing reminders for saved jobs are to-dos (`find.feedback`), which Home lists with the other to-dos.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from jobhunt.core import clock
from jobhunt.find import goal as goal_mod
from jobhunt.find import routes

NEW_MATCHES = 5             # cards on Home
NEW_HOURS = 36              # "new" on Home: found since yesterday morning


def needs(con: sqlite3.Connection) -> list[dict]:
    """Nothing of its own: saved jobs remind through to-dos."""
    return []


def new_matches(con: sqlite3.Connection, limit: int = NEW_MATCHES) -> list[dict]:
    """The best jobs found lately that Matches shows, as JobCards."""
    since = clock.iso(clock.now_utc() - timedelta(hours=NEW_HOURS))
    where, args = routes._shown_where("matches")
    rows = con.execute(f"SELECT j.* FROM jobs j WHERE {where} AND j.first_seen >= ? ORDER BY j.score DESC LIMIT ?",
                       [*args, since, limit]).fetchall()
    return routes.cards(con, rows)


def goal_card(con: sqlite3.Connection) -> dict:
    """The goal card: its rows in words, how hard the person is looking, and the goal version."""
    goal = goal_mod.current()
    return {"rows": [list(r) for r in goal_mod.summary_rows(goal)], "mode": goal.mode, "version": goal.version}


def sections(con: sqlite3.Connection) -> dict:
    """Home's "new_matches" and "goal" sections."""
    return {"new_matches": new_matches(con), "goal": goal_card(con)}

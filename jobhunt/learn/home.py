"""What learning puts on Home and the rail: suggestions waiting for a yes."""
from __future__ import annotations

import sqlite3

from jobhunt.learn import items

URGENCY = 55            # after the to-dos (50): a suggestion can always wait, on Home's scale


def needs(con: sqlite3.Connection) -> list[dict]:
    """One item while suggestions wait, naming the newest as the example."""
    waiting = items.waiting(con)
    if not waiting:
        return []
    n = len(waiting)
    first = waiting[0]["title"].rstrip("?")
    return [{"id": "learn.waiting", "icon": "spark", "tone": "why",
             "title": f"Hal has {n} suggestion{'s' if n != 1 else ''} for you",
             "detail": f"For example: {first[0].lower()}{first[1:]}. Nothing changes until you say yes.",
             "due": "", "act": {"label": "Review", "route": "memory"}, "urgency": URGENCY}]


def sections(con: sqlite3.Connection) -> dict:
    """The rail's count for What Hal remembers."""
    return {"counts": {"memory": items.waiting_count(con)}}

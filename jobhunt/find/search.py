"""Ctrl K results from the find area: jobs by title or employer, and the jobs actions.

An action is an API path the palette POSTs with `{}`, or `dialog:<kind>` to open a dialog (the convention
of `shell/palette.js`).
"""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from jobhunt.find.routes import OPEN

LIMIT = 8                   # jobs offered; the palette shows 30 results in all
ACTIONS = (("Add one advert", "dialog:addjob"), ("Re-check every job against your goal", "/jobs/rescore"))


def search(con: sqlite3.Connection, q: str) -> list[dict]:
    """Open jobs whose title or employer holds the words, best match first, then matching actions."""
    words = q.strip()
    out = []
    if len(words) >= 2:
        like = f"%{words}%"
        rows = con.execute(f"""SELECT id, title, company FROM jobs
                               WHERE active = 1 AND status IN ({','.join('?' * len(OPEN))})
                                 AND (title LIKE ? OR company LIKE ?)
                               ORDER BY hidden_rule IS NOT NULL, score DESC LIMIT ?""",
                           [*OPEN, like, like, LIMIT]).fetchall()
        out = [{"label": f"{r['title']}, {r['company']}", "kind": "Job", "route": "job-" + quote(r["id"], safe="")}
               for r in rows]
    low = words.lower()
    out += [{"label": label, "kind": "Action", "action": action} for label, action in ACTIONS if low in label.lower()]
    return out

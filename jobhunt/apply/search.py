"""Ctrl K results from the Applications area (`search`): applications by job title, employer or number.

Closed applications are left out, as on the board's columns; the Closed list is one page away.
"""
from __future__ import annotations

from .home import _route
from .queries import IN_FLIGHT

LIMIT = 8   # leaves room in the palette's 30 results (core/routes.py SEARCH_LIMIT) for the other areas


def search(con, q: str) -> list[dict]:
    """Applications whose title, employer or number matches `q`."""
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q.lower()}%"
    number = int(q) if q.isdigit() else -1
    marks = ",".join("?" * len(IN_FLIGHT))
    rows = con.execute(f"""SELECT j.id, j.title, j.company FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
                           WHERE j.status IN ({marks})
                             AND (lower(j.title) LIKE ? OR lower(j.company) LIKE ? OR a.number = ?)
                           ORDER BY a.number DESC LIMIT ?""", (*IN_FLIGHT, like, like, number, LIMIT)).fetchall()
    return [{"label": f"{r['title']}, {r['company']}", "kind": "Application", "route": _route(r["id"])} for r in rows]

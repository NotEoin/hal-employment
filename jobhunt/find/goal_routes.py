"""The goal routes: the goal card and editor's values, its versions, and restoring one.

The other goal routes (starters, quiz, chat, saving what Hal understood) arrive with the setup guide's goal
step. Restoring writes the old values as one new change, so it is itself in the history and undoable.
"""

from __future__ import annotations

from jobhunt.core import settings
from jobhunt.find import goal as goal_mod
from jobhunt.web.router import BadRequest, Request, route

VERSIONS = 50               # versions listed; older ones stay in the database


@route("GET", "/api/v2/goal")
def goal(req: Request) -> dict:
    """The goal's values, its rows in words, its version and how hard the person is looking."""
    current = goal_mod.current()
    return {"values": settings.get_many("goal."), "rows": [list(r) for r in goal_mod.summary_rows(current)],
            "version": current.version, "mode": current.mode}


@route("GET", "/api/v2/goal/versions")
def versions(req: Request) -> list[dict]:
    """Every goal version, newest first: when, by whom and what changed."""
    rows = req.con.execute("SELECT id, at, by, summary FROM goal_versions ORDER BY id DESC LIMIT ?",
                           (VERSIONS,)).fetchall()
    return [dict(r) for r in rows]


@route("POST", "/api/v2/goal/versions/{version}/restore")
def restore(req: Request) -> dict:
    """Puts an earlier goal back; answers the new version's number."""
    try:
        version = int(req.params["version"])
    except ValueError as e:
        raise BadRequest("version must be a whole number.", field="version") from e
    return {"version": goal_mod.restore(version)}

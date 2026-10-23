"""The routes of What Hal remembers, Saved answers: list, change and delete saved answers.

The rules live in `answers.saved`; these handlers only turn its errors into the router's replies.
"""
from __future__ import annotations

from ..web.router import BadRequest, Conflict, NotFound, Request, route
from . import saved

PREFIX = "/api/v2/answers"
GONE = "That saved answer is no longer there."


@route("GET", PREFIX)
def listing(req: Request) -> list[dict]:
    """Every saved answer with where it may be used, then the answers Hal works out each time."""
    return saved.listing(req.con)


@route("PUT", PREFIX + "/{answer_id}")
def change(req: Request) -> dict:
    """Changes an answer's words (`a`) or where it may be used (`scope`, `scope_key`)."""
    body = req.body if isinstance(req.body, dict) else {}
    try:
        item = saved.change(req.con, req.params["answer_id"], body)
    except saved.AnswerConflict as e:
        raise Conflict(str(e)) from e
    except saved.AnswerError as e:
        raise BadRequest(str(e)) from e
    if item is None:
        raise NotFound(GONE)
    return item


@route("DELETE", PREFIX + "/{answer_id}")
def delete(req: Request) -> dict:
    """Deletes a saved answer."""
    try:
        found = saved.delete(req.con, req.params["answer_id"])
    except saved.AnswerError as e:
        raise BadRequest(str(e)) from e
    if not found:
        raise NotFound(GONE)
    return {"ok": True}

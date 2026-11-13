"""What Hal remembers: the "Waiting for you" and "Learned" tabs.

Every write here is the person's own answer from the page; patterns only ever add waiting rows.
"""
from __future__ import annotations

from jobhunt.learn import items
from jobhunt.learn import undo as _undo  # noqa: F401 - registers the Undo of "Yes, do this" with the server
from jobhunt.web.router import Request, route

PREFIX = "/api/v2/memory"


@route("GET", PREFIX + "/waiting")
def waiting(req: Request) -> list[dict]:
    """The suggestions waiting for a yes or no."""
    return items.waiting(req.con)


@route("POST", PREFIX + "/waiting/{item_id}")
def decide(req: Request) -> dict:
    """"Yes, do this", "Not now" or "Never suggest this"."""
    decision = str((req.body or {}).get("decision") or "")
    return items.decide(req.con, req.params["item_id"], decision, session_id=req.session_id)


@route("GET", PREFIX + "/learned")
def learned(req: Request) -> list[dict]:
    """What Hal learned, where it came from, and whether it is in use."""
    return items.learned(req.con)


@route("PUT", PREFIX + "/learned/{item_id}")
def switch(req: Request) -> dict:
    """Switches a learned item on or off."""
    body = req.body or {}
    if not isinstance(body.get("on"), bool):
        raise ValueError("Say on or off.")
    return items.switch(req.con, req.params["item_id"], body["on"], session_id=req.session_id)


@route("DELETE", PREFIX + "/learned/{item_id}")
def delete(req: Request) -> dict:
    """Forgets a learned item, putting back what it changed."""
    return items.delete(req.con, req.params["item_id"], session_id=req.session_id)

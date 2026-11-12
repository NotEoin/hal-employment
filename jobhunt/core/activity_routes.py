"""The Activity and actions routes: the Activity page, the Why? drawer, Undo, Stop and Carry on.

Stop and Carry on answer at once: the action stops itself at its next checkpoint, and a carried-on action
starts in the background; both report through the event stream.
"""
from __future__ import annotations

from ..web.router import BadRequest, Request, route
from . import activity, runner


def _int(value: str | None, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError as e:
        raise BadRequest(f"{name} must be a whole number.", field=name) from e


@route("GET", "/api/v2/activity")
def activity_page(req: Request) -> dict:
    """Everything Hal did, newest first, grouped by day."""
    runner.flush()
    limit = _int(req.query.get("limit"), "limit") or activity.PAGE_LIMIT
    return activity.page(req.con, area=req.query.get("area") or None, before=_int(req.query.get("before"), "before"),
                         limit=limit, job_id=req.query.get("job") or None)


@route("GET", "/api/v2/activity/{activity_id}")
def activity_one(req: Request) -> dict:
    """One line, for the Why? drawer."""
    runner.flush()
    return activity.one(req.con, _int(req.params["activity_id"], "activity_id"))


@route("POST", "/api/v2/activity/{activity_id}/undo")
def activity_undo(req: Request) -> dict:
    """Undoes one line; the undo is a line of its own."""
    new_id = activity.undo(_int(req.params["activity_id"], "activity_id"),
                           session_id=req.session.id if req.session else None)
    return {"ok": True, "activity_id": new_id}


@route("GET", "/api/v2/actions")
def actions_list(req: Request) -> list[dict]:
    """Running and interrupted actions, or those in `state`."""
    return runner.listing(req.con, req.query.get("state") or None)


@route("GET", "/api/v2/actions/{action_id}", auth="session_or_local")
def actions_one(req: Request) -> dict:
    """One action and its steps."""
    return runner.get(req.params["action_id"], req.con)


@route("POST", "/api/v2/actions/{action_id}/stop", auth="session_or_local")
def actions_stop(req: Request) -> dict:
    """Stops an action; the work it already did is kept."""
    runner.get(req.params["action_id"], req.con)
    return {"ok": runner.stop(req.params["action_id"])}


@route("POST", "/api/v2/actions/{action_id}/carry-on")
def actions_carry_on(req: Request) -> dict:
    """Starts an interrupted action again from where it was."""
    return {"action_id": runner.carry_on(req.params["action_id"], session_id=req.session.id if req.session else None)}

"""The Messages routes: the list, opening (which marks read), Mark all read, Archive and Check email."""
from __future__ import annotations

from ..core import runner
from ..web.router import BadRequest, Conflict, Request, route
from . import messages

MAIL_SUBJECT = "_mail"   # one email check at a time, whoever starts it


@route("GET", "/api/v2/messages")
def messages_list(req: Request) -> dict:
    """Emails and alerts, newest first, for one filter."""
    f = req.query.get("filter") or "all"
    if f not in messages.FILTERS:
        raise BadRequest(f"The filter must be one of {', '.join(messages.FILTERS)}.", field="filter")
    return {"items": messages.listing(req.con, f)}


@route("POST", "/api/v2/messages/read-all")
def messages_read_all(req: Request) -> dict:
    """Marks every message read."""
    messages.mark_all_read(req.con)
    return {"ok": True}


@route("POST", "/api/v2/messages/{message_id}/read")
def messages_read(req: Request) -> dict:
    """Opening a message marks it read."""
    messages.mark_read(req.con, req.params["message_id"])
    return {"ok": True}


@route("POST", "/api/v2/messages/{message_id}/archive")
def messages_archive(req: Request) -> dict:
    """Archives one message."""
    messages.archive(req.con, req.params["message_id"])
    return {"ok": True}


@route("POST", "/api/v2/mail/check")
def mail_check(req: Request) -> dict:
    """Checks the email now, in the background."""
    try:
        action_id = runner.start("mail.check", subject=MAIL_SUBJECT, why="You pressed Check email",
                                 session_id=req.session.id if req.session else None, remote=req.remote)
    except RuntimeError as e:
        raise Conflict("Hal is already checking your email.") from e
    return {"action_id": action_id}

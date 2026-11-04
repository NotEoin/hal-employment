"""The Applications routes of API v2: the board, the application page and its buttons.

Anything that drives the browser or the AI starts an action and returns its id at once; the page follows
it on the event stream. An application is named by its job id or by its number ("12"). Asking for changes
and rewriting the letter are `docs/write_routes.py`'s.
"""
from __future__ import annotations

from ..web.router import BadRequest, NotHere, Request, route
from . import actions, queries

PREFIX = "/api/v2/applications"
TAB_CLOSED = "The form's tab is closed. Press \"Fill it in again\" to open it."


def _who(req: Request) -> dict:
    """Who asked, so an application keeps the browser of the computer it was started from."""
    return {"session_id": req.session.id if req.session else None,
            "helper_id": req.helper.id if getattr(req, "helper", None) else None, "remote": req.remote}


def _body(req: Request) -> dict:
    if req.body is None:
        return {}
    if not isinstance(req.body, dict):
        raise BadRequest("The request should be a JSON object.")
    return req.body


def _id(req: Request) -> str:
    return queries.resolve(req.con, req.params["app_id"])


def _for_each(req: Request, flag: str, missing: str, start) -> dict:
    """Runs `start(job_id)` for each application in the body's `ids` whose board card has `flag` set.

    Returns {started: {job id: action id}, skipped: [the ids left out]}; a card that changed since the page
    drew it is skipped rather than acted on.
    """
    ids = _body(req).get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise BadRequest(missing, field="ids")
    marked = {c["id"] for col in queries.board(req.con)["columns"] for c in col["items"] if c.get(flag)}
    started, skipped = {}, []
    for ref in ids:
        job_id = queries.resolve(req.con, ref)
        if job_id in marked:
            started[job_id] = start(job_id)
        else:
            skipped.append(ref)
    return {"started": started, "skipped": skipped}


@route("GET", PREFIX)
def board(req: Request) -> dict:
    """The board's columns and the Closed list."""
    return queries.board(req.con)


@route("POST", PREFIX + "/approve-ready")
def approve_ready(req: Request) -> dict:
    """Approves the documents of every application named in `ids` and starts filling each form.

    Only applications the board shows as ready to approve are taken; any other id is left out and listed.
    """
    return _for_each(req, "approvable", "Send the applications to approve.",
                     lambda job_id: actions.approve_documents(req.con, job_id, {}, **_who(req)))


@route("POST", PREFIX + "/remake-missing")
def remake_missing(req: Request) -> dict:
    """Starts Hal writing the documents again for every application named in `ids` whose documents stopped.

    A restart or a failure left queued jobs under Getting ready with no documents (testers, 2026-11-04). Only
    applications the board shows as `remakeable` are taken; any other id is left out and listed.
    """
    return _for_each(req, "remakeable", "Send the applications whose documents to make.",
                     lambda job_id: actions.start("docs.prepare", job_id, **_who(req)))


@route("GET", PREFIX + "/{app_id}")
def application(req: Request) -> dict:
    """One application and its four steps."""
    return queries.application(req.con, req.params["app_id"])


@route("POST", PREFIX + "/{app_id}/documents/approve")
def approve(req: Request) -> dict:
    """Approve and fill in the form."""
    who = _who(req)
    return {"action_id": actions.approve_documents(req.con, _id(req), _body(req), **who)}


@route("PUT", PREFIX + "/{app_id}/documents")
def save_documents(req: Request) -> dict:
    """Keeps edits to the letter, the answers or the message."""
    return actions.save_documents(req.con, _id(req), _body(req))


@route("POST", PREFIX + "/{app_id}/documents/changes/{change_id}")
def change(req: Request) -> dict:
    """Undo or Redo one change Hal made to the CV for this job."""
    on = _body(req).get("on")
    if not isinstance(on, bool):
        raise BadRequest("Say whether the change is on (true) or undone (false).", field="on")
    job_id = _id(req)
    actions.set_change(req.con, job_id, req.params["change_id"], on)
    return {"cv": queries.application(req.con, job_id)["docs"]["cv"]}


@route("POST", PREFIX + "/{app_id}/documents/redo")
def redo(req: Request) -> dict:
    """Make the documents again."""
    return {"action_id": actions.start("docs.prepare", _id(req), **_who(req))}


@route("POST", PREFIX + "/{app_id}/documents/reopen")
def reopen(req: Request) -> dict:
    """Change the documents: back to the Documents step, to approve them again."""
    try:
        actions.reopen_documents(req.con, _id(req))
    except ValueError as e:
        raise BadRequest(str(e)) from e
    return {"ok": True}


@route("POST", PREFIX + "/{app_id}/form/fill")
def fill(req: Request) -> dict:
    """Fill it in again: from the advert (`fresh`), or from the page the job's tab shows now (`here`)."""
    body = _body(req)
    params = {"fresh": bool(body.get("fresh")), "here": bool(body.get("here"))}
    return {"action_id": actions.start("apply.fill", _id(req), params=params, **_who(req))}


@route("POST", PREFIX + "/{app_id}/form/edits")
def edits(req: Request) -> dict:
    """Put my changes in the form."""
    raw = _body(req).get("edits")
    if not isinstance(raw, list):
        raise BadRequest("Send the changed answers as a list.", field="edits")
    changed = [{"hid": str(e.get("hid")), "value": e.get("value"), "skip": bool(e.get("skip")), "edited": True}
               for e in raw if isinstance(e, dict) and e.get("hid") is not None]
    return {"action_id": actions.start("apply.apply_edits", _id(req), params={"edits": changed}, **_who(req))}


@route("POST", PREFIX + "/{app_id}/form/next")
def next_page(req: Request) -> dict:
    """Carry on to the next page (on the last page, hand the form over)."""
    return {"action_id": actions.start("apply.next_page", _id(req), **_who(req))}


@route("POST", PREFIX + "/{app_id}/form/draft-rest")
def draft_rest(req: Request) -> dict:
    """Ask Hal to answer the rest."""
    return {"action_id": actions.start("apply.draft_rest", _id(req), **_who(req))}


@route("POST", PREFIX + "/{app_id}/form/show")
def show(req: Request) -> dict:
    """Show me the form: brings its tab to the front, on the computer the person is using."""
    from .. import browser

    job_id = _id(req)
    if req.remote:
        return _show_on_this_computer(job_id)
    if not browser.show(job_id):
        raise RuntimeError("The form's tab is closed. Press \"Fill it in again\" to open it.")
    return {"ok": True}


def _show_on_this_computer(job_id: str) -> dict:
    """The remote branch: Hal's own window is not offered from another computer, so the form is in a tab of
    the browser helper paired in that computer's browser, and that helper brings it forward."""
    from .. import extension

    if not extension.connected():
        raise NotHere("The browser helper in this browser is not connected. Open Hal in the browser that has "
                      "the helper, then try again.")
    try:
        shown = extension.show(job_id)
    except RuntimeError as e:
        if "closed" in str(e):
            raise RuntimeError(TAB_CLOSED) from e
        raise RuntimeError("The browser helper did not answer. Check the browser on this computer is still "
                           "open.") from e
    if not shown:
        raise RuntimeError(TAB_CLOSED)
    return {"ok": True}


@route("POST", PREFIX + "/{app_id}/sent")
def sent(req: Request) -> dict:
    """I sent it myself."""
    return {"column": actions.sent_myself(req.con, _id(req))}


@route("POST", PREFIX + "/{app_id}/discard")
def discard(req: Request) -> dict:
    """Discard, with one of the reasons the dialog offers."""
    body = _body(req)
    reason = body.get("reason") or "skipped_for_now"
    if reason not in actions.DISCARD_REASONS:
        raise BadRequest("Choose one of the reasons offered.", field="reason")
    actions.discard(req.con, _id(req), reason, str(body.get("words") or ""))
    return {"ok": True}


@route("POST", PREFIX + "/{app_id}/restore")
def restore(req: Request) -> dict:
    """Back to Matches."""
    actions.restore(req.con, _id(req))
    return {"ok": True}


@route("POST", PREFIX + "/{app_id}/status")
def status(req: Request) -> dict:
    """Change status by hand."""
    body = _body(req)
    return {"column": actions.change_status(req.con, _id(req), str(body.get("status") or ""),
                                            str(body.get("words") or ""))}


@route("POST", PREFIX + "/{app_id}/note")
def note(req: Request) -> dict:
    """Add a note."""
    actions.add_note(req.con, _id(req), str(_body(req).get("text") or ""))
    return {"ok": True}

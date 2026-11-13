"""The Applications routes of API v2: the board, the application page and its buttons.

Anything that drives the browser or the AI starts an action and returns its id at once; the page follows
it on the event stream. An application is named by its job id or by its number ("12"). Asking for changes
and rewriting the letter are `docs/write_routes.py`'s. The application's CV is edited through `/cv`, with
the same change list and 409 as My CV's editor (`cvstudio/tailor.py`).
"""
from __future__ import annotations

from jobhunt.apply import actions, queries
from jobhunt.core import activity
from jobhunt.cvstudio import edit, store, tailor
from jobhunt.cvstudio.model import CVError
from jobhunt.web.router import BadRequest, Conflict, NotFound, NotHere, Request, json_body, route, who

PREFIX = "/api/v2/applications"
NOW = 0   # runner priority for a button the person just pressed on one application and is watching
TAB_CLOSED = "The form's tab is closed. Press \"Fill it in again\" to open it."
OLDER_CV = ("Hal made this application's CV before CVs could be edited line by line. Use Undo on Hal's changes, "
            "or edit your main CV on My CV.")
COPY_WHY = "You pressed Copy to my main CV on an application's CV"


def _id(req: Request) -> str:
    return queries.resolve(req.con, req.params["app_id"])


def _for_each(req: Request, flag: str, missing: str, start) -> dict:
    """Runs `start(job_id)` for each application in the body's `ids` whose board card has `flag` set.

    Returns {started: {job id: action id}, skipped: [the ids left out]}; a card that changed since the page
    drew it is skipped rather than acted on.
    """
    ids = json_body(req).get("ids")
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
                     lambda job_id: actions.approve_documents(req.con, job_id, {}, **who(req)))


@route("POST", PREFIX + "/remake-missing")
def remake_missing(req: Request) -> dict:
    """Starts Hal writing the documents again for every application named in `ids` whose documents stopped.

    A restart or a failure left queued jobs under Getting ready with no documents (testers, 2026-11-04). Only
    applications the board shows as `remakeable` are taken; any other id is left out and listed.
    """
    return _for_each(req, "remakeable", "Send the applications whose documents to make.",
                     lambda job_id: actions.start("docs.prepare", job_id, **who(req)))


@route("GET", PREFIX + "/{app_id}")
def application(req: Request) -> dict:
    """One application and its four steps."""
    return queries.application(req.con, req.params["app_id"])


@route("POST", PREFIX + "/{app_id}/documents/approve")
def approve(req: Request) -> dict:
    """Approve and fill in the form."""
    return {"action_id": actions.approve_documents(req.con, _id(req), json_body(req), **who(req), priority=NOW)}


@route("PUT", PREFIX + "/{app_id}/documents")
def save_documents(req: Request) -> dict:
    """Keeps edits to the letter, the answers or the message."""
    return actions.save_documents(req.con, _id(req), json_body(req), by_you=True)


@route("POST", PREFIX + "/{app_id}/documents/changes/{change_id}")
def change(req: Request) -> dict:
    """Undo or Redo one change Hal made to the CV for this job."""
    on = json_body(req).get("on")
    if not isinstance(on, bool):
        raise BadRequest("Say whether the change is on (true) or undone (false).", field="on")
    job_id = _id(req)
    actions.set_change(req.con, job_id, req.params["change_id"], on)
    return {"cv": queries.application(req.con, job_id)["docs"]["cv"]}


def _kept(req: Request) -> str:
    """The application's job id; 404 when its CV was made before it could be edited line by line."""
    job_id = _id(req)
    if not tailor.kept(job_id, req.con):
        raise NotFound(OLDER_CV)
    return job_id


@route("GET", PREFIX + "/{app_id}/cv")
def job_cv(req: Request) -> dict:
    """The application's CV for Edit your CV, with Hal's changes and yours marked apart."""
    return tailor.editor(_kept(req), req.con)


@route("PATCH", PREFIX + "/{app_id}/cv")
def save_job_cv(req: Request) -> dict:
    """Saves Edit your CV's changes to the application's CV, made on `base`, and makes its PDF again."""
    body = json_body(req)
    base = str(body.get("base") or "")
    if not base:
        raise BadRequest("Say which copy of this job's CV you edited.", field="base")
    job_id = _kept(req)
    try:
        tailor.save_edits(job_id, base, edit.changes_from(body.get("changes")), req.con)
    except store.StaleVersion as e:
        raise Conflict(str(e)) from e
    except CVError as e:
        raise BadRequest(str(e), field=str(e).split(":", 1)[0]) from e
    made = actions.save_documents(req.con, job_id, {})
    return {**tailor.editor(job_id, req.con), "form_out_of_date": made["form_out_of_date"]}


@route("POST", PREFIX + "/{app_id}/cv/copy")
def copy_to_main(req: Request) -> dict:
    """Copy to my main CV: one entry's lines from the application's CV, as one new version of the main CV."""
    body = json_body(req)
    base, target = str(body.get("base") or ""), str(body.get("target") or "")
    if not base or not target:
        raise BadRequest("Say which entry to copy and which version of your main CV it goes on.",
                         field="target" if base else "base")
    job_id = _kept(req)
    try:
        vid = tailor.copy_to_main(job_id, target, base, req.con, dict(queries.load_job(req.con, job_id)))
    except store.StaleVersion as e:
        raise Conflict("Your main CV changed since this page opened. Nothing was copied.") from e
    except CVError as e:
        raise BadRequest(str(e).split(": ", 1)[-1].capitalize() + ".", field="target") from e
    n = store.version(vid, con=req.con)["n"]
    activity.record("cv", "cv.version", f"Saved your CV as version {n}: lines copied from your CV for a job",
                    COPY_WHY, icon="file", job_id=job_id)
    return {"version_id": vid, "n": n}


@route("POST", PREFIX + "/{app_id}/documents/redo")
def redo(req: Request) -> dict:
    """Make the documents again."""
    return {"action_id": actions.start("docs.prepare", _id(req), **who(req))}


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
    body = json_body(req)
    params = {"fresh": bool(body.get("fresh")), "here": bool(body.get("here"))}
    return {"action_id": actions.start("apply.fill", _id(req), params=params, **who(req), priority=NOW)}


@route("POST", PREFIX + "/{app_id}/form/edits")
def edits(req: Request) -> dict:
    """Put my changes in the form."""
    raw = json_body(req).get("edits")
    if not isinstance(raw, list):
        raise BadRequest("Send the changed answers as a list.", field="edits")
    changed = [{"hid": str(e.get("hid")), "value": e.get("value"), "skip": bool(e.get("skip")), "edited": True}
               for e in raw if isinstance(e, dict) and e.get("hid") is not None]
    return {"action_id": actions.start("apply.apply_edits", _id(req), params={"edits": changed}, **who(req))}


@route("POST", PREFIX + "/{app_id}/form/next")
def next_page(req: Request) -> dict:
    """Carry on to the next page (on the last page, hand the form over)."""
    return {"action_id": actions.start("apply.next_page", _id(req), **who(req))}


@route("POST", PREFIX + "/{app_id}/form/draft-rest")
def draft_rest(req: Request) -> dict:
    """Ask Hal to answer the rest."""
    return {"action_id": actions.start("apply.draft_rest", _id(req), **who(req))}


@route("POST", PREFIX + "/{app_id}/form/show")
def show(req: Request) -> dict:
    """Show me the form: brings its tab to the front, on the computer the person is using."""
    from jobhunt import browser

    job_id = _id(req)
    if req.remote:
        return _show_on_this_computer(job_id)
    if not browser.show(job_id):
        raise Conflict("The form's tab is closed. Press \"Fill it in again\" to open it.")
    return {"ok": True}


def _show_on_this_computer(job_id: str) -> dict:
    """The remote branch: Hal's own window is not offered from another computer, so the form is in a tab of
    the browser helper paired in that computer's browser, and that helper brings it forward."""
    from jobhunt import extension

    if not extension.connected():
        raise NotHere("The browser helper in this browser is not connected. Open Hal in the browser that has "
                      "the helper, then try again.")
    try:
        shown = extension.show(job_id)
    except RuntimeError as e:
        if "closed" in str(e):
            raise Conflict(TAB_CLOSED) from e
        raise Conflict("The browser helper did not answer. Check the browser on this computer is still "
                           "open.") from e
    if not shown:
        raise Conflict(TAB_CLOSED)
    return {"ok": True}


@route("POST", PREFIX + "/{app_id}/sent")
def sent(req: Request) -> dict:
    """I sent it myself."""
    return {"column": actions.sent_myself(req.con, _id(req))}


@route("POST", PREFIX + "/{app_id}/discard")
def discard(req: Request) -> dict:
    """Discard, with one of the reasons the dialog offers."""
    body = json_body(req)
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
    body = json_body(req)
    return {"column": actions.change_status(req.con, _id(req), str(body.get("status") or ""),
                                            str(body.get("words") or ""))}


@route("POST", PREFIX + "/{app_id}/note")
def note(req: Request) -> dict:
    """Add a note."""
    actions.add_note(req.con, _id(req), str(json_body(req).get("text") or ""))
    return {"ok": True}

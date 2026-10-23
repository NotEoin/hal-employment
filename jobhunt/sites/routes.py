"""The routes of notes about job sites and "Something wrong here?".

Listing and changing notes read and write only the notes tables, so each answers at once; turning a report
into a note may wait on the AI, so `POST /sites/report` starts `sites.propose_note` and returns its id.
"""
from __future__ import annotations

from jobhunt.sites import cards, report
from jobhunt.sites import notes as N
from jobhunt.web.router import BadRequest, Request, route


def _session(req: Request) -> str | None:
    return req.session.id if req.session else None


def _note(req: Request, note_id: str) -> dict:
    found = next((s for s in N.stored(req.con) if s.note.id == note_id), None)
    if found is None:
        raise LookupError("That note is not in Hal's list.")
    return cards.note_dict(found)


@route("GET", "/api/v2/sites")
def sites(req: Request) -> list[dict]:
    """Every site's card with its notes, where each came from, and any notes that disagree."""
    return cards.listing(req.con)


@route("POST", "/api/v2/sites/report")
def report_problem(req: Request) -> dict:
    """"Something wrong here?": keeps the report and starts drafting a note ({job_id, site, what, words})."""
    body = req.body or {}
    try:
        return report.start(req.con, job_id=body.get("job_id") or None, site=str(body.get("site") or ""),
                            what=str(body.get("what") or ""), words=str(body.get("words") or ""),
                            session_id=_session(req), remote=req.remote)
    except ValueError as e:
        raise BadRequest(str(e), field="words") from e


def _rid(req: Request) -> int:
    try:
        return int(req.params["rid"])
    except ValueError as e:
        raise LookupError("That report is not in Hal's list.") from e


@route("POST", "/api/v2/sites/reports/{rid}/save")
def save_note(req: Request) -> dict:
    """Saves the drafted note for this site, its application system or every site ({scope, text})."""
    body = req.body or {}
    try:
        return report.save(req.con, _rid(req), str(body.get("scope") or "site"), str(body.get("text") or ""))
    except N.NoteError as e:
        raise BadRequest(str(e), field="text") from e


@route("POST", "/api/v2/sites/reports/{rid}/next")
def what_now(req: Request) -> dict:
    """Try again with this note, I'll finish it myself, or Skip this job for now ({choice})."""
    body = req.body or {}
    return report.next_step(req.con, _rid(req), str(body.get("choice") or ""), session_id=_session(req),
                            remote=req.remote)


@route("POST", "/api/v2/sites/notes")
def add_note(req: Request) -> dict:
    """Adds a note in the person's own words for one site ({site, text, kind?})."""
    body = req.body or {}
    site = str(body.get("site") or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if not site:
        raise BadRequest("Say which site the note is for.", field="site")
    kind = str(body.get("kind") or "note")
    if kind != "note":
        raise BadRequest("A note you add by hand is shown to you and given to the AI; use Something wrong here? "
                         "for a note Hal acts on.", field="kind")
    key = N.site_key("https://" + site)
    try:
        note_id = N.add("tenant" if key != N.registrable("https://" + site) else "site", key, "note", {}, {},
                        str(body.get("text") or ""), origin="you", con=req.con)
    except N.NoteError as e:
        raise BadRequest(str(e), field="text") from e
    return {"id": note_id}


@route("PUT", "/api/v2/sites/notes/{note_id}")
def change_note(req: Request) -> dict:
    """Switches a note ({on}), rewords it ({text}) or says yes to one Hal suggested ({accept: true})."""
    body = req.body or {}
    note_id = req.params["note_id"]
    try:
        if body.get("accept") is True:
            N.accept(note_id, con=req.con)
        elif "on" in body:
            if not isinstance(body["on"], bool):
                raise BadRequest("on must be true or false.", field="on")
            N.switch(note_id, body["on"], con=req.con)
        elif "text" in body:
            N.edit(note_id, str(body["text"]), con=req.con)
        else:
            raise BadRequest("Send on, text or accept.", field="on")
    except N.NoteError as e:
        raise BadRequest(str(e), field="text") from e
    return _note(req, note_id)


@route("DELETE", "/api/v2/sites/notes/{note_id}")
def delete_note(req: Request) -> None:
    """Deletes a note you or Hal added; built-in notes can only be switched off."""
    N.delete(req.params["note_id"], con=req.con)


@route("GET", "/api/v2/sites/notes/{note_id}/share")
def share_note(req: Request) -> dict:
    """"Hal note for <site>: <text>", to send to whoever looks after Hal."""
    return {"text": N.share_text(req.params["note_id"], con=req.con)}

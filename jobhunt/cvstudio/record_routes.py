"""Routes for Your record's CVs as My CVs (`record_cvs`).

- `POST /api/v2/record/sources/{id}/use` {"on": true|false}: "Use this for applications" on a CV of the record.
  It answers the source as the record lists it, with "cv" (the CV of My CVs, or null while a new one is read)
  and "action_id" (the `cv.parse` reading the file, or null). A POST, so the page can follow the reading with
  `run` as My CVs' upload does. A file Hal cannot read, a source that is no CV file, and the last CV in use
  are 400 with plain words; a reading of the same file already under way is 409.

At start the record's lead CV is settled once (`record_cvs.settle_lead`), so an install that kept the old
"current CV" choice has its cards worked out again for the CV in use.
"""
from __future__ import annotations

import logging

from jobhunt import db
from jobhunt.core import plugins
from jobhunt.cvstudio import parse, record_cvs
from jobhunt.cvstudio.model import CVError
from jobhunt.sections import store as sources
from jobhunt.web.router import BadRequest, Conflict, Request, json_body, route

log = logging.getLogger(__name__)

PREFIX = "/api/v2/record/sources"


@route("POST", PREFIX + "/{source_id}/use")
def use(req: Request) -> dict:
    """Turns "Use this for applications" on or off for a CV of the record."""
    on = json_body(req).get("on")
    if not isinstance(on, bool):
        raise BadRequest("Say whether to use this CV for applications: true or false.", field="on")
    try:
        out = record_cvs.use(req.con, req.params["source_id"], on, session_id=req.session_id, remote=req.remote)
    except (record_cvs.NotUsable, parse.CVReadError, CVError) as e:
        raise BadRequest(str(e)) from e
    except RuntimeError as e:
        raise Conflict(str(e)) from e
    return {**sources.get(req.con, req.params["source_id"]), **out}


@plugins.on_start
def settle_at_start() -> None:
    """Lets the record's lead CV follow the CVs in use once, at start."""
    con = db.connect()
    try:
        record_cvs.settle_lead(con)
    finally:
        con.close()

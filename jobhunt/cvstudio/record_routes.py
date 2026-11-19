"""Routes for Your record's CVs as My CVs (`record_cvs`).

- `POST /api/v2/record/sources/{id}/use` {"on": true|false}: "Use this for applications" on a CV of the record.
  It answers the source as the record lists it, with "cv" (the CV of My CVs, or null while a new one is read)
  and "action_id" (the `cv.parse` reading the file, or null). A POST, so the page can follow the reading with
  `run` as My CVs' upload does. A file Hal cannot read, a source that is no CV file, and the last CV in use
  are 400 with plain words; a reading of the same file already under way is 409.
- `GET .../{id}/preview`: {"version_id", "original", "url", "why", "original_url"}, what the Preview dialog
  shows: the CV of My CVs read from this file (its version in use, for `DocPreview`, or its own file's pages
  while it sends that file, `original`), else Hal's quick reading at `url`, or
  the reader's reason (`why`) and the original file. `.../preview.html` is that quick reading as the page Hal
  prints (400 with the reason when the file cannot be read); its fonts are served beside it, as for the CV
  previews (`look_routes`). `.../original` sends the file as a download, never shown inline, as a record CV
  may be any file.

At start the record's lead CV is settled once (`record_cvs.settle_lead`), so an install that kept the old
"current CV" choice has its cards worked out again for the CV in use.
"""
from __future__ import annotations

import logging
import mimetypes

from jobhunt import db
from jobhunt.core import plugins
from jobhunt.cvstudio import parse, record_cvs, render
from jobhunt.cvstudio.model import CVError
from jobhunt.sections import store as sources
from jobhunt.web.router import (
    BadRequest,
    BytesResponse,
    Conflict,
    FileResponse,
    HtmlResponse,
    NotFound,
    Request,
    json_body,
    route,
)

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


@route("GET", PREFIX + "/{source_id}/preview")
def preview(req: Request) -> dict:
    """How to preview a CV of the record: {"version_id", "original", "url", "why", "original_url"}
    (`record_cvs.preview`)."""
    try:
        return record_cvs.preview(req.con, req.params["source_id"])
    except record_cvs.NotUsable as e:
        raise BadRequest(str(e)) from e


@route("GET", PREFIX + "/{source_id}/preview.html")
def preview_page(req: Request) -> HtmlResponse:
    """Hal's quick reading of a CV of the record, as the page Hal prints, in the default look."""
    try:
        page = record_cvs.preview_html(req.con, req.params["source_id"])
    except (record_cvs.NotUsable, parse.CVReadError) as e:
        raise BadRequest(str(e)) from e
    return HtmlResponse(page, policy=render.POLICY)


@route("GET", PREFIX + "/{source_id}/fonts/{name}")
def preview_font(req: Request) -> FileResponse:
    """One of the CV fonts, for the preview page's relative `fonts/<file>` links."""
    for f in render.font_files():
        if f.name == req.params["name"]:
            return FileResponse(f, f.name, "font/woff2")
    raise NotFound("There is no such font.")


@route("GET", PREFIX + "/{source_id}/original")
def original(req: Request) -> BytesResponse:
    """A CV of the record's own file, byte for byte, to open when Hal cannot preview it."""
    try:
        _, data, name = record_cvs.source_file(req.con, req.params["source_id"])
    except record_cvs.NotUsable as e:
        raise NotFound(str(e)) from e
    return BytesResponse(data, name, mimetypes.guess_type(name)[0] or "application/octet-stream", download=True)


@plugins.on_start
def settle_at_start() -> None:
    """Lets the record's lead CV follow the CVs in use once, at start."""
    con = db.connect()
    try:
        record_cvs.settle_lead(con)
    finally:
        con.close()

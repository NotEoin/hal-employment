"""Routes for adding a CV and checking what Hal read.

- `POST /cv/upload` takes one file (multipart) or `{text}` and makes a new CV from it. With `?cv=<id>` it is
  "Upload a new version of this CV" instead: the reading becomes that CV's next version. The cheap checks
  (size, kind, pages, a PDF that is only a picture) answer at once with words that say what to do; the file
  is then stored unchanged and `cv.parse` reads it in the background. A file whose bytes already made a CV
  starts nothing: the answer names that CV (`duplicate`), so the page selects it instead of making a copy.
  A file being read already answers with that reading's `action_id` (`parse_actions.start_read`), and 409
  when that reading goes into another CV. A file also goes into Your record's CVs (once: the same file there
  already is left as it is), so the record knows the CVs the person has.
- `GET /cv/parse-report` is the "Check what Hal read" dialog for one CV: the original on the left (page
  pictures of a PDF, the paragraphs of a Word file or pasted text) and Hal's reading of the CV's newest file
  on the right.
- `PUT /cv/parse-report/{path}` corrects one field and makes a version "You corrected N things Hal misread".
  A correction goes onto the CV's current copy, so suggestions accepted since the upload are kept.
- `GET /cv/original` sends the CV's file byte for byte; the store re-checks its SHA-256 on every read.
- Every route takes the CV as `?cv=<id>` (`routes.cv_of`), the lead CV when it is left out.
"""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import quote

from jobhunt.core import settings
from jobhunt.cvstudio import parse, pdfread, store
from jobhunt.cvstudio.model import CVError
from jobhunt.cvstudio.parse_actions import fill_empty_settings, start_read
from jobhunt.cvstudio.routes import cv_of
from jobhunt.web.router import BadRequest, BytesResponse, Conflict, NotFound, route

PAGE_SCALE = 1.5          # page pictures at 108 dots per inch: readable beside Hal's reading, quick to make
PASTED_NAME = store.PASTED_NAME
NO_REPORT_WORDS = "Hal has not read a CV yet. Add your CV first."
CORRECTED_WHAT = "You corrected {n} Hal misread"


def _upload(req) -> tuple[bytes, str]:
    if req.files:
        if len(req.files) > 1:
            raise BadRequest("Add one CV at a time.")
        upload = next(iter(req.files.values()))
        return upload.data, upload.name or "CV"
    text = str((req.body or {}).get("text") or "") if isinstance(req.body, dict) else ""
    if not text.strip():
        raise BadRequest(parse.EMPTY_WORDS, field="text")
    return text.encode("utf-8"), PASTED_NAME


@route("POST", "/api/v2/cv/upload")
def upload(req) -> dict:
    """Stores the file unchanged and starts reading it into a new CV, or into `?cv=` as its next version; a file
    Hal cannot read is refused with the reason, and one that already made a CV is answered with that CV."""
    into = cv_of(req) if req.query.get("cv") else None
    data, name = _upload(req)
    try:
        parse.check(data, name)
    except parse.CVReadError as e:
        raise BadRequest(str(e), field="file") from e
    file_id = store.add_file(data, name, role="baseline")
    had = store.cv_for_file(file_id, con=req.con)
    if had is not None and (into is None or had == into):
        cv = store.doc(req.con, had)
        return {"action_id": None, "file_id": file_id,
                "duplicate": {"id": had, "name": cv["name"], "archived": cv["archived"]}}
    try:
        action_id = start_read(file_id, name, doc_id=into, session_id=req.session_id, remote=req.remote)
    except RuntimeError as e:
        raise Conflict(str(e)) from e
    if req.files:
        _to_record(req, data, name)
    return {"action_id": action_id, "file_id": file_id, "duplicate": None}


def _to_record(req, data: bytes, name: str) -> None:
    """Adds the CV file to Your record's CVs and starts reading it, unless it is there already: a person who gave
    Hal their CV in setup found Your record saying "No CVs yet" (2026-11-12)."""
    from jobhunt.sections import actions, pipeline

    added = pipeline.add_uploads(req.con, "cv", [pipeline.Incoming(name, data)])["added"]
    actions.queue(added, session_id=req.session_id, remote=req.remote, why="You added your CV")


def latest(con: sqlite3.Connection, doc_id: str | None = None) -> sqlite3.Row | None:
    """The newest reading's report row of one CV (the lead CV by default), with its version number and file."""
    doc_id = doc_id or store.lead_id(con)
    return con.execute("""SELECT r.version_id, r.file_id, r.found, r.items, v.n,
                                 f.name, f.mime, f.pages, f.sha256, f.added
                          FROM cv_parse_reports r JOIN cv_versions v ON v.id = r.version_id
                          LEFT JOIN cv_files f ON f.id = r.file_id
                          WHERE v.doc_id = ? ORDER BY v.n DESC LIMIT 1""", (doc_id,)).fetchone()


def _original(row: sqlite3.Row) -> dict:
    data = store.file_bytes(row["file_id"])
    kind = parse.kind_of(row["name"] or "", data)
    out: dict = {"kind": kind, "name": row["name"], "pages": row["pages"], "page_urls": [], "paragraphs": []}
    if kind == "pdf":
        out["page_urls"] = [f"/api/v2/cv/original/page/{n}" for n in range(1, (row["pages"] or 1) + 1)]
    else:
        out["paragraphs"] = parse.preview_paragraphs(parse.lines_of(data, kind))
    return out


def _from_cv(con: sqlite3.Connection, version_id: str) -> dict[str, str]:
    """The `you.*` values that still hold what the CV said, for the "From your CV" chips."""
    values = parse.prefill(store.get(version_id))
    return {k: v for k, v in values.items() if str(settings.get(k) or "") == v}


@route("GET", "/api/v2/cv/parse-report")
def report(req) -> dict:
    """What Hal read from the CV's newest file, row by row, beside the original."""
    row = latest(req.con, cv_of(req))
    if row is None:
        # An empty report, not a 404: the setup step and My CV ask for it before any CV is added.
        return {"version_id": None, "n": None, "found": "", "rows": [], "unsure": 0, "original_preview_url": None,
                "original": None, "file": None, "from_cv": {}}
    original = _original(row)
    query = f"?cv={quote(req.query['cv'])}" if req.query.get("cv") else ""
    original["page_urls"] = [u + query for u in original["page_urls"]]
    items = json.loads(row["items"])
    preview = original["page_urls"][0] if original["page_urls"] else "/api/v2/cv/original" + query
    return {"version_id": row["version_id"], "n": row["n"], "found": row["found"], "rows": items,
            "unsure": sum(1 for r in items if not r["sure"]), "original_preview_url": preview,
            "original": original, "file": {"id": row["file_id"], "name": row["name"], "pages": row["pages"],
                                           "added": row["added"], "sha256": row["sha256"]},
            "from_cv": _from_cv(req.con, row["version_id"])}


@route("PUT", "/api/v2/cv/parse-report/{path}")
def correct(req) -> dict:
    """Corrects one field Hal misread, in a new version; the report row is marked as corrected."""
    body = req.body if isinstance(req.body, dict) else {}
    if not isinstance(body.get("value"), str):
        raise BadRequest("Type what your CV should say.", field="value")
    value = body["value"].strip()
    doc_id = cv_of(req)
    row = latest(req.con, doc_id)
    if row is None:
        raise NotFound(NO_REPORT_WORDS)
    path = req.params["path"]
    items = json.loads(row["items"])
    item = next((i for i in items if i["path"] == path), None)
    if item is None:
        raise NotFound(parse.GONE_WORDS)
    current_id, doc = store.current(doc_id=doc_id)
    try:
        fixed = parse.correct(doc, path, value)
    except LookupError:
        current_id, fixed = row["version_id"], parse.correct(store.get(row["version_id"]), path, value)
    if path.startswith("contact/"):
        _follow_setting(req.con, row["version_id"], path, item["value"], value)
    item.update(value=value, sure=True, why="", corrected=True)
    n = sum(1 for i in items if i.get("corrected"))
    things = "1 thing" if n == 1 else f"{n} things"
    version_id = store.new_version(fixed, origin="corrected", what=CORRECTED_WHAT.format(n=things), by="you",
                                   parent=current_id, doc_id=doc_id)
    req.con.execute("UPDATE cv_parse_reports SET items = ? WHERE version_id = ?",
                    (json.dumps(items, ensure_ascii=True), row["version_id"]))
    req.con.commit()
    return {"version_id": version_id, "corrected": n, "n": store.version(version_id)["n"]}


def _follow_setting(con: sqlite3.Connection, version_id: str, path: str, old: str, new: str) -> None:
    """A contact setting that still holds what Hal misread takes the correction too."""
    key = f"you.{path.split('/', 1)[1]}"
    if key not in {"you.name", "you.email", "you.phone", "you.city"}:
        return
    if str(settings.get(key) or "") == old:
        settings.set(key, new, by="you")
    else:
        fill_empty_settings({key: new})


def _file_id(doc_id: str | None) -> str:
    if doc_id is None:
        raise NotFound("Hal has no file of your CV. Add your CV first.")
    doc = store.doc(doc_id=doc_id)
    if doc.get("baseline_file_id"):
        return doc["baseline_file_id"]
    from jobhunt import db

    con = db.connect()
    try:
        row = latest(con, doc_id)
    finally:
        con.close()
    if row is None or not row["file_id"]:
        raise NotFound("Hal has no file of your CV. Add your CV first.")
    return row["file_id"]


def _file_row(file_id: str) -> sqlite3.Row:
    from jobhunt import db

    con = db.connect()
    try:
        row = con.execute("SELECT name, mime, pages FROM cv_files WHERE id = ?", (file_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise NotFound("Hal has no file of your CV. Add your CV first.")
    return row


@route("GET", "/api/v2/cv/original")
def original(req) -> BytesResponse:
    """Your original file, byte for byte."""
    file_id = _file_id(cv_of(req))
    row = _file_row(file_id)
    try:
        data = store.file_bytes(file_id)
    except CVError as e:  # the store raises CVError when the kept file's SHA-256 no longer matches
        raise Conflict(str(e)) from e
    return BytesResponse(data, row["name"], row["mime"] or "application/octet-stream", download=True)


@route("GET", "/api/v2/cv/original/page/{n}")
def page(req) -> BytesResponse:
    """One page of your original PDF as a picture."""
    try:
        number = int(req.params["n"])
    except ValueError as e:
        raise NotFound("There is no such page.") from e
    file_id = _file_id(cv_of(req))
    data = store.file_bytes(file_id)
    if parse.kind_of(_file_row(file_id)["name"] or "", data) != "pdf":
        raise NotFound("Your CV is not a PDF, so it has no page pictures.")
    try:
        png = pdfread.page_png(data, number, scale=PAGE_SCALE)
    except IndexError as e:
        raise NotFound("There is no such page.") from e
    return BytesResponse(png, f"page-{number}.png", "image/png")

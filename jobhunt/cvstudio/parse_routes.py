"""Routes for adding a CV and checking what Hal read.

- `POST /cv/upload` takes one file (multipart) or `{text}`. The cheap checks (size, kind, pages, a PDF
  that is only a picture) answer at once with words that say what to do; the file is then stored
  unchanged and `cv.parse` reads it in the background.
- `GET /cv/parse-report` is the "Check what Hal read" dialog: the original on the left (page pictures of a
  PDF, the paragraphs of a Word file or pasted text) and Hal's reading on the right.
- `PUT /cv/parse-report/{path}` corrects one field and makes a version "You corrected N things Hal misread".
  A correction goes onto Hal's current copy, so suggestions accepted since the upload are kept.
- `GET /cv/original` sends the file byte for byte; the store re-checks its SHA-256 on every read.
"""
from __future__ import annotations

import json
import sqlite3

from jobhunt.core import runner, settings
from jobhunt.web.router import BadRequest, BytesResponse, Conflict, NotFound, route

from . import parse, pdfread, store
from .parse_actions import fill_empty_settings

PAGE_SCALE = 1.5          # page pictures at 108 dots per inch: readable beside Hal's reading, quick to make
PASTED_NAME = "Pasted CV.txt"
NO_REPORT_WORDS = "Hal has not read a CV yet. Add your CV first."
CORRECTED_WHAT = "You corrected {n} Hal misread"


def _session(req) -> str | None:
    return getattr(req.session, "id", None)


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
    """Stores the file unchanged and starts reading it; a file Hal cannot read is refused with the reason."""
    data, name = _upload(req)
    try:
        parse.check(data, name)
    except parse.CVReadError as e:
        raise BadRequest(str(e), field="file") from e
    file_id = store.add_file(data, name, role="baseline")
    action_id = runner.start("cv.parse", subject="cv.parse", params={"file_id": file_id, "file": name},
                             replace=True, session_id=_session(req), remote=req.remote)
    return {"action_id": action_id, "file_id": file_id}


def latest(con: sqlite3.Connection) -> sqlite3.Row | None:
    """The newest reading's report row, with its version number and file."""
    return con.execute("""SELECT r.version_id, r.file_id, r.found, r.items, v.n,
                                 f.name, f.mime, f.pages, f.sha256, f.added
                          FROM cv_parse_reports r JOIN cv_versions v ON v.id = r.version_id
                          LEFT JOIN cv_files f ON f.id = r.file_id
                          ORDER BY v.n DESC LIMIT 1""").fetchone()


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
    """What Hal read from the newest CV file, row by row, beside the original."""
    row = latest(req.con)
    if row is None:
        # An empty report, not a 404: the setup step and My CV ask for it before any CV is added.
        return {"version_id": None, "n": None, "found": "", "rows": [], "unsure": 0, "original_preview_url": None,
                "original": None, "file": None, "from_cv": {}}
    original = _original(row)
    items = json.loads(row["items"])
    preview = original["page_urls"][0] if original["page_urls"] else "/api/v2/cv/original"
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
    row = latest(req.con)
    if row is None:
        raise NotFound(NO_REPORT_WORDS)
    path = req.params["path"]
    items = json.loads(row["items"])
    item = next((i for i in items if i["path"] == path), None)
    if item is None:
        raise NotFound(parse.GONE_WORDS)
    current_id, doc = store.current()
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
                                   parent=current_id)
    req.con.execute("UPDATE cv_parse_reports SET items = ? WHERE version_id = ?",
                    (json.dumps(items, ensure_ascii=True), row["version_id"]))
    req.con.commit()
    return {"version_id": version_id, "corrected": n}


def _follow_setting(con: sqlite3.Connection, version_id: str, path: str, old: str, new: str) -> None:
    """A contact setting that still holds what Hal misread takes the correction too."""
    key = f"you.{path.split('/', 1)[1]}"
    if key not in {"you.name", "you.email", "you.phone", "you.city"}:
        return
    if str(settings.get(key) or "") == old:
        settings.set(key, new, by="you")
    else:
        fill_empty_settings({key: new})


def _file_id() -> str:
    doc = store.doc()
    if doc.get("baseline_file_id"):
        return doc["baseline_file_id"]
    from jobhunt import db

    con = db.connect()
    try:
        row = latest(con)
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
    file_id = _file_id()
    row = _file_row(file_id)
    try:
        data = store.file_bytes(file_id)
    except RuntimeError as e:
        raise Conflict(str(e)) from e
    return BytesResponse(data, row["name"], row["mime"] or "application/octet-stream", download=True)


@route("GET", "/api/v2/cv/original/page/{n}")
def page(req) -> BytesResponse:
    """One page of your original PDF as a picture."""
    try:
        number = int(req.params["n"])
    except ValueError as e:
        raise NotFound("There is no such page.") from e
    file_id = _file_id()
    data = store.file_bytes(file_id)
    if parse.kind_of(_file_row(file_id)["name"] or "", data) != "pdf":
        raise NotFound("Your CV is not a PDF, so it has no page pictures.")
    try:
        png = pdfread.page_png(data, number, scale=PAGE_SCALE)
    except IndexError as e:
        raise NotFound("There is no such page.") from e
    return BytesResponse(png, f"page-{number}.png", "image/png")

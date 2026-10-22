"""The Documents and files routes: every application's files, Copy, Download all, Open the folder.

An application is named in these paths by its number (`/documents/412`) or its job id; both are stable. Files
are served only through the registry (`docs.files`), never by a path from the request. An HTML file is sent
with the server's strict page policy, so a saved advert can never run a script inside Hal.
"""
from __future__ import annotations

import mimetypes
import re
import sqlite3
from datetime import datetime

from ..web.router import BytesResponse, FileResponse, HtmlResponse, NotFound, Request, route
from . import details, folders
from . import files as registry

# Board columns by job status; "applying" splits by the application's stage.
COLUMN_TITLES = {"ready": "Getting ready", "form": "Form filled", "sent": "Sent", "interview": "Interview",
                 "offer": "Offer", "closed": "Closed"}
CLOSED = ("rejected", "withdrawn", "closed", "skipped")
# What the page shows for each kind of file: name, and the buttons it offers.
KIND_WORDS = {
    "cv_pdf": ("CV (PDF)", ("open", "download")),
    "cv_docx": ("CV (Word)", ("download",)),
    "letter_pdf": ("Cover letter (PDF)", ("open", "download", "copy")),
    "letter_txt": ("Cover letter", ("copy", "download")),
    "answers_md": ("Answers to the form's questions", ("copy",)),
    "message_txt": ("Message to the hiring manager", ("copy",)),
    "advert_html": ("The advert, saved", ("open",)),
    "prep_md": ("Interview preparation", ("open", "download")),
}
ORDER = tuple(KIND_WORDS)
# The text behind each Copy button, by the kind the page asks for.
TEXT_KINDS = {"letter": "letter_txt", "answers": "answers_md", "message": "message_txt", "prep": "prep_md"}
CHIP = {"cv_pdf": "CV", "cv_docx": "CV", "letter_pdf": "Letter", "letter_txt": "Letter", "answers_md": "Answers",
        "message_txt": "Message", "prep_md": "Interview"}
ZIP_LEAVES_OUT = (folders.PACK_MD, folders.MANIFEST, "cv.json", "match.json")


def column(status: str, stage: str | None) -> str:
    """The board column for a job status and application stage."""
    if status == "applying":
        return "ready" if (stage or "documents") == "documents" else "form"
    if status == "applied":
        return "sent"
    if status in ("assessment", "interview"):
        return "interview"
    if status == "offer":
        return "offer"
    if status in CLOSED:
        return "closed"
    return "ready"


APP_SQL = """SELECT a.job_id, a.number, a.stage, a.folder, a.approved_at, a.cv_version_id, a.cv_sent_as, a.docs,
                    j.id, j.title, j.company, j.status, j.description, j.url
               FROM applications a JOIN jobs j ON j.id = a.job_id"""


def find_app(con: sqlite3.Connection, app_id: str) -> sqlite3.Row:
    """One application by number or job id; NotFound when there is none."""
    if re.fullmatch(r"\d{1,9}", app_id):
        row = con.execute(APP_SQL + " WHERE a.number = ?", (int(app_id),)).fetchone()
    else:
        row = con.execute(APP_SQL + " WHERE a.job_id = ?", (app_id,)).fetchone()
    if row is None:
        raise NotFound("That application is not in Hal's list.")
    return row


def _folder(con: sqlite3.Connection, row: sqlite3.Row):
    return folders.folder_for(con, row, create=False)


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _texts(con: sqlite3.Connection, job_id: str) -> dict[str, str]:
    return {r["kind"]: r["text"] or "" for r in con.execute(
        "SELECT kind, text FROM app_documents WHERE job_id = ? AND text IS NOT NULL", (job_id,))}


def _file_ids(con: sqlite3.Connection, row: sqlite3.Row, folder) -> dict[str, str]:
    """File ids by name for the folder's listed files, registering any that are not registered yet."""
    listed = folders.listed_files(folder)
    rel = folder.resolve().relative_to(registry.root_dir(folders.ROOT).resolve()).as_posix()
    known = registry.ids_under(con, folders.ROOT, rel)
    if any(p.name not in known for _, p in listed):
        folders.register_folder(con, row, folder, approved_at=row["approved_at"])
        con.commit()
        return _file_ids(con, row, folder)
    return known


def _sub(kind: str, path, texts: dict, row) -> str:
    if kind == "cv_pdf":
        return path.name
    if kind in ("letter_pdf", "letter_txt"):
        n = _words(texts.get("letter_txt", ""))
        return f"{n} words" if n else path.name
    if kind == "answers_md":
        n = texts.get("answers_md", "").count("**") // 2
        return f"{n} answer" + ("" if n == 1 else "s")
    if kind == "message_txt":
        return f"{_words(texts.get('message_txt', ''))} words"
    if kind == "advert_html":
        return _saved_words(path)
    return path.name


def _saved_words(path) -> str:
    when = datetime.fromtimestamp(path.stat().st_mtime)
    return f"Saved {when.day} {when.strftime('%b')}"


@route("GET", "/api/v2/documents")
def documents_list(req: Request) -> dict:
    """Every application with documents, newest number first; `q` searches title and employer."""
    q = (req.query.get("q") or "").strip().lower()
    rows = req.con.execute(APP_SQL + " WHERE a.number IS NOT NULL ORDER BY a.number DESC").fetchall()
    kinds: dict[str, list[str]] = {}
    for r in req.con.execute("SELECT job_id, kind FROM app_documents"):
        chip = CHIP.get(r["kind"])
        if chip and chip not in kinds.setdefault(r["job_id"], []):
            kinds[r["job_id"]].append(chip)
    out = []
    for r in rows:
        if q and q not in f"{r['title']} {r['company']}".lower():
            continue
        col = column(r["status"], r["stage"])
        out.append({"id": r["number"], "number": r["number"], "job_id": r["job_id"], "title": r["title"],
                    "org": r["company"], "column": col, "column_title": COLUMN_TITLES[col],
                    "kinds": kinds.get(r["job_id"], [])})
    return {"apps": out}


@route("GET", "/api/v2/documents/details")
def documents_details(req: Request) -> dict:
    """Copy-paste details from your profile; never private answers."""
    return {"rows": details.rows()}


@route("GET", "/api/v2/documents/{app_id}")
def documents_one(req: Request) -> dict:
    """One application's files, with the text behind each Copy button."""
    row = find_app(req.con, req.params["app_id"])
    folder = _folder(req.con, row)
    texts = _texts(req.con, row["job_id"])
    files = []
    if folder is not None and folder.is_dir():
        ids = _file_ids(req.con, row, folder)
        listed = folders.listed_files(folder)
        has_letter_pdf = any(k == "letter_pdf" for k, _ in listed)
        for kind, p in sorted(listed, key=lambda kp: (ORDER.index(kp[0]), kp[1].name)):
            if kind == "letter_txt" and has_letter_pdf:
                continue
            name, acts = KIND_WORDS[kind]
            text_kind = "letter" if kind == "letter_pdf" else next((t for t, k in TEXT_KINDS.items() if k == kind),
                                                                       None)
            files.append({"kind": kind, "name": name, "sub": _sub(kind, p, texts, row), "file_id": ids.get(p.name),
                          "file_name": p.name, "has_text": bool(text_kind and texts.get(TEXT_KINDS[text_kind])),
                          "text_kind": text_kind, "acts": list(acts)})
    col = column(row["status"], row["stage"])
    return {"id": row["number"], "number": row["number"], "job_id": row["job_id"], "title": row["title"],
            "org": row["company"], "column": col, "column_title": COLUMN_TITLES[col],
            "cv": {"version_n": None, "type": None, "look": None, "sent_as": row["cv_sent_as"]},
            "approved_at": row["approved_at"],
            "folder_words": str(folder) if folder is not None else "",
            "files": files, "answers_text": texts.get("answers_md", ""), "message_text": texts.get("message_txt", ""),
            "sent_myself_ok": row["status"] == "applying"}


@route("GET", "/api/v2/documents/{app_id}/text/{kind}")
def documents_text(req: Request) -> dict:
    """The text for one Copy button: letter, answers, message or prep."""
    row = find_app(req.con, req.params["app_id"])
    kind = TEXT_KINDS.get(req.params["kind"])
    if kind is None:
        raise NotFound("Hal keeps no text of that kind.")
    text = _texts(req.con, row["job_id"]).get(kind)
    if text is None:
        folder = _folder(req.con, row)
        path = folder / folders.TEXT_FILES[kind] if folder is not None else None
        if path is None or not path.is_file():
            raise NotFound("This application has no text of that kind yet.")
        text = path.read_text(encoding="utf-8", errors="replace")
    return {"text": text}


@route("GET", "/api/v2/documents/{app_id}/zip")
def documents_zip(req: Request) -> BytesResponse:
    """Every file of one application, in one zip."""
    row = find_app(req.con, req.params["app_id"])
    folder = _folder(req.con, row)
    if folder is None or not folder.is_dir():
        raise NotFound("This application has no files yet.")
    return BytesResponse(registry.zip_folder(folder, leave_out=ZIP_LEAVES_OUT), f"{folder.name}.zip",
                         "application/zip", download=True)


@route("POST", "/api/v2/documents/{app_id}/open-folder", local_only=True)
def documents_open_folder(req: Request) -> dict:
    """Opens the application's folder on the computer Hal runs on."""
    row = find_app(req.con, req.params["app_id"])
    folder = _folder(req.con, row)
    if folder is None or not folder.is_dir():
        raise NotFound("This application has no folder yet.")
    registry.open_folder(folder)
    return {"ok": True}


@route("GET", "/api/v2/files/{file_id}")
def file_one(req: Request):
    """Any registered file: shown in the browser, or downloaded with `download=1`."""
    path, _row = registry.path_of(req.con, req.params["file_id"])
    download = req.query.get("download") == "1"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime == "text/html" and not download:
        return HtmlResponse(path.read_text(encoding="utf-8", errors="replace"))
    if mime.startswith("text/") or path.suffix.lower() == ".md":
        mime = ("text/markdown" if path.suffix.lower() == ".md" else mime) + "; charset=utf-8"
    return FileResponse(path, path.name, mime, download=download)

"""Application folders: one numbered folder per application, holding exactly what was approved.

`<applications folder>/<NNNN> <Company> - <Title>`: the number comes from `applications.number`, so two jobs
with the same company and title never share a folder again (the old packs did). The folder's name
is stored in `applications.folder` when it is first made, so a later change to the job's title or company
never moves it.

Approval writes the whole folder into a hidden sibling and swaps it into place, so the folder never holds a
mix of an old letter and a new CV. `pack.md` stays in the folder because `python -m jobhunt pack` prints its
path and the voice bridge reads it; the Documents page does not list it.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..core import clock, naming, paths
from . import files as registry

log = logging.getLogger(__name__)

ROOT = "applications"
MANIFEST = "manifest.json"
PACK_MD = "pack.md"
TOKEN_BYTES = 4
# Files written from the approved text, by kind (migration 0009's app_documents kinds).
TEXT_FILES = {"letter_txt": "letter.txt", "answers_md": "answers.md", "message_txt": "message.txt",
              "prep_md": "prep.md", "advert_html": "advert.html"}
NOT_LISTED = (PACK_MD, MANIFEST, "cv.json", "match.json")
JOB_ID_LINE = re.compile(r"Job id: `([^`]+)`")


def number_for(con: sqlite3.Connection, job_id: str, *, create: bool = True) -> int | None:
    """The application's number; with `create`, starts the application and gives it the next one."""
    row = con.execute("SELECT number FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if row and row["number"] is not None:
        return int(row["number"])
    if not create:
        return None
    con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                (job_id, clock.iso()))
    con.execute("""UPDATE applications SET number = (SELECT coalesce(max(number), 0) + 1 FROM applications)
                   WHERE job_id = ? AND number IS NULL""", (job_id,))
    return int(con.execute("SELECT number FROM applications WHERE job_id = ?", (job_id,)).fetchone()[0])


def folder_name(con: sqlite3.Connection, job, *, create: bool = True) -> str | None:
    """The folder's name inside the applications folder, fixed the first time it is asked for."""
    row = con.execute("SELECT folder FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
    if row and row["folder"]:
        return row["folder"]
    number = number_for(con, job["id"], create=create)
    if number is None:
        return None
    name = naming.folder_name(number, job["company"] or "", job["title"] or "")
    if create:
        # Reads never write, so a page that lists applications cannot wait on another process's write.
        con.execute("UPDATE applications SET folder = ? WHERE job_id = ?", (name, job["id"]))
    return name


def folder_for(con: sqlite3.Connection, job, *, create: bool = True) -> Path | None:
    """The application's folder (not made on disk); None without `create` for a job not yet started."""
    name = folder_name(con, job, create=create)
    return paths.applications_dir() / name if name else None


def job_id_in_pack(pack_md: Path) -> str | None:
    """The job id an old `pack.md` names in its header (L pack.py:209-237), or None."""
    try:
        head = pack_md.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return None
    m = JOB_ID_LINE.search(head)
    return m.group(1) if m else None


def legacy_folder(job) -> Path | None:
    """The old shared pack folder for `job`, only when its `pack.md` says it belongs to this job."""
    from .. import pack

    # pack.PACKS is the legacy packs folder, or the folder a test pointed it at.
    folder = pack._live("PACKS") / pack.slug(f"{job['company']}-{job['title']}")
    owner = job_id_in_pack(folder / PACK_MD)
    return folder if owner == job["id"] else None


def answers_md(answers: list[dict]) -> str:
    """The form answers as Markdown: each question in bold, then its answer."""
    parts = []
    for a in answers or []:
        q, text = (a.get("question") or a.get("q") or "").strip(), (a.get("answer") or a.get("a") or "").strip()
        if q or text:
            parts.append(f"**{q}**\n\n{text}\n")
    return "\n".join(parts)


def _texts(con: sqlite3.Connection, job, docs: dict) -> dict[str, str]:
    row = con.execute("SELECT prep FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
    message = docs.get("message") or ""
    if docs.get("message_to"):
        message = f"To: {docs['message_to']}\n\n{message}"
    return {"letter_txt": docs.get("letter") or "", "answers_md": answers_md(docs.get("answers") or []),
            "message_txt": message, "prep_md": (row["prep"] if row else None) or ""}


def _advert_html(job) -> str:
    import html

    body = html.escape(job["description"] or "").replace("\n", "<br>\n")
    title = html.escape(f"{job['title']} at {job['company']}")
    link = html.escape(job["url"] or "")
    return (f"<!doctype html>\n<meta charset=\"utf-8\">\n<title>{title}</title>\n<h1>{title}</h1>\n"
            f"<p><a href=\"{link}\">{link}</a></p>\n<div>{body}</div>\n")


def read_manifest(folder: Path) -> dict | None:
    """The folder's manifest, or None when it has none or it cannot be read."""
    try:
        return json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _swap(tmp: Path, dest: Path) -> None:
    old = dest.parent / f".{dest.name}.old-{secrets.token_hex(TOKEN_BYTES)}"
    had = dest.exists()
    if had:
        dest.rename(old)
    try:
        tmp.rename(dest)
    except OSError:
        if had:
            old.rename(dest)
        raise
    if had:
        shutil.rmtree(old, ignore_errors=True)


def publish(con: sqlite3.Connection, job, docs: dict, *, render: Callable[[Path], dict] | None = None,
            approved_at: str | None = None, cv_version_id: str | None = None) -> Path:
    """Writes the application's folder from the approved `docs` and registers every file; returns the folder.

    `render(out_dir)` makes the CV and letter PDFs in `out_dir` and returns `{"cv": path, "letter": path}`
    for the ones it made. `approved_at` defaults to the time in `docs["approved"]`; without either, the
    folder is written but the application is not marked approved (the move of old folders). The caller
    commits.
    """
    dest = folder_for(con, job)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.new-{secrets.token_hex(TOKEN_BYTES)}"
    if approved_at is None and isinstance(docs.get("approved"), str):
        approved_at = docs["approved"]
    try:
        if dest.exists():
            shutil.copytree(dest, tmp)
        else:
            tmp.mkdir()
            legacy = legacy_folder(job)
            if legacy is not None and (legacy / PACK_MD).exists():
                shutil.copy2(legacy / PACK_MD, tmp / PACK_MD)
        made = _named(tmp, render(tmp) if render else {})
        texts = _texts(con, job, docs)
        for kind, name in TEXT_FILES.items():
            if kind == "advert_html":
                if not (tmp / name).exists() and job["description"]:
                    (tmp / name).write_text(_advert_html(job), encoding="utf-8")
                continue
            if texts.get(kind, "").strip():
                (tmp / name).write_text(texts[kind].rstrip() + "\n", encoding="utf-8")
            else:
                (tmp / name).unlink(missing_ok=True)
        pdfs = {"cv_pdf": made.get("cv"), "letter_pdf": made.get("letter")}
        listed = _listed(tmp, pdfs)
        manifest = {"job_id": job["id"], "number": number_for(con, job["id"]), "company": job["company"],
                    "title": job["title"],
                    "files": [{"kind": k, "name": p.name, "sha256": registry.sha256(p),
                               "cv_version": cv_version_id if k == "cv_pdf" else None, "approved_at": approved_at}
                              for k, p in listed],
                    "sent": _sent(con, job["id"]), "updated": clock.iso()}
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _swap(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    register_folder(con, job, dest, approved_at=approved_at, texts=texts, cv_version_id=cv_version_id)
    con.execute("""UPDATE applications SET approved_at = coalesce(?, approved_at), docs_need_redraft = 0
                   WHERE job_id = ?""", (approved_at, job["id"]))
    return dest


def _named(folder: Path, made: dict) -> dict:
    """Gives the PDFs the file names employers see (`cvdocs.file_name`), whatever their maker called them."""
    out = {}
    for kind, made_path in made.items():
        if not made_path or not Path(made_path).exists():
            continue
        want = folder / naming.file_name(kind)
        if Path(made_path) != want:
            Path(made_path).replace(want)
        out[kind] = str(want)
    return out


def _sent(con: sqlite3.Connection, job_id: str) -> dict | None:
    row = con.execute("SELECT sent_how, sent_at FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return {"how": row["sent_how"], "at": row["sent_at"]} if row and row["sent_at"] else None


def _kind_of(path: Path, pdfs: dict) -> str | None:
    for kind, made in pdfs.items():
        if made and Path(made).name == path.name:
            return kind
    for kind, name in TEXT_FILES.items():
        if path.name == name:
            return kind
    if path.suffix.lower() == ".pdf":
        low = path.name.lower()
        return "letter_pdf" if "letter" in low else "cv_pdf"
    if path.suffix.lower() == ".docx":
        return "cv_docx"
    return None


def _listed(folder: Path, pdfs: dict) -> list[tuple[str, Path]]:
    out = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.name not in NOT_LISTED and not p.name.startswith("."):
            kind = _kind_of(p, pdfs)
            if kind:
                out.append((kind, p))
    return out


def register_folder(con: sqlite3.Connection, job, folder: Path, *, approved_at: str | None = None,
                    texts: dict | None = None, cv_version_id: str | None = None) -> list[str]:
    """Registers every listed file of an application folder in `files` and `app_documents`; returns the ids."""
    base = paths.applications_dir().resolve()
    rel = folder.resolve().relative_to(base).as_posix()
    ids = []
    now = clock.iso()
    pdfs = {}
    manifest = read_manifest(folder) or {}
    for f in manifest.get("files", []):
        if f.get("kind") in ("cv_pdf", "letter_pdf"):
            pdfs[f["kind"]] = folder / f["name"]
    for kind, p in _listed(folder, pdfs):
        file_id = registry.register(con, ROOT, p, kind, job_id=job["id"], label=p.name)
        ids.append(file_id)
        text = (texts or {}).get(kind)
        if text is None and kind in TEXT_FILES and kind != "advert_html":
            text = p.read_text(encoding="utf-8", errors="replace")
        con.execute("""INSERT INTO app_documents (job_id, kind, file_id, text, cv_version_id, approved_at, updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(job_id, kind) DO UPDATE SET file_id = excluded.file_id, text = excluded.text,
                           cv_version_id = coalesce(excluded.cv_version_id, app_documents.cv_version_id),
                           approved_at = coalesce(excluded.approved_at, app_documents.approved_at),
                           updated = excluded.updated""",
                    (job["id"], kind, file_id, text, cv_version_id if kind == "cv_pdf" else None, approved_at, now))
    registry.forget_under(con, ROOT, rel, keep=set(ids))
    return ids


def listed_files(folder: Path) -> list[tuple[str, Path]]:
    """The files the Documents page lists for a folder, by kind (never `pack.md` or the manifest)."""
    if not folder.is_dir():
        return []
    pdfs = {}
    for f in (read_manifest(folder) or {}).get("files", []):
        if f.get("kind") in ("cv_pdf", "letter_pdf"):
            pdfs[f["kind"]] = folder / f["name"]
    return _listed(folder, pdfs)

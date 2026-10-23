"""Moving the old shared pack folders into numbered application folders.

Old folder names came from company and title only, so one folder could hold the documents of two jobs. The
job id in each `pack.md` header says which job owns the folder; that job gets a numbered folder with its
PDFs under the new names and its edited texts (from `applications.docs`, never the first draft when an edit
exists). Every other job that mapped to the same old folder is marked "needs making again", because the files
there were written for someone else's advert. The old folders are only read, never changed, and `_base-cv` and
`_built-cv` previews are left behind because they are made again.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..core import activity, clock, naming
from . import folders

log = logging.getLogger(__name__)

# Statuses whose documents are still in use: an application started, or documents ready to approve.
LIVE = ("ready", "applying")
WHY_REDRAFT = "an older job used the same folder"


def _slug(job) -> str:
    from .. import pack

    return pack.slug(f"{job['company']}-{job['title']}")


def _pdf_kind(path: Path) -> str | None:
    if path.suffix.lower() != ".pdf":
        return None
    return "letter" if "letter" in path.name.lower() else "cv"


def _copy_pdfs(source: Path) -> Callable[[Path], dict]:
    def copy(out: Path) -> dict:
        made = {}
        for p in sorted(source.iterdir()):
            kind = _pdf_kind(p)
            if kind and kind not in made:
                target = out / naming.file_name(kind)
                shutil.copy2(p, target)
                made[kind] = str(target)
        cv_json = source / "cv.json"
        if cv_json.is_file():
            shutil.copy2(cv_json, out / "cv.json")
        return made
    return copy


def _docs(con: sqlite3.Connection, job) -> dict:
    row = con.execute("SELECT docs FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
    if row and row["docs"]:
        try:
            return json.loads(row["docs"])
        except ValueError:
            log.warning("the stored documents of %s are not JSON; using the first draft", job["id"])
    from .. import documents

    return documents.drafts(job)


def run(con: sqlite3.Connection, report: Callable[[str], None] = lambda text: None) -> dict:
    """Moves every old pack folder that a job owns; returns counts. Safe to run again: moved ones are skipped."""
    from .. import pack

    source = pack.PACKS
    counts = {"moved": 0, "redraft": 0, "skipped": 0}
    if not source.is_dir():
        report("No old document folders to move")
        return counts
    jobs = {r["id"]: r for r in con.execute(
        """SELECT j.* FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status IN (?, ?) OR a.job_id IS NOT NULL""", LIVE)}
    by_slug: dict[str, list[str]] = {}
    for job_id, job in jobs.items():
        by_slug.setdefault(_slug(job), []).append(job_id)
    for folder in sorted(p for p in source.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        sharing = by_slug.get(folder.name, [])
        owner = folders.job_id_in_pack(folder / folders.PACK_MD)
        if owner is None and len(sharing) == 1:
            owner = sharing[0]
        job = jobs.get(owner) if owner else None
        if job is None:
            counts["skipped"] += 1
            continue
        dest = folders.folder_for(con, job)
        if folders.read_manifest(dest) is None:
            report(f"Moving the documents for {job['title']} at {job['company']}")
            row = con.execute("SELECT approved_at FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
            folders.publish(con, job, _docs(con, job), render=_copy_pdfs(folder),
                            approved_at=row["approved_at"] if row else None)
            if not (dest / folders.PACK_MD).exists() and (folder / folders.PACK_MD).exists():
                shutil.copy2(folder / folders.PACK_MD, dest / folders.PACK_MD)
            counts["moved"] += 1
        for other in sharing:
            if other == job["id"] or jobs[other]["status"] not in LIVE:
                continue
            counts["redraft"] += _mark_redraft(con, jobs[other])
        con.commit()
    return counts


def _mark_redraft(con: sqlite3.Connection, job) -> int:
    con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                (job["id"], clock.iso()))
    done = con.execute("SELECT docs_need_redraft FROM applications WHERE job_id = ?", (job["id"],)).fetchone()[0]
    if done:
        return 0
    con.execute("UPDATE applications SET docs_need_redraft = 1 WHERE job_id = ?", (job["id"],))
    activity.record("documents", "docs.relayout_packs",
                    f"Documents for {job['title']} need making again, because {WHY_REDRAFT}",
                    f"Hal moved your documents into one folder per application; {WHY_REDRAFT}",
                    job_id=job["id"], icon="file", tone="warn", con=con)
    return 1

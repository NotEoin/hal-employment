"""Stand-in for `jobhunt.cvstudio.store`, installed only while the real module is missing.

It works on the real `cv_documents` and `cv_versions` tables of migration 0011, so the suggestion tests and G9
exercise the same rows the real store writes. A version made for one job (`job_id` set) never becomes
Hal's main copy; the real store keeps that rule too.
"""
from __future__ import annotations

import json
import uuid

from jobhunt import db
from jobhunt.core import clock, events

DOC_ID = "main"


def _model():
    from jobhunt.cvstudio import model

    return model


def _connect():
    return db.connect()


def doc() -> dict:
    """The `cv_documents` row, created on first use."""
    con = _connect()
    try:
        row = con.execute("SELECT * FROM cv_documents WHERE id = ?", (DOC_ID,)).fetchone()
        if row is None:
            now = clock.iso()
            con.execute("INSERT INTO cv_documents (id, route, send, type_id, look_id, created, updated) "
                        "VALUES (?, 'improve', 'hal_copy', 'humanities_grad', 'classic', ?, ?)", (DOC_ID, now, now))
            con.commit()
            row = con.execute("SELECT * FROM cv_documents WHERE id = ?", (DOC_ID,)).fetchone()
        return dict(row)
    finally:
        con.close()


def current() -> tuple[str, object]:
    """(version id, document) of Hal's copy; LookupError when there is no CV yet."""
    vid = doc()["current_version_id"]
    if not vid:
        raise LookupError("There is no CV in Hal yet.")
    return vid, get(vid)


def get(version_id: str):
    """One version's document."""
    con = _connect()
    try:
        row = con.execute("SELECT data FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise LookupError("That version is not in Hal.")
    return _model().from_json(row["data"])


def new_version(cv, *, origin: str, what: str, by: str, parent: str | None = None, job_id: str | None = None,
                type_id: str | None = None, file_id: str | None = None) -> str:
    """Saves `cv` as the next version and, unless it is for one job, makes it Hal's copy."""
    row = doc()
    vid = uuid.uuid4().hex[:12]
    con = _connect()
    try:
        n = con.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM cv_versions WHERE doc_id = ?", (DOC_ID,)).fetchone()[0]
        con.execute("INSERT INTO cv_versions (id, doc_id, n, parent_id, origin, type_id, look_id, job_id, file_id, "
                    "data, what, by, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (vid, DOC_ID, n, parent, origin, type_id or row["type_id"], row["look_id"], job_id, file_id,
                     _model().to_json(cv), what, by, clock.iso()))
        if job_id is None:
            con.execute("UPDATE cv_documents SET current_version_id = ?, updated = ? WHERE id = ?",
                        (vid, clock.iso(), DOC_ID))
        con.commit()
    finally:
        con.close()
    events.publish("cv", what=what, id=vid)
    return vid


def use(version_id: str) -> None:
    """Makes an earlier version Hal's copy again."""
    con = _connect()
    try:
        con.execute("UPDATE cv_documents SET current_version_id = ? WHERE id = ?", (version_id, DOC_ID))
        con.commit()
    finally:
        con.close()


def layout() -> dict:
    """Sections and locked lines."""
    return json.loads(doc()["layout"] or "{}")


def employers_get() -> tuple[str, str]:
    """The "Employers get" line and its sub-line."""
    row = doc()
    if row["send"] == "original":
        return ("Employers get: your original file, unchanged",
                "Adjusting for each job is off while you send your original.")
    con = _connect()
    try:
        n = con.execute("SELECT n FROM cv_versions WHERE id = ?", (row["current_version_id"],)).fetchone()
    finally:
        con.close()
    return (f"Employers get: Hal's copy of your CV, version {n[0] if n else 1}",
            "Every change in it was accepted by you.")

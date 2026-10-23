"""Stand-in for `jobhunt.cvstudio.store`, for the CV type tests while the real store is missing.

It keeps versions in the real `cv_documents` and `cv_versions` tables of migration 0011, so what the type code
writes next to them (`cv_documents.type_id`, `cv_checks`) is exercised for real. `install()` registers it
only when the real module cannot be imported.
"""
from __future__ import annotations

import importlib
import sys
import types
import uuid

from jobhunt import db
from jobhunt.core import events

REAL = "jobhunt.cvstudio.store"
DOC_ID = "main"
FIRST_TYPE = "general_grad"
FIRST_LOOK = "classic"


def _model():
    from jobhunt.cvstudio import model

    return model


def doc() -> dict:
    """The `cv_documents` row, made on first use."""
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM cv_documents WHERE id = ?", (DOC_ID,)).fetchone()
        if row is None:
            now = db.now()
            con.execute("INSERT INTO cv_documents (id, route, send, type_id, look_id, created, updated)"
                        " VALUES (?, 'improve', 'hal_copy', ?, ?, ?, ?)", (DOC_ID, FIRST_TYPE, FIRST_LOOK, now, now))
            con.commit()
            row = con.execute("SELECT * FROM cv_documents WHERE id = ?", (DOC_ID,)).fetchone()
        return dict(row)
    finally:
        con.close()


def current():
    """(version id, document) of Hal's copy; LookupError when there is none."""
    vid = doc()["current_version_id"]
    if not vid:
        raise LookupError("There is no CV in Hal yet.")
    return vid, get(vid)


def get(version_id: str):
    """One version's document."""
    con = db.connect()
    try:
        row = con.execute("SELECT data FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise LookupError(f"There is no version {version_id}.")
    return _model().from_json(row["data"])


def new_version(cv, *, origin: str, what: str, by: str, parent: str | None = None, job_id: str | None = None,
                type_id: str | None = None, file_id: str | None = None) -> str:
    """Saves `cv` as the next version and makes it current."""
    row = doc()
    vid = "v-" + uuid.uuid4().hex[:12]
    con = db.connect()
    try:
        n = con.execute("SELECT coalesce(max(n), 0) + 1 FROM cv_versions WHERE doc_id = ?", (DOC_ID,)).fetchone()[0]
        con.execute("INSERT INTO cv_versions (id, doc_id, n, parent_id, origin, type_id, look_id, job_id, file_id,"
                    " data, what, by, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (vid, DOC_ID, n, parent or row["current_version_id"], origin, type_id or row["type_id"],
                     row["look_id"], job_id, file_id, _model().to_json(cv), what, by, db.now()))
        if job_id is None:
            con.execute("UPDATE cv_documents SET current_version_id = ?, updated = ? WHERE id = ?",
                        (vid, db.now(), DOC_ID))
        con.commit()
    finally:
        con.close()
    events.publish("cv", version_id=vid)
    return vid


def install() -> types.ModuleType:
    """The real store when it exists, else this stub registered under its name."""
    try:
        return importlib.import_module(REAL)
    except ModuleNotFoundError:
        mod = sys.modules[__name__]
        sys.modules[REAL] = mod
        import jobhunt.cvstudio as pkg

        pkg.store = mod
        return mod

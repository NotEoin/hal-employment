"""Stand-in for the CV model and store, used while `cvstudio/model.py` and
`cvstudio/store.py` are missing.

`install()` registers `jobhunt.cvstudio.model` and `jobhunt.cvstudio.store` only when the real modules are
missing, so the same tests run against the real code once it exists. The store keeps its rows in the real
`cv_documents`, `cv_versions` and `cv_files` tables (migration 0011) and its files under
`paths.cv_dir()/files`, so what CV reading writes looks the same as it will with the real store.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import stat
import sys
import types
import uuid
from dataclasses import dataclass, field
from typing import Any

MODEL = "jobhunt.cvstudio.model"
STORE = "jobhunt.cvstudio.store"
SCHEMA = 2
READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH


@dataclass
class Bullet:
    """A line of an entry."""

    id: str
    text: str
    sources: list[str] = field(default_factory=list)
    locked: bool = False


@dataclass
class Module:
    """A module and its mark."""

    name: str
    mark: str = ""
    year: str = ""


@dataclass
class Entry:
    """One job, qualification, project or skill group."""

    id: str
    heading: str = ""
    org: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    grade: str = ""
    meta: str = ""
    link: str = ""
    style: str = "bullets"
    text: str = ""
    bullets: list[Bullet] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    show_marks_from: int | None = None
    items: list[str] = field(default_factory=list)
    hidden: bool = False
    locked: bool = False


@dataclass
class Section:
    """One section of the CV."""

    id: str
    kind: str
    title: str
    text: str = ""
    entries: list[Entry] = field(default_factory=list)
    also: str = ""
    visible: bool = True
    locked: bool = False
    max_entries: int | None = None


@dataclass
class CVDoc:
    """A CV in schema 2."""

    schema: int = SCHEMA
    headline: str = ""
    contact_snapshot: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    closing: str = ""
    locale: str = "en-GB"
    extras: dict = field(default_factory=dict)


def to_json(doc: CVDoc) -> str:
    """Canonical JSON with sorted keys."""
    return json.dumps(dataclasses.asdict(doc), sort_keys=True, ensure_ascii=True)


def from_json(text: str) -> CVDoc:
    """A document from `to_json` output."""
    raw = json.loads(text)
    sections = []
    for s in raw.get("sections", []):
        entries = [Entry(**{**e, "bullets": [Bullet(**b) for b in e.get("bullets", [])],
                            "modules": [Module(**m) for m in e.get("modules", [])]}) for e in s.get("entries", [])]
        sections.append(Section(**{**s, "entries": entries}))
    return CVDoc(**{**raw, "sections": sections})


# ---------------------------------------------------------------------------------------------- store

def _con():
    from jobhunt import db

    return db.connect()


def _now() -> str:
    from jobhunt.core import clock

    return clock.iso()


def doc() -> dict:
    """The "main" `cv_documents` row, made on first use."""
    con = _con()
    try:
        row = con.execute("SELECT * FROM cv_documents WHERE id = 'main'").fetchone()
        if row is None:
            con.execute("""INSERT INTO cv_documents (id, route, send, type_id, look_id, created, updated)
                           VALUES ('main', 'improve', 'hal_copy', 'general', 'classic', ?, ?)""", (_now(), _now()))
            con.commit()
            row = con.execute("SELECT * FROM cv_documents WHERE id = 'main'").fetchone()
        return dict(row)
    finally:
        con.close()


def new_version(doc_: CVDoc, *, origin: str, what: str, by: str, parent: str | None = None,
                job_id: str | None = None, type_id: str | None = None, file_id: str | None = None) -> str:
    """Stores a version and makes it current (a per-job version is not made current)."""
    doc()
    from jobhunt.core import events

    vid = uuid.uuid4().hex
    con = _con()
    try:
        n = con.execute("SELECT coalesce(max(n), 0) + 1 FROM cv_versions WHERE doc_id = 'main'").fetchone()[0]
        con.execute("""INSERT INTO cv_versions (id, doc_id, n, parent_id, origin, type_id, job_id, file_id, data,
                                                what, by, created)
                       VALUES (?, 'main', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (vid, n, parent, origin, type_id, job_id, file_id, to_json(doc_), what, by, _now()))
        if job_id is None:
            con.execute("UPDATE cv_documents SET current_version_id = ?, updated = ? WHERE id = 'main'", (vid, _now()))
        con.commit()
    finally:
        con.close()
    events.publish("cv", version_id=vid)
    return vid


def current() -> tuple[str, CVDoc]:
    """The current version id and document; an empty one when there is none."""
    vid = doc()["current_version_id"]
    if vid is None:
        empty = CVDoc()
        return new_version(empty, origin="manual", what="An empty CV", by="hal"), empty
    return vid, get(vid)


def get(version_id: str) -> CVDoc:
    """One version's document."""
    con = _con()
    try:
        row = con.execute("SELECT data FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise LookupError("That version of your CV is not in Hal's list.")
    return from_json(row["data"])


def use(version_id: str) -> None:
    """Makes a version current."""
    get(version_id)
    con = _con()
    try:
        con.execute("UPDATE cv_documents SET current_version_id = ? WHERE id = 'main'", (version_id,))
        con.commit()
    finally:
        con.close()


MIME = {"pdf": "application/pdf", "txt": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def add_file(data: bytes, name: str, *, role: str) -> str:
    """Stores a file as `<sha256>.<ext>`, read-only, and returns its `cv_files` id."""
    from jobhunt.core import paths

    doc()
    sha = hashlib.sha256(data).hexdigest()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"
    folder = paths.cv_dir() / "files"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sha}.{ext}"
    if not target.exists():
        target.write_bytes(data)
        os.chmod(target, READ_ONLY)
    fid = uuid.uuid4().hex
    con = _con()
    try:
        con.execute("""INSERT INTO cv_files (id, sha256, name, mime, size, rel, role, added)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fid, sha, name, MIME.get(ext, "application/octet-stream"), len(data),
                     f"cv/files/{sha}.{ext}", role, _now()))
        if role == "baseline":
            con.execute("UPDATE cv_documents SET baseline_file_id = ? WHERE id = 'main'", (fid,))
        con.commit()
    finally:
        con.close()
    return fid


def file_bytes(file_id: str) -> bytes:
    """A stored file's bytes, after checking its SHA-256."""
    from jobhunt.core import paths

    con = _con()
    try:
        row = con.execute("SELECT sha256, rel FROM cv_files WHERE id = ?", (file_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise LookupError("Hal has no such file.")
    data = (paths.data_dir() / row["rel"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise RuntimeError("Your file has changed on disk since you added it.")
    return data


def _module(name: str, members: dict[str, Any]) -> types.ModuleType:
    mod = types.ModuleType(name, f"Stand-in for {name} (tests/support/stubs/cv_store.py).")
    mod.__dict__.update(members)
    return mod


def install() -> None:
    """Registers the stand-ins under the real module names when the CV store's modules are not there yet."""
    import jobhunt.cvstudio as package

    if MODEL not in sys.modules and importlib.util.find_spec(MODEL) is None:
        model = _module(MODEL, {"SCHEMA": SCHEMA, "Bullet": Bullet, "Module": Module, "Entry": Entry,
                                "Section": Section, "CVDoc": CVDoc, "to_json": to_json, "from_json": from_json})
        sys.modules[MODEL] = model
        package.model = model
    if STORE not in sys.modules and importlib.util.find_spec(STORE) is None:
        store = _module(STORE, {"doc": doc, "new_version": new_version, "current": current, "get": get, "use": use,
                                "add_file": add_file, "file_bytes": file_bytes})
        sys.modules[STORE] = store
        package.store = store

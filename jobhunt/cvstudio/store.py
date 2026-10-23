"""The CV store: Hal's copy of the person's CV as numbered versions that never change.

- One document per install, `cv_documents` row "main", made on first use. Its first version comes from the
  import of today's CV (`import_v1`) when there is one to import, else a short starter CV made from the
  settings, so every caller always has a current version.
- A version is written once and never edited: every change, whoever makes it, is a new version, and any
  version can be put back in use. Per-job versions (with `job_id`) never become the main CV.
- The bin: a version moved there is kept for `BIN_DAYS` days, then removed the next time the versions are
  listed (or by `purge_bin`), unless an application was sent with it. The current version and the first one
  cannot be binned.
- Uploaded files are kept as `cv/files/<sha256>.<ext>`, read-only, and their SHA-256 is checked on every
  read, so "your original file, byte for byte" stays true.
- Writes take SQLite's write lock first (`BEGIN IMMEDIATE`), so two requests saving at once get versions
  N and N+1, never a clash. Each new version publishes the event "cv".
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import mimetypes
import os
import sqlite3
import stat
from collections.abc import Iterator
from datetime import timedelta

from jobhunt.core import clock, events, paths
from jobhunt.cvstudio import model
from jobhunt.cvstudio.model import CVDoc, CVError

log = logging.getLogger(__name__)

DOC_ID = "main"
BIN_DAYS = 30                      # `cv_versions.binned`: purged after 30 days
DEFAULT_TYPE = "technical_grad"    # the recipe used until a CV type is chosen
DEFAULT_LOOK = "classic"
ROUTES = {"as_is": "original", "improve": "hal_copy", "fresh": "hal_copy"}   # route -> what employers get
SENDS = ("original", "hal_copy")
BYS = ("you", "hal", "baseline")
NO_FILE = "Hal has no file of yours to send as it is. Add your CV file first."
READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH


# ------------------------------------------------------------------------------------------ connections

@contextlib.contextmanager
def _conn(con: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if con is not None:
        yield con
        return
    from jobhunt import db

    own = db.connect()
    try:
        yield own
    finally:
        own.close()


@contextlib.contextmanager
def _writing(con: sqlite3.Connection) -> Iterator[None]:
    """One write transaction holding the write lock from the start; commits at the end, rolls back on error."""
    if con.in_transaction:
        yield
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.rollback()
        raise
    con.commit()


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


# ----------------------------------------------------------------------------------------- the document

def doc(con: sqlite3.Connection | None = None) -> dict:
    """The `cv_documents` row ("main"), created on first use; `layout` parsed."""
    with _conn(con) as c:
        row = c.execute("SELECT * FROM cv_documents WHERE id = ?", (DOC_ID,)).fetchone()
        if row is None:
            now = clock.iso()
            with _writing(c):
                c.execute("""INSERT OR IGNORE INTO cv_documents (id, route, send, type_id, look_id, layout,
                             created, updated) VALUES (?, 'improve', 'hal_copy', ?, ?, '{}', ?, ?)""",
                          (DOC_ID, DEFAULT_TYPE, DEFAULT_LOOK, now, now))
            row = c.execute("SELECT * FROM cv_documents WHERE id = ?", (DOC_ID,)).fetchone()
        out = dict(row)
        out["layout"] = json.loads(out.get("layout") or "{}")
        return out


def _update_doc(con: sqlite3.Connection, **values) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    con.execute(f"UPDATE cv_documents SET {sets}, updated = ? WHERE id = ?",
                (*values.values(), clock.iso(), DOC_ID))


def _seed(con: sqlite3.Connection) -> None:
    """Makes the first version: today's CV imported once, else a starter from the settings."""
    from jobhunt.cvstudio import import_v1

    if import_v1.import_into(con)["versions"]:
        return
    from jobhunt import cv

    new_version(model.from_v1(cv.starter()), origin="generated", what="A starting CV made from your settings",
                by="hal", con=con)


def current(con: sqlite3.Connection | None = None) -> tuple[str, CVDoc]:
    """(version id, document) of Hal's copy, the version in use."""
    with _conn(con) as c:
        d = doc(c)
        vid = d["current_version_id"]
        if not vid:
            _seed(c)
            vid = doc(c)["current_version_id"]
        return vid, get(vid, con=c)


def get(version_id: str, con: sqlite3.Connection | None = None) -> CVDoc:
    """One version's document; LookupError when there is no such version."""
    with _conn(con) as c:
        row = c.execute("SELECT data FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    if row is None:
        raise LookupError(f"There is no CV version {version_id}.")
    return model.from_json(row["data"])


def version(version_id: str, con: sqlite3.Connection | None = None) -> dict:
    """One version's row, without its document; LookupError when there is none."""
    with _conn(con) as c:
        row = c.execute("""SELECT id, n, parent_id, origin, type_id, look_id, job_id, file_id, what, by,
                           created, binned FROM cv_versions WHERE id = ?""", (version_id,)).fetchone()
    if row is None:
        raise LookupError(f"There is no CV version {version_id}.")
    return dict(row)


def new_version(doc_: CVDoc, *, origin: str, what: str, by: str, parent: str | None = None,
                job_id: str | None = None, type_id: str | None = None, file_id: str | None = None,
                con: sqlite3.Connection | None = None) -> str:
    """Stores `doc_` as the next version and returns its id; a main-CV version becomes the one in use.

    CVError when the document has problems (`model.validate`) or `by` is not you, hal or baseline.
    """
    if by not in BYS:
        raise CVError(f"by: {by!r} is not one of {', '.join(BYS)}")
    if not what.strip():
        raise CVError("what: a version needs words saying what changed")
    problems = model.validate(doc_)
    if problems:
        raise CVError(problems[0])
    doc_ = _recheck_not_found(doc_)
    data = model.to_json(doc_)
    with _conn(con) as c:
        d = doc(c)
        with _writing(c):
            n = c.execute("SELECT coalesce(max(n), 0) + 1 FROM cv_versions WHERE doc_id = ?", (DOC_ID,)).fetchone()[0]
            vid = f"v{n}-{hashlib.sha1(data.encode()).hexdigest()[:8]}"
            parent = parent if parent is not None else (d["current_version_id"] if job_id is None else None)
            c.execute("""INSERT INTO cv_versions (id, doc_id, n, parent_id, origin, type_id, look_id, job_id,
                         file_id, data, what, by, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (vid, DOC_ID, n, parent, origin, type_id or d["type_id"], d["look_id"], job_id, file_id,
                       data, what, by, clock.iso()))
            if job_id is None:
                _update_doc(c, current_version_id=vid)
    events.publish("cv", what="version", id=vid)
    log.info("CV version %s saved (%s, by %s)", n, origin, by)
    return vid


def _recheck_not_found(doc_: CVDoc) -> CVDoc:
    """The document with the import's "not found" list cut to the text it still lacks, so the warning goes
    once the person has put the text back."""
    missing = doc_.extras.get("not_found")
    if not missing:
        return doc_
    out = model.from_json(model.to_json(doc_))
    out.extras.pop("not_found")
    still = model.not_found({"text": list(missing)}, out)
    if still:
        out.extras["not_found"] = still
    return out


def use(version_id: str, con: sqlite3.Connection | None = None) -> None:
    """Puts a version of the main CV back in use; the one it replaces is kept."""
    with _conn(con) as c:
        v = version(version_id, con=c)
        if v["job_id"]:
            raise CVError("That version was made for one job. Its application keeps it.")
        if v["binned"]:
            raise CVError("That version is in the bin. Take it out of the bin first.")
        doc(c)
        with _writing(c):
            _update_doc(c, current_version_id=version_id)
    events.publish("cv", what="use", id=version_id)


# -------------------------------------------------------------------------------------- versions and bin

def _baseline_id(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT id FROM cv_versions WHERE doc_id = ? AND job_id IS NULL ORDER BY n LIMIT 1",
                      (DOC_ID,)).fetchone()
    return row["id"] if row else None


def versions(con: sqlite3.Connection | None = None) -> list[dict]:
    """The main CV's versions, newest first: {id, n, when, what, by, current, baseline, binned, origin}.

    Versions in the bin for longer than `BIN_DAYS` days are removed first.
    """
    with _conn(con) as c:
        purge_bin(con=c)
        d = doc(c)
        first = _baseline_id(c)
        rows = c.execute("""SELECT id, n, origin, what, by, created, binned, file_id FROM cv_versions
                            WHERE doc_id = ? AND job_id IS NULL ORDER BY n DESC""", (DOC_ID,)).fetchall()
    return [{"id": r["id"], "n": r["n"], "when": r["created"], "what": r["what"], "by": r["by"],
             "origin": r["origin"], "current": r["id"] == d["current_version_id"],
             "baseline": r["id"] == first and (r["by"] == "baseline" or bool(r["file_id"])),
             "binned": r["binned"]} for r in rows]


def bin_version(version_id: str, con: sqlite3.Connection | None = None) -> None:
    """Moves a version to the bin, where it stays for `BIN_DAYS` days."""
    with _conn(con) as c:
        v = version(version_id, con=c)
        if v["id"] == doc(c)["current_version_id"]:
            raise CVError("This version is in use. Use another version first.")
        if v["id"] == _baseline_id(c):
            raise CVError("Your first version is always kept.")
        with _writing(c):
            c.execute("UPDATE cv_versions SET binned = ? WHERE id = ?", (clock.iso(), version_id))
    events.publish("cv", what="bin", id=version_id)


def restore(version_id: str, con: sqlite3.Connection | None = None) -> None:
    """Takes a version out of the bin."""
    with _conn(con) as c:
        version(version_id, con=c)
        with _writing(c):
            c.execute("UPDATE cv_versions SET binned = NULL WHERE id = ?", (version_id,))
    events.publish("cv", what="restore", id=version_id)


def _kept(con: sqlite3.Connection) -> set[str]:
    """Versions something else still points at: an application sent with it, a job's CV, a suggestion."""
    kept: set[str] = set()
    for sql in ("SELECT cv_version_id FROM applications WHERE cv_version_id IS NOT NULL",
                "SELECT base_version_id FROM job_cvs", "SELECT version_id FROM job_cvs WHERE version_id IS NOT NULL",
                "SELECT result_version_id FROM cv_suggestions WHERE result_version_id IS NOT NULL"):
        kept |= {r[0] for r in con.execute(sql)}
    return kept


def purge_bin(now=None, con: sqlite3.Connection | None = None) -> int:
    """Removes versions binned more than `BIN_DAYS` days ago; returns how many went."""
    cutoff = clock.iso((now or clock.now_utc()) - timedelta(days=BIN_DAYS))
    with _conn(con) as c:
        old = [r["id"] for r in c.execute("SELECT id FROM cv_versions WHERE binned IS NOT NULL AND binned < ?",
                                          (cutoff,))]
        gone = [v for v in old if v not in _kept(c)]
        if not gone:
            return 0
        marks = ",".join("?" * len(gone))
        with _writing(c):
            for table, col in (("cv_parse_reports", "version_id"), ("cv_checks", "version_id"),
                               ("cv_versions", "id")):
                c.execute(f"DELETE FROM {table} WHERE {col} IN ({marks})", gone)
    log.info("removed %d CV versions from the bin", len(gone))
    return len(gone)


def compare(a: str, b: str, con: sqlite3.Connection | None = None) -> list[dict]:
    """What changed from version `a` to version `b`."""
    with _conn(con) as c:
        return model.diff(get(a, con=c), get(b, con=c))


# ------------------------------------------------------------------------------- route, send and layout

def set_route(route: str, con: sqlite3.Connection | None = None) -> dict:
    """How the CV is made (as_is, improve, fresh); it also sets what employers get."""
    if route not in ROUTES:
        raise CVError(f"route: {route!r} is not one of {', '.join(ROUTES)}")
    with _conn(con) as c:
        d = doc(c)
        if ROUTES[route] == "original" and not d["baseline_file_id"]:
            raise CVError(NO_FILE)
        with _writing(c):
            _update_doc(c, route=route, send=ROUTES[route])
    events.publish("cv", what="route", id=route)
    return doc()


def set_send(send: str, con: sqlite3.Connection | None = None) -> dict:
    """What employers get: your original file, or Hal's copy."""
    if send not in SENDS:
        raise CVError(f"send: {send!r} is not one of {', '.join(SENDS)}")
    with _conn(con) as c:
        if send == "original" and not doc(c)["baseline_file_id"]:
            raise CVError(NO_FILE)
        with _writing(c):
            _update_doc(c, send=send)
    events.publish("cv", what="send", id=send)
    return doc()


def employers_get(con: sqlite3.Connection | None = None) -> tuple[str, str]:
    """The "Employers get" line on top of My CV, and its sub-line."""
    with _conn(con) as c:
        d = doc(c)
        if d["send"] == "original":
            return ("Employers get: your original file, unchanged.",
                    "Adjusting for each job is off while you send your original.")
        vid, _ = current(c)
        n = version(vid, con=c)["n"]
    return f"Employers get: Hal's copy of your CV, version {n}.", "Every change in it was accepted by you."


def layout(con: sqlite3.Connection | None = None) -> dict:
    """Sections (id, title, show, lock, max, kind) in order, and the locked lines, of the version in use."""
    with _conn(con) as c:
        d = doc(c)
        _, cv = current(c)
    locked = list(d["layout"].get("locked_lines") or [])
    for s in cv.sections:
        for e in s.entries:
            locked += [model.target_of(s, e, b) for b in e.bullets if b.locked]
    return {"sections": [{"id": s.id, "kind": s.kind, "title": s.title, "show": s.visible, "lock": s.locked,
                          "max": s.max_entries} for s in cv.sections],
            "locked_lines": sorted(set(locked))}


def set_layout(sections: list[dict], locked_lines: list[str] | None = None,
               con: sqlite3.Connection | None = None) -> str | None:
    """Saves section order, names, limits, locks and Show, and the locked lines; returns the new version id,
    or None when nothing in the CV changed."""
    with _conn(con) as c:
        vid, cv = current(c)
        by_id = {s.id: s for s in cv.sections}
        wanted = [x["id"] for x in sections]
        if sorted(wanted) != sorted(by_id):
            raise CVError("sections: list every section once")
        new = model.from_json(model.to_json(cv))
        new_by_id = {s.id: s for s in new.sections}
        new.sections = [new_by_id[i] for i in wanted]
        for x in sections:
            s = new_by_id[x["id"]]
            title = str(x.get("title", s.title)).strip()
            if not title:
                raise CVError(f"sections.{x['id']}.title: a section needs a name")
            s.title = title
            s.visible = bool(x.get("show", s.visible))
            s.locked = bool(x.get("lock", s.locked))
            most = x.get("max", s.max_entries)
            s.max_entries = None if most in (None, "", "-") else int(most)
        if locked_lines is not None:
            wanted_locks = set(locked_lines)
            for t in wanted_locks:
                model.find(new, t)
            for s in new.sections:
                for e in s.entries:
                    for b in e.bullets:
                        b.locked = model.target_of(s, e, b) in wanted_locks
            d = doc(c)
            with _writing(c):
                _update_doc(c, layout=json.dumps({**d["layout"], "locked_lines": sorted(wanted_locks)}))
        if model.to_json(new) == model.to_json(cv):
            return None
        return new_version(new, origin="manual", what="You changed your CV's sections", by="you", parent=vid, con=c)


# ------------------------------------------------------------------------------------------------- files

def _files_dir():
    return paths.cv_dir() / "files"


def add_file(data: bytes, name: str, *, role: str, con: sqlite3.Connection | None = None) -> str:
    """Keeps a file as `cv/files/<sha256>.<ext>`, read-only, and returns its `cv_files` id.

    The same bytes added again give the same id.
    """
    if role not in ("baseline", "source", "export"):
        raise CVError(f"role: {role!r} is not baseline, source or export")
    sha = hashlib.sha256(data).hexdigest()
    ext = (os.path.splitext(name)[1].lower().lstrip(".") or "bin")[:8]
    rel = f"cv/files/{sha}.{ext}"
    path = paths.data_dir() / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        os.chmod(tmp, READ_ONLY)
        os.replace(tmp, path)
    fid = f"f-{sha[:16]}"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with _conn(con) as c, _writing(c):
        c.execute("""INSERT OR IGNORE INTO cv_files (id, sha256, name, mime, size, rel, role, added)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (fid, sha, name, mime, len(data), rel, role, clock.iso()))
    return fid


def file_row(file_id: str, con: sqlite3.Connection | None = None) -> dict:
    """A kept file's row; LookupError when there is none."""
    with _conn(con) as c:
        row = c.execute("SELECT * FROM cv_files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise LookupError(f"There is no CV file {file_id}.")
    return dict(row)


def file_bytes(file_id: str, con: sqlite3.Connection | None = None) -> bytes:
    """A kept file's bytes, after checking they still have the SHA-256 they were added with."""
    row = file_row(file_id, con=con)
    data = (paths.data_dir() / row["rel"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise CVError(f"Your file {row['name']} has changed on disk since Hal kept it, so Hal will not send it.")
    return data


def set_baseline_file(file_id: str, con: sqlite3.Connection | None = None) -> None:
    """Makes a kept file the person's own CV file, the one "your original file" means."""
    with _conn(con) as c:
        file_row(file_id, con=c)
        doc(c)
        with _writing(c):
            _update_doc(c, baseline_file_id=file_id)
    events.publish("cv", what="file", id=file_id)


def set_type(type_id: str, con: sqlite3.Connection | None = None) -> None:
    """Records the CV type in use (the caller checks the id and makes the `type_change` version)."""
    with _conn(con) as c:
        doc(c)
        with _writing(c):
            _update_doc(c, type_id=type_id)


def set_look(look_id: str, con: sqlite3.Connection | None = None) -> None:
    """Records the look in use (the caller checks the id)."""
    with _conn(con) as c:
        doc(c)
        with _writing(c):
            _update_doc(c, look_id=look_id)

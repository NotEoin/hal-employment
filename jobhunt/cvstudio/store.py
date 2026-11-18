"""The CV store: the person's CVs, each a history of numbered versions that never change.

- Several CVs, one `cv_documents` row each, with a name the person can change, `starred` ("Use this for
  applications"), `archived` and a `position` among the starred ones. Each has its own file, version in use,
  type, look and layout. An install's first CV has the id "main" (`DOC_ID`); later ones get `cv-<hex>`.
- The lead CV (`lead_id`) is what every caller that names no CV gets: the first starred one in the person's
  order, else the most recently changed one not archived. With one CV that is the CV, so tailoring, the pack
  writer and exports behave as they did before there were several. While any CV exists one is starred: the
  last star cannot be taken off, and archiving the last starred CV stars the most recent one left.
- Archive is the delete: an archived CV keeps every version, so an application sent with one still has it.
- Version numbers count one CV's own versions from 1. A per-job version (with `job_id`) takes none of them: it
  is kept with the CV it was made from, with `n` below zero (its own count, only there for the table's
  UNIQUE (doc_id, n)), and `version()` shows its `n` as None. It never becomes a CV's version in use.
- A version id is `v<n>-<hash>`, and stays as it was when migration 0033 numbered the old history again, so ids
  of older versions need not match their number.
- The first use with no CV at all makes one: the import of today's CV (`import_v1`) when there is one to
  import, else a short starter CV made from the settings, so every caller always has a current version. An
  empty CV, or a starter nobody changed, goes to the archive when the person's own first CV arrives
  (`create`).
- The bin: a version moved there is kept for `BIN_DAYS` days, then removed the next time the versions are
  listed (or by `purge_bin`), unless an application was sent with it. The current version and the first one
  cannot be binned.
- Uploaded files are kept as `cv/files/<sha256>.<ext>`, read-only, and their SHA-256 is checked on every
  read, so "your original file, byte for byte" stays true. `cv_for_file` finds the CV a file already made.
- "Keep this CV to" is `cv_documents.page_limit`, NULL until the person chooses; `pagelimit.limit_of` is the
  one reader, so the CV type's rule stands in for it in one place only.
- Writes take SQLite's write lock first (`BEGIN IMMEDIATE`), so two requests saving at once get versions
  N and N+1, never a clash. Each change publishes the event "cv" with the CV's id as `cv`.
- A save made on one version (`new_version(..., base=...)`) is refused with `StaleVersion` when another version
  came into use since, checked under the write lock, so an editor never overwrites a change made elsewhere.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from datetime import timedelta
from pathlib import PurePath

from jobhunt.core import clock, events, paths
from jobhunt.cvstudio import model
from jobhunt.cvstudio.model import CVDoc, CVError

log = logging.getLogger(__name__)

DOC_ID = "main"                    # the id of an install's first CV (migration 0033 kept it for the lead)
BIN_DAYS = 30                      # `cv_versions.binned`: purged after 30 days
DEFAULT_TYPE = "technical_grad"    # stored until a type is chosen; documents use `type_in_use`
DEFAULT_LOOK = "classic"
DEFAULT_NAME = "My CV"
NAME_MAX = 80                      # a CV's name fits on one line of the switcher
ID_BYTES = 4                       # `cv-<8 hex>`: plenty for the handful of CVs one person keeps
ROUTES = {"as_is": "original", "improve": "hal_copy", "fresh": "hal_copy"}   # route -> what employers get
SENDS = ("original", "hal_copy")
BYS = ("you", "hal", "baseline")
NO_FILE = "Hal has no file of yours to send as it is. Add your CV file first."
# No "star" on screen: the app keeps that word for nothing, as "STAR" names a way to tell examples (2026-11-18).
LAST_STAR = ("Hal needs one CV to use for applications, so this one stays in use. Choose \"Use this for "
             "applications\" on another CV first, then you can turn it off here.")
LAST_ACTIVE = "This is your only CV in use, so it cannot go to the archive. Add another CV first."
STALE_ORDER = "Your list of CVs changed since this page opened. Reload the page and try again."
ARCHIVED = "This CV is in the archive. Restore it first."
STARTER_WHAT = "A starting CV made from your settings"
PASTED_NAME = "Pasted CV.txt"      # the file name pasted text is kept under
PAGE_LIMITS = (0, 1, 2, 3, 4)      # "Keep this CV to" (`cv_documents.page_limit`), as far as a type's `MAX_PAGES`
READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
_COLUMNS = ("id, name, starred, archived, position, current_version_id, baseline_file_id, type_id, look_id, "
            "route, send, created, updated")


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
def writing(con: sqlite3.Connection) -> Iterator[None]:
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


class StaleVersion(CVError):
    """The version a change was made on is no longer the one in use."""


class NoSuchCV(LookupError):
    """There is no CV with that id."""


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _publish(what: str, doc_id: str, **data) -> None:
    events.publish("cv", what=what, cv=doc_id, **data)


# ------------------------------------------------------------------------------------------------ the CVs

def lead_id(con: sqlite3.Connection | None = None) -> str | None:
    """The lead CV's id without making one: the first starred CV in the person's order, else the most recently
    changed CV not archived, else the most recently changed one; None when there is no CV."""
    with _conn(con) as c:
        row = c.execute("""SELECT id FROM cv_documents
                           ORDER BY archived IS NOT NULL, starred DESC, CASE WHEN starred THEN position END,
                                    updated DESC, id LIMIT 1""").fetchone()
    return row["id"] if row else None


def lead_version_id(con: sqlite3.Connection | None = None) -> str | None:
    """The lead CV's version in use, without making a CV when there is none (Home, the profile's headline)."""
    with _conn(con) as c:
        lead = lead_id(c)
        if lead is None:
            return None
        row = c.execute("SELECT current_version_id FROM cv_documents WHERE id = ?", (lead,)).fetchone()
    return row["current_version_id"] if row else None


def _make_first(con: sqlite3.Connection) -> None:
    now = clock.iso()
    with writing(con):
        con.execute("""INSERT OR IGNORE INTO cv_documents (id, route, send, type_id, look_id, layout, created, updated,
                       name, starred, position) VALUES (?, 'improve', 'hal_copy', ?, ?, '{}', ?, ?, ?, 1, 0)""",
                    (DOC_ID, DEFAULT_TYPE, DEFAULT_LOOK, now, now, DEFAULT_NAME))


def doc(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> dict:
    """One CV's `cv_documents` row with `layout` parsed; the lead CV when `doc_id` is None, made on first use.

    `NoSuchCV` when `doc_id` names no CV.
    """
    with _conn(con) as c:
        if doc_id is None:
            doc_id = lead_id(c)
            if doc_id is None:
                _make_first(c)
                doc_id = DOC_ID
        row = c.execute("SELECT * FROM cv_documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            raise NoSuchCV(f"There is no CV {doc_id}.")
        out = dict(row)
        out["layout"] = json.loads(out.get("layout") or "{}")
        return out


def _doc_id(con: sqlite3.Connection, doc_id: str | None) -> str:
    """The CV a call acts on: `doc_id` once checked, else the lead CV."""
    return doc(con, doc_id)["id"]


def _update_doc(con: sqlite3.Connection, doc_id: str, **values) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    con.execute(f"UPDATE cv_documents SET {sets}, updated = ? WHERE id = ?",
                (*values.values(), clock.iso(), doc_id))


def _shape(con: sqlite3.Connection, r: sqlite3.Row, lead: str | None) -> dict:
    """One CV as `documents()` lists it."""
    current = r["current_version_id"]
    n = con.execute("SELECT n FROM cv_versions WHERE id = ?", (current,)).fetchone() if current else None
    count = con.execute("SELECT count(*) FROM cv_versions WHERE doc_id = ? AND job_id IS NULL AND binned IS NULL",
                        (r["id"],)).fetchone()[0]
    file = (con.execute("SELECT name FROM cv_files WHERE id = ?", (r["baseline_file_id"],)).fetchone()
            if r["baseline_file_id"] else None)
    return {"id": r["id"], "name": r["name"] or DEFAULT_NAME, "starred": bool(r["starred"]),
            "archived": r["archived"], "position": r["position"], "lead": r["id"] == lead,
            "current_version_id": current, "n": n["n"] if n else None, "versions": count,
            "file": file["name"] if file else None, "type_id": r["type_id"], "look_id": r["look_id"],
            "send": r["send"], "created": r["created"], "updated": r["updated"]}


def documents(con: sqlite3.Connection | None = None, *, archived: bool | None = False) -> list[dict]:
    """The person's CVs: starred ones first in their order, then the rest, most recently changed first.

    `archived` False (the default) lists the CVs in use, True the archived ones, None all of them. Each is
    {id, name, starred, archived, position, lead, current_version_id, n, versions, file, type_id, look_id, send,
    created, updated}; `n` is the number of its version in use, `file` the name of its own file.
    """
    where = {False: "WHERE archived IS NULL", True: "WHERE archived IS NOT NULL", None: ""}[archived]
    with _conn(con) as c:
        lead = lead_id(c)
        rows = c.execute(f"""SELECT {_COLUMNS} FROM cv_documents {where}
                             ORDER BY archived IS NOT NULL, starred DESC, CASE WHEN starred THEN position END,
                                      updated DESC, id""").fetchall()
        return [_shape(c, r, lead) for r in rows]


def starred(con: sqlite3.Connection | None = None) -> list[dict]:
    """The CVs marked "Use this for applications", in the person's order, as `documents()` lists them.

    This is the list to pick an application's base CV from; its first item is the lead CV. Empty only
    when the person has no CV yet.
    """
    return [d for d in documents(con) if d["starred"]]


def _active_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM cv_documents WHERE archived IS NULL").fetchone()[0]


def _star_someone(con: sqlite3.Connection) -> None:
    """Stars the most recently changed CV in use when none is starred, so applications always have one."""
    if con.execute("SELECT 1 FROM cv_documents WHERE starred = 1 AND archived IS NULL").fetchone():
        return
    con.execute("""UPDATE cv_documents SET starred = 1, position = 0 WHERE id =
                   (SELECT id FROM cv_documents WHERE archived IS NULL ORDER BY updated DESC, id LIMIT 1)""")


def star(doc_id: str, on: bool, con: sqlite3.Connection | None = None) -> dict:
    """Turns "Use this for applications" on (last in the starred order) or off; returns the CV's row.

    CVError (`LAST_STAR`) when it would leave no starred CV, and (`ARCHIVED`) when an archived CV is starred.
    The CV is read under the write lock, so a change another request made meanwhile is seen.
    """
    with _conn(con) as c:
        with writing(c):
            d = doc(c, doc_id)
            if on:
                if d["archived"]:
                    raise CVError(ARCHIVED)
                end = c.execute("SELECT coalesce(max(position), 0) + 1 FROM cv_documents WHERE starred = 1"
                                ).fetchone()[0]
                if not d["starred"]:
                    c.execute("UPDATE cv_documents SET starred = 1, position = ? WHERE id = ?", (end, doc_id))
            elif d["starred"]:
                others = c.execute("SELECT count(*) FROM cv_documents WHERE starred = 1 AND archived IS NULL "
                                   "AND id != ?", (doc_id,)).fetchone()[0]
                if not others:
                    raise CVError(LAST_STAR)
                c.execute("UPDATE cv_documents SET starred = 0 WHERE id = ?", (doc_id,))
    _publish("star", doc_id, on=bool(on))
    return doc(con, doc_id)


def archive(doc_id: str, on: bool = True, con: sqlite3.Connection | None = None) -> dict:
    """Moves a CV to the archive (`on`), or restores it; returns the CV's row. Nothing in it is deleted.

    An archived CV loses its star; when it was the last starred one, the most recent CV left is starred.
    CVError (`LAST_ACTIVE`) for the only CV in use. The CV is read under the write lock, as in `star`.
    """
    with _conn(con) as c:
        with writing(c):
            d = doc(c, doc_id)
            if on and not d["archived"]:
                if _active_count(c) <= 1:
                    raise CVError(LAST_ACTIVE)
                c.execute("UPDATE cv_documents SET archived = ?, starred = 0 WHERE id = ?", (clock.iso(), doc_id))
                _star_someone(c)
            elif not on and d["archived"]:
                c.execute("UPDATE cv_documents SET archived = NULL, updated = ? WHERE id = ?", (clock.iso(), doc_id))
    _publish("archive", doc_id, on=bool(on))
    return doc(con, doc_id)


def rename(doc_id: str, name: str, con: sqlite3.Connection | None = None) -> dict:
    """Gives a CV a new name; CVError for an empty or over-long one. Returns the CV's row."""
    name = " ".join(str(name or "").split())
    if not name:
        raise CVError("name: a CV needs a name")
    if len(name) > NAME_MAX:
        raise CVError(f"name: keep it to {NAME_MAX} characters")
    with _conn(con) as c:
        doc(c, doc_id)
        with writing(c):
            c.execute("UPDATE cv_documents SET name = ? WHERE id = ?", (name, doc_id))
    _publish("rename", doc_id)
    return doc(con, doc_id)


def reorder(doc_ids: list[str], con: sqlite3.Connection | None = None) -> list[dict]:
    """Puts the starred CVs in this order (the first is the lead CV); returns `starred()`.

    CVError (`STALE_ORDER`, in words for the page's toast) unless `doc_ids` lists every starred CV once, as when
    the page's list is out of date.
    """
    with _conn(con) as c:
        with writing(c):
            have = [d["id"] for d in starred(c)]
            if sorted(doc_ids) != sorted(have) or len(set(doc_ids)) != len(doc_ids):
                raise CVError(STALE_ORDER)
            for i, d in enumerate(doc_ids):
                c.execute("UPDATE cv_documents SET position = ? WHERE id = ?", (i, d))
        _publish("order", doc_ids[0] if doc_ids else "")
        return starred(c)


def name_for(file_name: str, doc_: CVDoc | None = None) -> str:
    """A new CV's name: its file's name without the extension, else the CV's headline, else `DEFAULT_NAME`."""
    stem = PurePath(file_name or "").stem.strip()
    if (not stem or file_name == PASTED_NAME) and doc_ is not None and str(doc_.headline or "").strip():
        stem = str(doc_.headline).strip()
    return " ".join((stem or DEFAULT_NAME).split())[:NAME_MAX]


def _starters(con: sqlite3.Connection) -> list[str]:
    """CVs in use that are empty, or hold only the starter Hal made from the settings, untouched since."""
    out = []
    for r in con.execute("SELECT id FROM cv_documents WHERE archived IS NULL").fetchall():
        kinds = con.execute("SELECT origin, what FROM cv_versions WHERE doc_id = ? AND job_id IS NULL",
                            (r["id"],)).fetchall()
        if not kinds or (len(kinds) == 1 and (kinds[0]["origin"], kinds[0]["what"]) == ("generated", STARTER_WHAT)):
            out.append(r["id"])
    return out


def create(name: str, first: CVDoc | None = None, *, origin: str = "", what: str = "", by: str = "",
           file_id: str | None = None, type_id: str | None = None, star: bool = True,
           con: sqlite3.Connection | None = None) -> str:
    """Makes a new CV and returns its id; with `first`, its version 1 (as `new_version`) in the same write.

    A starred new CV goes first in the starred order, so it is the lead CV, as an upload was the CV in use
    before there were several. Its look and type come from the lead CV. When `first` is the person's own CV,
    a starter CV nobody changed goes to the archive.
    """
    name = " ".join(str(name or "").split())[:NAME_MAX] or DEFAULT_NAME
    with _conn(con) as c:
        lead = doc(c) if lead_id(c) else None
        new_id = f"cv-{secrets.token_hex(ID_BYTES)}"
        now = clock.iso()
        with writing(c):
            top = c.execute("SELECT coalesce(min(position), 0) - 1 FROM cv_documents WHERE starred = 1").fetchone()[0]
            c.execute("""INSERT INTO cv_documents (id, route, send, baseline_file_id, type_id, look_id, layout, created,
                         updated, name, starred, position) VALUES (?, 'improve', 'hal_copy', ?, ?, ?, '{}', ?, ?, ?,
                         ?, ?)""",
                      (new_id, file_id, type_id or (lead["type_id"] if lead else DEFAULT_TYPE),
                       lead["look_id"] if lead else DEFAULT_LOOK, now, now, name, int(star), top if star else 0))
            if first is not None:
                new_version(first, origin=origin, what=what, by=by, file_id=file_id, type_id=type_id,
                            doc_id=new_id, con=c)
                if origin != "generated":
                    for old in _starters(c):
                        if old != new_id:
                            c.execute("UPDATE cv_documents SET archived = ?, starred = 0 WHERE id = ?", (now, old))
            _star_someone(c)
    _publish("create", new_id)
    log.info("made a new CV %s", new_id)
    return new_id


def create_for_file(file_id: str, name: str, first: CVDoc, *, what: str, last: bool = False) -> tuple[str, bool]:
    """(the CV's id, True) for a new CV read from a kept file (`create`, origin uploaded), or (the CV the file
    made already, False), checked under the write lock right before the CV is made.

    So two readings of one file that started before either finished (My CVs and Your record at once) make one
    CV, not two (2026-11-18). A new CV goes first among the CVs in use, or last with `last` (Your record's
    toggle); with `last` a CV the file made already is put in use too (restored from the archive, then last).
    """
    with _conn(None) as c, writing(c):
        had = cv_for_file(file_id, con=c)
        if had is None:
            made = create(name, first, origin="uploaded", what=what, by="baseline", file_id=file_id,
                          star=not last, con=c)
            if last:
                star(made, True, con=c)
            return made, True
        if last:
            if doc(c, had)["archived"]:
                archive(had, False, con=c)
            star(had, True, con=c)
        log.info("file %s made CV %s while it was read: no second CV", file_id, had)
        return had, False


def cv_for_file(file_id: str, con: sqlite3.Connection | None = None) -> str | None:
    """The CV a kept file already made (its own file, or a version read from it); one in use first."""
    with _conn(con) as c:
        row = c.execute("""SELECT d.id FROM cv_documents d
                           WHERE d.baseline_file_id = ? OR EXISTS (SELECT 1 FROM cv_versions v WHERE v.doc_id = d.id
                                 AND v.file_id = ? AND v.job_id IS NULL)
                           ORDER BY d.archived IS NOT NULL, d.updated DESC LIMIT 1""", (file_id, file_id)).fetchone()
    return row["id"] if row else None


def file_cvs(con: sqlite3.Connection | None = None) -> dict[str, dict]:
    """Each kept file's SHA-256 with the CV it belongs to (its own file, or a version read from it).

    A file two CVs share answers the one most in use: a starred CV first, in the person's order, then one in
    use, most recently changed first, then an archived one. Each value is {id, name, starred, archived, rank,
    version_id}: `rank` is the CV's place among the starred ones (0 for the lead), None when it is not starred,
    and `version_id` its version in use.
    """
    with _conn(con) as c:
        order = [r["id"] for r in c.execute("""SELECT id FROM cv_documents WHERE starred = 1 AND archived IS NULL
                                                ORDER BY position, updated DESC, id""")]
        rows = c.execute("""SELECT f.sha256, d.id, d.name, d.starred, d.archived, d.current_version_id
                            FROM cv_files f JOIN cv_documents d ON d.baseline_file_id = f.id OR EXISTS
                                 (SELECT 1 FROM cv_versions v WHERE v.doc_id = d.id AND v.file_id = f.id
                                  AND v.job_id IS NULL)
                            ORDER BY d.archived IS NOT NULL, d.starred DESC,
                                     CASE WHEN d.starred THEN d.position END, d.updated DESC, d.id""").fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        starred_ = bool(r["starred"]) and r["id"] in order
        out.setdefault(r["sha256"], {"id": r["id"], "name": r["name"] or DEFAULT_NAME, "starred": starred_,
                                     "archived": r["archived"], "rank": order.index(r["id"]) if starred_ else None,
                                     "version_id": r["current_version_id"]})
    return out


# --------------------------------------------------------------------------------------------- versions

def _seed(con: sqlite3.Connection, doc_id: str) -> None:
    """Makes a CV's first version: today's CV imported once, else a starter from the settings."""
    from jobhunt.cvstudio import import_v1

    if import_v1.import_into(con, doc_id=doc_id)["versions"]:
        return
    from jobhunt.cvstudio import cv

    new_version(model.from_v1(cv.starter()), origin="generated", what=STARTER_WHAT, by="hal", doc_id=doc_id, con=con)


def current(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> tuple[str, CVDoc]:
    """(version id, document) of a CV's version in use; the lead CV when `doc_id` is None."""
    with _conn(con) as c:
        d = doc(c, doc_id)
        vid = d["current_version_id"]
        if not vid:
            _seed(c, d["id"])
            vid = doc(c, d["id"])["current_version_id"]
        return vid, get(vid, con=c)


def get(version_id: str, con: sqlite3.Connection | None = None) -> CVDoc:
    """One version's document; LookupError when there is no such version."""
    with _conn(con) as c:
        row = c.execute("SELECT data FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    if row is None:
        raise LookupError(f"There is no CV version {version_id}.")
    return model.from_json(row["data"])


def version(version_id: str, con: sqlite3.Connection | None = None) -> dict:
    """One version's row, without its document, with its CV as `doc_id`; `n` is None for a per-job version.

    LookupError when there is none.
    """
    with _conn(con) as c:
        row = c.execute("""SELECT id, doc_id, n, parent_id, origin, type_id, look_id, job_id, file_id, what, by,
                           created, binned FROM cv_versions WHERE id = ?""", (version_id,)).fetchone()
    if row is None:
        raise LookupError(f"There is no CV version {version_id}.")
    out = dict(row)
    if out["job_id"]:
        out["n"] = None
    return out


def _target(con: sqlite3.Connection, doc_id: str | None, *versions_: str | None) -> str:
    """The CV a new version goes to: `doc_id`, else the CV of the first version named, else the lead CV."""
    if doc_id is not None:
        return _doc_id(con, doc_id)
    for vid in versions_:
        if vid:
            row = con.execute("SELECT doc_id FROM cv_versions WHERE id = ?", (vid,)).fetchone()
            if row is not None:
                return row["doc_id"]
    return _doc_id(con, None)


def _new_id(con: sqlite3.Connection, n: int, data: str) -> str:
    """`v<n>-<hash of the document>`, salted when that id is taken (ids from before 0033 keep old numbers)."""
    salt = ""
    for attempt in range(1, 100):
        vid = f"v{n}-{hashlib.sha1((salt + data).encode(), usedforsecurity=False).hexdigest()[:8]}"
        if con.execute("SELECT 1 FROM cv_versions WHERE id = ?", (vid,)).fetchone() is None:
            return vid
        salt = f"{attempt}\n"
    raise CVError("Hal could not give this version an id. Try again.")


def new_version(doc_: CVDoc, *, origin: str, what: str, by: str, parent: str | None = None,
                job_id: str | None = None, type_id: str | None = None, file_id: str | None = None,
                base: str | None = None, doc_id: str | None = None, con: sqlite3.Connection | None = None) -> str:
    """Stores `doc_` as a CV's next version and returns its id; a version that is not per-job is put in use.

    The CV is `doc_id`, else the one `base` or `parent` belongs to, else the lead CV. A per-job version
    (`job_id`) takes no number of the CV. CVError when the document has problems (`model.validate`) or `by` is
    not you, hal or baseline; `StaleVersion` when `base` is given and is no longer the CV's version in use.
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
        target = _target(c, doc_id, base, parent)
        with writing(c):
            d = doc(c, target)
            if base is not None and d["current_version_id"] != base:
                raise StaleVersion("Your CV changed since you started editing. Your changes are not saved.")
            if job_id is None:
                n = c.execute("SELECT coalesce(max(n), 0) + 1 FROM cv_versions WHERE doc_id = ? AND n > 0",
                              (target,)).fetchone()[0]
            else:
                n = c.execute("SELECT coalesce(min(n), 0) - 1 FROM cv_versions WHERE doc_id = ? AND n < 0",
                              (target,)).fetchone()[0]
            vid = _new_id(c, n, data)
            parent = parent if parent is not None else (d["current_version_id"] if job_id is None else None)
            c.execute("""INSERT INTO cv_versions (id, doc_id, n, parent_id, origin, type_id, look_id, job_id,
                         file_id, data, what, by, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (vid, target, n, parent, origin, type_id or d["type_id"], d["look_id"], job_id, file_id,
                       data, what, by, clock.iso()))
            if job_id is None:
                _update_doc(c, target, current_version_id=vid)
    _publish("version", target, id=vid)
    log.info("CV %s version %s saved (%s, by %s)", target, n, origin, by)
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
    """Puts a version back in use in its own CV; the one it replaces is kept."""
    with _conn(con) as c:
        v = version(version_id, con=c)
        if v["job_id"]:
            raise CVError("That version was made for one job. Its application keeps it.")
        if v["binned"]:
            raise CVError("That version is in the bin. Take it out of the bin first.")
        with writing(c):
            _update_doc(c, v["doc_id"], current_version_id=version_id)
    _publish("use", v["doc_id"], id=version_id)


# -------------------------------------------------------------------------------------- versions and bin

def _baseline_id(con: sqlite3.Connection, doc_id: str) -> str | None:
    row = con.execute("SELECT id FROM cv_versions WHERE doc_id = ? AND job_id IS NULL ORDER BY n LIMIT 1",
                      (doc_id,)).fetchone()
    return row["id"] if row else None


def versions(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> list[dict]:
    """A CV's own versions (the lead CV's by default), newest first:
    {id, n, when, what, by, current, baseline, binned, origin}. Per-job versions are not listed.

    Versions in the bin for longer than `BIN_DAYS` days are removed first.
    """
    with _conn(con) as c:
        purge_bin(con=c)
        d = doc(c, doc_id)
        first = _baseline_id(c, d["id"])
        rows = c.execute("""SELECT id, n, origin, what, by, created, binned, file_id FROM cv_versions
                            WHERE doc_id = ? AND job_id IS NULL ORDER BY n DESC""", (d["id"],)).fetchall()
    return [{"id": r["id"], "n": r["n"], "when": r["created"], "what": r["what"], "by": r["by"],
             "origin": r["origin"], "current": r["id"] == d["current_version_id"],
             "baseline": r["id"] == first and (r["by"] == "baseline" or bool(r["file_id"])),
             "binned": r["binned"]} for r in rows]


def bin_version(version_id: str, con: sqlite3.Connection | None = None) -> None:
    """Moves a version to the bin, where it stays for `BIN_DAYS` days."""
    with _conn(con) as c:
        v = version(version_id, con=c)
        if v["id"] == doc(c, v["doc_id"])["current_version_id"]:
            raise CVError("This version is in use. Use another version first.")
        if v["id"] == _baseline_id(c, v["doc_id"]):
            raise CVError("Your first version is always kept.")
        with writing(c):
            c.execute("UPDATE cv_versions SET binned = ? WHERE id = ?", (clock.iso(), version_id))
    _publish("bin", v["doc_id"], id=version_id)


def restore(version_id: str, con: sqlite3.Connection | None = None) -> None:
    """Takes a version out of the bin."""
    with _conn(con) as c:
        v = version(version_id, con=c)
        with writing(c):
            c.execute("UPDATE cv_versions SET binned = NULL WHERE id = ?", (version_id,))
    _publish("restore", v["doc_id"], id=version_id)


def _kept(con: sqlite3.Connection) -> set[str]:
    """Versions something else still points at: an application sent with it, a job's CV, a suggestion."""
    kept: set[str] = set()
    for sql in ("SELECT cv_version_id FROM applications WHERE cv_version_id IS NOT NULL",
                "SELECT base_version_id FROM job_cvs", "SELECT version_id FROM job_cvs WHERE version_id IS NOT NULL",
                "SELECT result_version_id FROM cv_suggestions WHERE result_version_id IS NOT NULL"):
        kept |= {r[0] for r in con.execute(sql)}
    return kept


def purge_bin(now=None, con: sqlite3.Connection | None = None) -> int:
    """Removes versions binned more than `BIN_DAYS` days ago, from every CV; returns how many went."""
    cutoff = clock.iso((now or clock.now_utc()) - timedelta(days=BIN_DAYS))
    with _conn(con) as c:
        old = [r["id"] for r in c.execute("SELECT id FROM cv_versions WHERE binned IS NOT NULL AND binned < ?",
                                          (cutoff,))]
        gone = [v for v in old if v not in _kept(c)]
        if not gone:
            return 0
        marks = ",".join("?" * len(gone))
        with writing(c):
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

def set_route(route: str, con: sqlite3.Connection | None = None, doc_id: str | None = None) -> dict:
    """How a CV is made (as_is, improve, fresh); it also sets what employers get. Returns the CV's row."""
    if route not in ROUTES:
        raise CVError(f"route: {route!r} is not one of {', '.join(ROUTES)}")
    with _conn(con) as c:
        d = doc(c, doc_id)
        if ROUTES[route] == "original" and not d["baseline_file_id"]:
            raise CVError(NO_FILE)
        with writing(c):
            _update_doc(c, d["id"], route=route, send=ROUTES[route])
    _publish("route", d["id"], route=route)
    return doc(con, d["id"])


def set_send(send: str, con: sqlite3.Connection | None = None, doc_id: str | None = None) -> dict:
    """What employers get from a CV: your original file, or Hal's copy. Returns the CV's row."""
    if send not in SENDS:
        raise CVError(f"send: {send!r} is not one of {', '.join(SENDS)}")
    with _conn(con) as c:
        d = doc(c, doc_id)
        if send == "original" and not d["baseline_file_id"]:
            raise CVError(NO_FILE)
        with writing(c):
            _update_doc(c, d["id"], send=send)
    _publish("send", d["id"], send=send)
    return doc(con, d["id"])


def employers_get(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> tuple[str, str]:
    """The "Employers get" line on top of My CV, and its sub-line, for one CV."""
    with _conn(con) as c:
        d = doc(c, doc_id)
        if d["send"] == "original":
            return ("Employers get: your original file, unchanged.",
                    "Adjusting for each job is off while you send your original.")
        vid, _ = current(c, d["id"])
        n = version(vid, con=c)["n"]
    return f"Employers get: Hal's copy of your CV, version {n}.", "Every change in it was accepted by you."


def layout(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> dict:
    """Sections (id, title, show, lock, max, kind) in order, and the locked lines, of a CV's version in use."""
    with _conn(con) as c:
        d = doc(c, doc_id)
        _, cv = current(c, d["id"])
    return layout_of(d, cv)


def layout_of(d: dict, cv: CVDoc) -> dict:
    """`layout` of document `cv` as a version of CV row `d` (`doc`) would have it, reading nothing more.

    So a document not saved yet is printed with the layout it will have once saved (`pagelimit.count_doc`),
    not with the version in use's sections (2026-11-19).
    """
    locked = list(d["layout"].get("locked_lines") or [])
    for s in cv.sections:
        for e in s.entries:
            locked += [model.target_of(s, e, b) for b in e.bullets if b.locked]
    return {"sections": [{"id": s.id, "kind": s.kind, "title": s.title, "show": s.visible, "lock": s.locked,
                          "max": s.max_entries} for s in cv.sections],
            "locked_lines": sorted(set(locked))}


def set_layout(sections: list[dict], locked_lines: list[str] | None = None,
               con: sqlite3.Connection | None = None, doc_id: str | None = None) -> str | None:
    """Saves a CV's section order, names, limits, locks and Show, and the locked lines; returns the new version
    id, or None when nothing in the CV changed."""
    with _conn(con) as c:
        doc_id = _doc_id(c, doc_id)
        vid, cv = current(c, doc_id)
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
            d = doc(c, doc_id)
            with writing(c):
                _update_doc(c, doc_id, layout=json.dumps({**d["layout"], "locked_lines": sorted(wanted_locks)}))
        if model.to_json(new) == model.to_json(cv):
            return None
        return new_version(new, origin="manual", what="You changed your CV's sections", by="you", parent=vid,
                           doc_id=doc_id, con=c)


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
    with _conn(con) as c, writing(c):
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


def set_baseline_file(file_id: str, con: sqlite3.Connection | None = None, doc_id: str | None = None) -> None:
    """Makes a kept file a CV's own file, the one "your original file" means."""
    with _conn(con) as c:
        file_row(file_id, con=c)
        doc_id = _doc_id(c, doc_id)
        with writing(c):
            _update_doc(c, doc_id, baseline_file_id=file_id)
    _publish("file", doc_id, id=file_id)


def type_chosen(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> bool:
    """True when the person chose a CV's type: a `type_change` version, or a type other than the first one."""
    with _conn(con) as c:
        d = doc(c, doc_id)
        if d["type_id"] != DEFAULT_TYPE:
            return True
        return c.execute("SELECT 1 FROM cv_versions WHERE origin = 'type_change' AND doc_id = ? LIMIT 1",
                         (d["id"],)).fetchone() is not None


def type_in_use(con: sqlite3.Connection | None = None, doc_id: str | None = None) -> str:
    """The CV type documents are written for: the one the person chose, else the one Hal suggests from their goal,
    subject and CV (`types.suggest`). Until 2026-11-12 everyone who never opened Type and look was written for as
    a technical graduate (`DEFAULT_TYPE`), whatever they studied."""
    with _conn(con) as c:
        d = doc(c, doc_id)
        if type_chosen(c, d["id"]):
            return d["type_id"]
        _, cv = current(c, d["id"])
    from jobhunt.core import settings
    from jobhunt.cvstudio import types

    try:
        from jobhunt.find import goal

        wanted = goal.current()
    except (ImportError, LookupError, KeyError) as e:
        log.info("no goal to suggest a CV type from: %s", e)
        wanted = None
    return types.suggest(wanted, cv, subject=str(settings.get("you.education.subject") or ""))[0]


def set_type(type_id: str, con: sqlite3.Connection | None = None, doc_id: str | None = None) -> None:
    """Records a CV's type in use (the caller checks the id and makes the `type_change` version)."""
    with _conn(con) as c:
        doc_id = _doc_id(c, doc_id)
        with writing(c):
            _update_doc(c, doc_id, type_id=type_id)


def set_look(look_id: str, con: sqlite3.Connection | None = None, doc_id: str | None = None) -> None:
    """Records a CV's look in use (the caller checks the id)."""
    with _conn(con) as c:
        doc_id = _doc_id(c, doc_id)
        with writing(c):
            _update_doc(c, doc_id, look_id=look_id)


def set_page_limit(pages: int, con: sqlite3.Connection | None = None, doc_id: str | None = None) -> None:
    """Records "Keep this CV to" for a CV: 0 (no limit) to 4 pages (`PAGE_LIMITS`); CVError otherwise.

    Read it through `pagelimit.limit_of`, which falls back to the CV type's rule while none is chosen. The card
    offers 3 and 4 only for a copied type with that rule (`pagelimit.choices`); the route checks that.
    """
    if isinstance(pages, bool) or pages not in PAGE_LIMITS:
        raise CVError("pages: choose no limit, or one to four pages")
    with _conn(con) as c:
        doc_id = _doc_id(c, doc_id)
        with writing(c):
            _update_doc(c, doc_id, page_limit=pages)
    _publish("pages", doc_id, pages=pages)

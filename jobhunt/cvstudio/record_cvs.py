"""Your record's CVs as My CVs: "Use this for applications" on a CV of the record.

The record keeps CV files as sources (`record_sources`, section cv) that the AI reads into facts; My CVs keeps
the editable CVs applications are written from (`cvstudio.store`). The two meet at the file: a CV of My CVs
read from a file has that file's SHA-256 in `cv_files`, and a record source of one uploaded file has the same
SHA-256, so `store.file_cvs` says which CV of My CVs a record CV is, with no column to keep in step.

- Turning it on uses the CV the file already made (restored from the archive when it is there) and puts it
  last in the order of the CVs in use, as My CVs' own toggle does. A file that made no CV yet is read by the CV
  reader (`cv.parse`, the rules then the AI) into a new CV named after the file; the request only checks the
  file (`parse.check`) and starts the action, so the slow part never runs in a request thread.
- Turning it off takes it out of use; the store refuses for the last CV in use, in plain words.
- After either, the lead CV of the record follows (`profile.cvs.settle`): the first CV in use leads the facts.
- Only a CV of one uploaded file can be used: a folder or zip of files is no one CV file to read.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import PurePath

from jobhunt.core import runner
from jobhunt.cvstudio import parse, store
from jobhunt.sections import registry
from jobhunt.sections import store as sources

log = logging.getLogger(__name__)

SECTION = "cv"
NOT_A_CV = "Only a CV in Your CVs can be used for applications."
NOT_ONE_FILE = "This CV is a folder of files, so Hal cannot read it as one CV. Add the CV file on its own."
GONE_FILE = "Hal no longer has its copy of this file. Add it again."
BUSY = "Hal is already reading this CV. It shows in My CVs when it is done."


class NotUsable(ValueError):
    """This source cannot be one of My CVs; the message says why in plain words."""


def source_file(con: sqlite3.Connection, source_id) -> tuple[sqlite3.Row, bytes, str]:
    """(the source's row, its one file's bytes, the file's name) of a CV of the record.

    LookupError when there is no such source; `NotUsable` for a source that is not one CV file Hal still has.
    """
    r = sources.row(con, source_id)
    if r["section"] != SECTION:
        raise NotUsable(NOT_A_CV)
    rels = sources.file_rels(con, r["id"])
    if r["kind"] != registry.UPLOAD or len(rels) != 1:
        raise NotUsable(NOT_ONE_FILE)
    path = sources.files_dir(r["id"]) / rels[0]
    try:
        data = path.read_bytes()
    except FileNotFoundError as e:
        raise NotUsable(GONE_FILE) from e
    return r, data, PurePath(rels[0]).name


def my_cv(con: sqlite3.Connection, r: sqlite3.Row) -> dict | None:
    """The CV of My CVs read from this source's file (`store.file_cvs`), or None."""
    return store.file_cvs(con).get(r["sha256"])


def settle_lead(con: sqlite3.Connection | None = None) -> list[int]:
    """Lets the record's lead CV follow the CVs in use (`profile.cvs.settle`); returns the cards worked out again."""
    from jobhunt import db
    from jobhunt.profile import cvs

    if con is not None:
        return cvs.settle(con)
    own = db.connect()
    try:
        return cvs.settle(own)
    finally:
        own.close()


def use(con: sqlite3.Connection, source_id, on: bool, *, session_id: str | None = None,
        remote: bool = False) -> dict:
    """Turns "Use this for applications" on or off for a CV of the record; {"cv", "action_id"}.

    `cv` is the CV of My CVs it is (None while a new one is being read); `action_id` the `cv.parse` reading the
    file into a new CV, else None. `NotUsable` (and `parse.CVReadError`) say why a file cannot be used,
    `model.CVError` why the last CV in use stays in use, `RuntimeError` (`BUSY`) when the file is being read.
    """
    r, data, name = source_file(con, source_id)
    mine = my_cv(con, r)
    if not on:
        if mine and mine["starred"]:
            store.star(mine["id"], False, con=con)
            settle_lead(con)
        return {"cv": mine["id"] if mine else None, "action_id": None}
    if mine:
        if mine["archived"]:
            store.archive(mine["id"], False, con=con)
        store.star(mine["id"], True, con=con)
        settle_lead(con)
        return {"cv": mine["id"], "action_id": None}
    parse.check(data, name)
    file_id = store.add_file(data, name, role="baseline", con=con)
    try:
        action_id = runner.start("cv.parse", subject=f"cv.parse:{file_id}",
                                 params={"file_id": file_id, "file": name, "last": True},
                                 session_id=session_id, remote=remote)
    except RuntimeError as e:
        log.info("not reading %s again: %s", name, e)
        raise RuntimeError(BUSY) from e
    return {"cv": None, "action_id": action_id}

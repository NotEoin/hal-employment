"""Your record's CVs as My CVs: "Use this for applications" on a CV of the record.

The record keeps CV files as sources (`record_sources`, section cv) that the AI reads into facts; My CVs keeps
the editable CVs applications are written from (`cvstudio.store`). The two meet at the file: a CV of My CVs
read from a file has that file's SHA-256 in `cv_files`, and a record source of one uploaded file has the same
SHA-256, so `store.file_cvs` says which CV of My CVs a record CV is, with no column to keep in step.

- Turning it on uses the CV the file already made (restored from the archive when it is there) and puts it
  last in the order of the CVs in use, as My CVs' own toggle does. A file that made no CV yet is read by the CV
  reader (`cv.parse`, the rules then the AI) into a new CV named after the file; the request only checks the
  file (`parse.check`) and starts the action, so the slow part never runs in a request thread. A file being
  read already (an upload on My CVs) is not read twice: the request follows that reading
  (`parse_actions.start_read`), and the reading makes one CV however many asked (`store.create_for_file`).
- Turning it off takes it out of use; the store refuses for the last CV in use, in plain words.
- After either, the lead CV of the record follows (`profile.cvs.settle`): the first CV in use leads the facts.
- Only a CV of one uploaded file can be used: a folder or zip of files is no one CV file to read.
- Preview: a CV that is one of My CVs shows that CV's version in use, or what employers get while it sends its
  own file (`original.view`). Any other is read with the rules only
  (`parse.read_rules`, no AI, so it is quick and private) and drawn by the same renderer in the default look;
  it is not added to My CVs. The reading (or the reader's reason it cannot read the file) is kept in memory
  by SHA-256, so the preview's two requests and a second opening read the file once. Kept in memory, not on
  disk: it is cheap to make again and nothing else needs it.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from pathlib import PurePath

from jobhunt.cvstudio import original, parse, render, store
from jobhunt.cvstudio.model import CVDoc
from jobhunt.sections import registry
from jobhunt.sections import store as sources

log = logging.getLogger(__name__)

SECTION = "cv"
PREFIX = "/record/sources"   # the page's address of a source, under /api/v2
READINGS_KEPT = 32           # previews kept in memory: more CVs than one person keeps, each a few kilobytes
_readings: OrderedDict[str, CVDoc | str] = OrderedDict()   # SHA-256 -> the rules' reading, or why it failed
_lock = threading.Lock()
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
    `model.CVError` why the last CV in use stays in use, `RuntimeError` (`BUSY`) when the file is being read
    into another CV ("Upload a new version") or in another window.
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
    from jobhunt.cvstudio.parse_actions import start_read  # here, as parse_actions imports this module

    try:
        action_id = start_read(file_id, name, last=True, session_id=session_id, remote=remote)
    except RuntimeError as e:
        log.info("not reading %s again: %s", name, e)
        raise RuntimeError(BUSY) from e
    return {"cv": None, "action_id": action_id}


# ------------------------------------------------------------------------------------------------ preview

def _read(sha: str, data: bytes, name: str) -> CVDoc:
    """The rules' reading of a file (no AI), kept by its SHA-256 so a preview opened again is instant.

    `parse.CVReadError` says why Hal cannot read it; that answer is kept too.
    """
    with _lock:
        if sha in _readings:
            _readings.move_to_end(sha)
            found = _readings[sha]
            if isinstance(found, str):
                raise parse.CVReadError(found)
            return found
    try:
        kind, _ = parse.check(data, name)
        doc: CVDoc | str = parse.read_rules(parse.lines_of(data, kind)).doc
    except parse.CVReadError as e:
        doc = str(e)
    with _lock:
        _readings[sha] = doc
        while len(_readings) > READINGS_KEPT:
            _readings.popitem(last=False)
    if isinstance(doc, str):
        raise parse.CVReadError(doc)
    return doc


def preview(con: sqlite3.Connection, source_id) -> dict:
    """How to preview a CV of the record: {"version_id", "original", "url", "why", "original_url"}.

    A CV that is one of My CVs shows that CV's version in use (`version_id`, for `DocPreview`), or, while that CV
    sends its own file, what employers get of it (`original`, from `original.view`); any other is read
    by the rules only and shown at `url`, and adds nothing to My CVs. `why` is the reader's reason when it cannot
    read the file, with the original at `original_url` to open instead.
    """
    r, data, name = source_file(con, source_id)
    base = f"{PREFIX}/{r['id']}"
    out = {"version_id": None, "original": None, "url": None, "why": "", "original_url": f"{base}/original"}
    mine = my_cv(con, r)
    if mine and mine["version_id"]:
        return {**out, "version_id": mine["version_id"], "original": original.view(con, mine["id"])}
    try:
        _read(r["sha256"], data, name)
    except parse.CVReadError as e:
        return {**out, "why": str(e)}
    return {**out, "url": f"{base}/preview.html"}


def preview_html(con: sqlite3.Connection, source_id) -> str:
    """The rules' reading of a CV of the record as the page Hal prints, in the default look."""
    r, data, name = source_file(con, source_id)
    doc = _read(r["sha256"], data, name)
    return render.render_cv_html(doc, None, render.DEFAULT_LOOK, render.profile_from_settings())

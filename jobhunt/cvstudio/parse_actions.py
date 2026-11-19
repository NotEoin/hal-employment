"""The `cv.parse` action: reads a CV file the person added and keeps what Hal read as one version.

The route stores the file first (`store.add_file`, read-only, by SHA-256) and calls `start_read`, which keeps
Hal's quick reading at once and starts this action for the AI's, so the slow part (the AI) never runs in a
request thread. The file becomes the CV's own file (`baseline_file_id`), so "your original file" means it.

- The rules' reading is kept in the request (`keep_rules_reading`), so an upload is on My CVs within a second
  even while the AI is busy or this action waits in the queue (2026-11-19: the CV used to be made only at the
  action's end). The rules take tens of milliseconds on a CV (`parse.check` already bounds a file's pages),
  as the record's quick preview (`record_cvs`) does in a request too. My CVs marks the CV "Hal is
  reading this CV" from the running or queued action (`reading_cvs`), never from a stored flag, so a stopped or
  failed reading cannot leave the mark behind.
- Without `doc_id` the file is a new CV (`store.create_for_file`), named after the file, whose history starts
  at version 1. With `doc_id` it is "Upload a new version of this CV": one new version of that CV.
- One upload is one version ("Your file: <name> (never changed)", origin uploaded, by baseline, `file_id`
  set), never a version 1 hidden under a version 2 (2026-11-18: an upload used to make both, and a new CV
  showed as "version 9" of the old one). The rules' reading is that version at first; the AI's reading, when
  there is one, takes its place (`store.replace_reading`, same id and number). Versions the person made
  meanwhile stand. With no AI the rules' reading stays.
- A new CV is in use for applications, so the record's lead CV may change with it (`record_cvs.settle_lead`).
- One reading per file at a time (`start_read`, subject `cv.parse:<file id>`), whoever asks: My CVs' upload,
  "Upload a new version", the setup guide, a second tab, or "Use this for applications" in Your record. A
  second request for a file being read follows that reading instead of starting another, so two uploads close
  together never cancel each other (2026-11-18: one subject for every upload let the second stop the first).
  `start_read` holds a lock from its check to the action's start, so two requests in this process never make
  two versions of one upload; `store.create_for_file` checks again under the write lock, so a request in
  another process makes no second CV either.
- The report ("Check what Hal read") belongs to that version and follows the reading kept in it; corrections
  to it are new versions. Values the CV gives for `you.*` settings fill only settings that are still empty,
  once the final reading is in, so nothing the person typed is overwritten.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading

from jobhunt import db
from jobhunt.core import runner, settings
from jobhunt.core.settings.registry import SettingError
from jobhunt.cvstudio import parse, record_cvs, store

log = logging.getLogger(__name__)

UPLOADED_WHAT = "Your file: {name} (never changed)"
_STARTING = threading.Lock()   # held from the check for a reading to its start: one upload, one version
BUSY_ELSEWHERE = "Hal is already reading this file for another of your CVs. Wait until it is done, then try again."
EXAMPLE_CV = """Ada Fernhollow
Brindle Bay | ada.fernhollow@example.org | 07700 900470

EDUCATION
BA (Hons) Made-up Studies, 2:1    2022 - 2025
University of Elsewhere
- Dissertation: Lighthouses of an invented coast

EXPERIENCE
Visitor Assistant, Brindle Bay Museum    2023 - Present
- Welcomed 50 visitors a day

SKILLS
People: customer service, public speaking
"""


def _example(world) -> dict:
    """A made-up pasted CV, stored the way the upload route stores one, for gate G9."""
    file_id = store.add_file(EXAMPLE_CV.encode("utf-8"), "Pasted CV.txt", role="baseline")
    return {"file_id": file_id, "file": "Pasted CV.txt"}


def save_report(version_id: str, file_id: str, found: str, items: list[dict]) -> None:
    """Keeps the report of one reading."""
    con = db.connect()
    try:
        con.execute("INSERT OR REPLACE INTO cv_parse_reports (version_id, file_id, found, items) VALUES (?, ?, ?, ?)",
                    (version_id, file_id, found, json.dumps(items, ensure_ascii=True)))
        con.commit()
    finally:
        con.close()


def _set_pages(file_id: str, pages: int | None) -> None:
    if pages is None:
        return
    con = db.connect()
    try:
        con.execute("UPDATE cv_files SET pages = ? WHERE id = ? AND pages IS NULL", (pages, file_id))
        con.commit()
    finally:
        con.close()


def fill_empty_settings(values: dict[str, str]) -> dict[str, str]:
    """Saves each value whose setting is still empty; returns those saved. A value a setting refuses is left."""
    saved = {}
    for key, value in values.items():
        try:
            if str(settings.get(key) or "").strip():
                continue
            settings.set(key, value, by="setup")
        except (SettingError, KeyError) as e:
            log.info("left %s empty: %s", key, e)
            continue
        saved[key] = value
    return saved


def subject(file_id: str) -> str:
    """The `cv.parse` subject of one kept file: one reading of a file runs at a time."""
    return f"cv.parse:{file_id}"


def _params_of(action_id: str) -> dict:
    con = db.connect()
    try:
        row = con.execute("SELECT params FROM actions WHERE id = ?", (action_id,)).fetchone()
    finally:
        con.close()
    return json.loads(row["params"]) if row and row["params"] else {}


def reading_now(file_id: str) -> bool:
    """True while a `cv.parse` of this kept file is queued or running, here or in another live process."""
    return runner.running_for(subject(file_id)) is not None


def reading_cvs(con: sqlite3.Connection) -> set[str]:
    """The CVs whose file Hal is reading now: a queued or running `cv.parse` holds their file's subject.

    Worked out from the actions each time, never stored, so a reading that stopped or failed marks nothing.
    """
    runner.flush()
    rows = con.execute("SELECT DISTINCT subject FROM actions WHERE kind = 'cv.parse' "
                       "AND state IN ('queued','running','waiting_for_you')").fetchall()
    out: set[str] = set()
    for r in rows:
        held = runner.running_for(r["subject"]) if r["subject"] else None
        if held is None:
            continue
        params = _params_of(held["id"])
        cv = params.get("cv") or params.get("doc_id") or store.cv_for_file(params.get("file_id") or "", con=con)
        if cv:
            out.add(cv)
    return out


def _follow(file_id: str, doc_id: str | None, cause: Exception | None = None) -> str | None:
    """The id of the reading of `file_id` running now, None when there is none.

    RuntimeError (`BUSY_ELSEWHERE`) when that reading goes into another CV than `doc_id`.
    """
    running = runner.running_for(subject(file_id))
    if running is None:
        return None
    if _params_of(running["id"]).get("doc_id") != doc_id:
        raise RuntimeError(BUSY_ELSEWHERE) from cause
    return running["id"]


def keep_rules_reading(file_id: str, file: str, reading: parse.Reading, *, doc_id: str | None = None,
                       last: bool = False) -> tuple[str, str | None]:
    """Keeps the rules' reading of a kept file as its upload version; (the CV, that version's id).

    A new CV without `doc_id` (`store.create_for_file`), else the next version of `doc_id`. The version is
    None when the file made a CV already (that CV is answered). The report is saved for the version now, so
    "Check what Hal read" works while the AI reads; the settings wait for the final reading.
    """
    what = UPLOADED_WHAT.format(name=file)
    if doc_id is None:
        doc_id, made = store.create_for_file(file_id, store.name_for(file, reading.doc), reading.doc, what=what,
                                             last=last)
        record_cvs.settle_lead()
        if not made:
            return doc_id, None
        version_id = store.doc(doc_id=doc_id)["current_version_id"]
    else:
        version_id = store.new_version(reading.doc, origin="uploaded", what=what, by="baseline", file_id=file_id,
                                       doc_id=doc_id)
        store.set_baseline_file(file_id, doc_id=doc_id)
    save_report(version_id, file_id, parse.found_words(reading.doc), parse.rows(reading))
    return doc_id, version_id


def start_read(file_id: str, file: str, *, doc_id: str | None = None, last: bool = False,
               session_id: str | None = None, remote: bool = False) -> str | None:
    """Keeps Hal's quick reading of a kept file at once and starts the AI's (`cv.parse`); returns the action's
    id. When the same file is being read already, the id of that reading, so the second request follows it;
    None when the file made a CV already and nothing reads it (`store.cv_for_file` names that CV).

    RuntimeError (`BUSY_ELSEWHERE`) when that reading goes into another CV than `doc_id`;
    `parse.CVReadError` when the file cannot be read.
    """
    with _STARTING:
        following = _follow(file_id, doc_id)
        if following is not None:
            log.info("%s is being read already: following that reading", file)
            return following
        data = store.file_bytes(file_id)
        kind, _ = parse.check(data, file)
        reading = parse.read_rules(parse.lines_of(data, kind))
        cv, version_id = keep_rules_reading(file_id, file, reading, doc_id=doc_id, last=last)
        if version_id is None:
            return _follow(file_id, doc_id)
        params: dict = {"file_id": file_id, "file": file, "cv": cv, "version_id": version_id}
        if doc_id:
            params["doc_id"] = doc_id
        if last:
            params["last"] = True
        try:
            return runner.start("cv.parse", subject=subject(file_id), params=params, session_id=session_id,
                                remote=remote)
        except RuntimeError as e:
            following = _follow(file_id, doc_id, e)
            if following is None:
                raise
            log.info("%s is being read in another window: following that reading", file)
            return following


@runner.action("cv.parse", area="cv", label="Reading your CV", why="You added {file}", priority=0,
               example=_example)
def parse_cv(ctx, file_id: str, file: str, doc_id: str | None = None, last: bool = False,
             version_id: str | None = None, cv: str | None = None) -> dict:
    """Reads the file with the rules, then the AI, and keeps the AI's reading in the upload version
    `version_id` that `start_read` made (the first of a new CV, or the next of `doc_id`).

    Without `version_id` (gate G9's example, or a reading queued by an older Hal and carried on after a
    restart) the rules' reading is kept here first, as `start_read` would. A new CV goes first among the CVs in
    use, as an upload is meant to be used; with `last` (turned on from Your record) it goes last, as My CVs'
    own toggle puts a CV.
    """
    ctx.step(f"Opening {file}")
    data = store.file_bytes(file_id)
    kind, pages = parse.check(data, file)
    _set_pages(file_id, pages)
    lines = parse.lines_of(data, kind)
    ctx.step("Finding the sections, dates and contact details", total=3)
    reading = parse.read_rules(lines)
    if version_id is None:
        cv, version_id = keep_rules_reading(file_id, file, reading, doc_id=doc_id, last=last)
        if version_id is None:
            ctx.changed(f"{file} is your CV \"{store.doc(doc_id=cv)['name']}\" already.")
            current = store.doc(doc_id=cv)["current_version_id"]
            return {"version_id": current, "cv": cv, "n": store.version(current)["n"], "found": "",
                    "pages": pages, "how": reading.how, "unsure": [], "filled": []}
    cv = cv or doc_id or store.version(version_id)["doc_id"]
    ctx.step("Asking the AI to check Hal's reading")
    ai_reading, why_not = parse.read_with_ai(lines)
    if ai_reading is None:
        ctx.step(f"Kept the reading from Hal's own rules: {why_not}")
    else:
        ctx.used("ai", "cv_parse")
        reading = ai_reading
        if not store.replace_reading(version_id, reading.doc):
            ctx.changed(f"Read {file}, but its version of your CV is gone, so Hal kept nothing.")
            return {"version_id": None, "cv": cv, "n": None, "found": "", "pages": pages, "how": reading.how,
                    "unsure": [], "filled": []}
    rows = parse.rows(reading)
    found = parse.found_words(reading.doc)
    save_report(version_id, file_id, found, rows)
    filled = fill_empty_settings(parse.prefill(reading.doc))
    unsure = [r for r in rows if not r["sure"]]
    ctx.changed(f"Read {file}: found {found}.")
    return {"version_id": version_id, "cv": cv, "n": store.version(version_id)["n"], "found": found,
            "pages": pages, "how": reading.how,
            "unsure": [{"path": r["path"], "label": r["label"], "value": r["value"]} for r in unsure],
            "filled": sorted(filled)}

"""The `cv.parse` action: reads a CV file the person added and keeps what Hal read as one version.

The route stores the file first (`store.add_file`, read-only, by SHA-256) and starts this action with the
file's id, so the slow part (reading, the AI) never runs in a request thread. The file becomes the CV's own
file (`baseline_file_id`), so "your original file" means it.

- Without `doc_id` the file is a new CV (`store.create`), named after the file, whose history starts at
  version 1. With `doc_id` it is "Upload a new version of this CV": one new version of that CV.
- Hal reads twice, with its rules and then the AI, and keeps one reading: the AI's when there is one, else the
  rules'. So a file uploaded once is one version ("Your file: <name> (never changed)", origin uploaded, by
  baseline, `file_id` set), never a version 1 hidden under a version 2 (2026-11-18: an upload used to make
  both, and a new CV showed as "version 9" of the old one). The rules' reading is not kept: with no AI it is
  the reading, and the AI's reading is what the person checks.
- A new CV is in use for applications, so the record's lead CV may change with it (`record_cvs.settle_lead`).
- One reading per file at a time (`start_read`, subject `cv.parse:<file id>`), whoever asks: My CVs' upload,
  "Upload a new version", the setup guide, a second tab, or "Use this for applications" in Your record. A
  second request for a file being read follows that reading instead of starting another, so two uploads close
  together never cancel each other (2026-11-18: one subject for every upload let the second stop the first).
  A request checks for a CV the file made already only when it starts, so a reading that started just as
  another ended makes no CV: `store.create_for_file` checks again under the write lock and answers that CV.
- The report ("Check what Hal read") belongs to that version, and corrections to it are new versions.
  Values the CV gives for `you.*` settings fill only settings that are still empty, so nothing the person
  typed is overwritten.
"""
from __future__ import annotations

import json
import logging

from jobhunt import db
from jobhunt.core import runner, settings
from jobhunt.core.settings.registry import SettingError
from jobhunt.cvstudio import parse, record_cvs, store

log = logging.getLogger(__name__)

UPLOADED_WHAT = "Your file: {name} (never changed)"
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


def start_read(file_id: str, file: str, *, doc_id: str | None = None, last: bool = False,
               session_id: str | None = None, remote: bool = False) -> str:
    """Starts reading a kept file (`cv.parse`) and returns the action's id; when the same file is being read
    already, the id of that reading, so the second request follows it.

    RuntimeError (`BUSY_ELSEWHERE`) when that reading goes into another CV than `doc_id`.
    """
    params: dict = {"file_id": file_id, "file": file}
    if doc_id:
        params["doc_id"] = doc_id
    if last:
        params["last"] = True
    try:
        return runner.start("cv.parse", subject=subject(file_id), params=params, session_id=session_id,
                            remote=remote)
    except RuntimeError as e:
        running = runner.running_for(subject(file_id))
        if running is None:
            raise
        if _params_of(running["id"]).get("doc_id") != doc_id:
            raise RuntimeError(BUSY_ELSEWHERE) from e
        log.info("%s is being read already: following that reading", file)
        return running["id"]


@runner.action("cv.parse", area="cv", label="Reading your CV", why="You added {file}", priority=0,
               example=_example)
def parse_cv(ctx, file_id: str, file: str, doc_id: str | None = None, last: bool = False) -> dict:
    """Reads the file into sections with the rules, then the AI, and keeps the reading as one version: the first
    of a new CV, or the next of `doc_id`. A new CV goes first among the CVs in use, as an upload is meant to be
    used; with `last` (turned on from Your record) it goes last, as My CVs' own toggle puts a CV."""
    ctx.step(f"Opening {file}")
    data = store.file_bytes(file_id)
    kind, pages = parse.check(data, file)
    _set_pages(file_id, pages)
    lines = parse.lines_of(data, kind)
    ctx.step("Finding the sections, dates and contact details", total=3)
    reading = parse.read_rules(lines)
    ctx.step("Asking the AI to check Hal's reading")
    ai_reading, why_not = parse.read_with_ai(lines)
    if ai_reading is None:
        ctx.step(f"Kept the reading from Hal's own rules: {why_not}")
    else:
        ctx.used("ai", "cv_parse")
        reading = ai_reading
    what = UPLOADED_WHAT.format(name=file)
    if doc_id is None:
        doc_id, made = store.create_for_file(file_id, store.name_for(file, reading.doc), reading.doc, what=what,
                                             last=last)
        record_cvs.settle_lead()
        version_id = store.doc(doc_id=doc_id)["current_version_id"]
        if not made:
            ctx.changed(f"{file} is your CV \"{store.doc(doc_id=doc_id)['name']}\" already.")
            return {"version_id": version_id, "cv": doc_id, "n": store.version(version_id)["n"], "found": "",
                    "pages": pages, "how": reading.how, "unsure": [], "filled": []}
    else:
        version_id = store.new_version(reading.doc, origin="uploaded", what=what, by="baseline", file_id=file_id,
                                       doc_id=doc_id)
        store.set_baseline_file(file_id, doc_id=doc_id)
    rows = parse.rows(reading)
    found = parse.found_words(reading.doc)
    save_report(version_id, file_id, found, rows)
    filled = fill_empty_settings(parse.prefill(reading.doc))
    unsure = [r for r in rows if not r["sure"]]
    ctx.changed(f"Read {file}: found {found}.")
    return {"version_id": version_id, "cv": doc_id, "n": store.version(version_id)["n"], "found": found,
            "pages": pages, "how": reading.how,
            "unsure": [{"path": r["path"], "label": r["label"], "value": r["value"]} for r in unsure],
            "filled": sorted(filled)}

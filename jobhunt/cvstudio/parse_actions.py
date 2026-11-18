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
from jobhunt.cvstudio import parse, store

log = logging.getLogger(__name__)

UPLOADED_WHAT = "Your file: {name} (never changed)"
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


@runner.action("cv.parse", area="cv", label="Reading your CV", why="You added {file}", priority=0,
               example=_example)
def parse_cv(ctx, file_id: str, file: str, doc_id: str | None = None) -> dict:
    """Reads the file into sections with the rules, then the AI, and keeps the reading as one version: the first
    of a new CV, or the next of `doc_id`."""
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
        doc_id = store.create(store.name_for(file, reading.doc), reading.doc, origin="uploaded", what=what,
                              by="baseline", file_id=file_id)
        version_id = store.doc(doc_id=doc_id)["current_version_id"]
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

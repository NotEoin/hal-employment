"""The `cv.parse` action: reads a CV file the person added and keeps what Hal read as versions.

The route stores the file first (`store.add_file`, read-only, by SHA-256) and starts this action with the
file's id, so the slow part (reading, the AI) never runs in a request thread. Versions it makes:

1. "Your file: <name> (never changed)", origin uploaded, by baseline, with `file_id` set: the rules'
   reading, so the CV is usable at once and without an AI.
2. "Hal read your uploaded CV into sections", origin read, by hal: only when the AI's reading differs.

The report ("Check what Hal read") belongs to the last of these. Values the CV gives for `you.*` settings
fill only settings that are still empty, so nothing the person typed is overwritten.
"""
from __future__ import annotations

import json
import logging

from jobhunt import db
from jobhunt.core import runner, settings
from jobhunt.core.settings.registry import SettingError

from . import parse, store
from .model import to_json

log = logging.getLogger(__name__)

UPLOADED_WHAT = "Your file: {name} (never changed)"
READ_WHAT = "Hal read your uploaded CV into sections"
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
def parse_cv(ctx, file_id: str, file: str) -> dict:
    """Reads the file into sections with the rules, then the AI, and keeps each reading as a version."""
    ctx.step(f"Opening {file}")
    data = store.file_bytes(file_id)
    kind, pages = parse.check(data, file)
    _set_pages(file_id, pages)
    lines = parse.lines_of(data, kind)
    ctx.step("Finding the sections, dates and contact details", total=3)
    rules = parse.read_rules(lines)
    version_id = store.new_version(rules.doc, origin="uploaded", what=UPLOADED_WHAT.format(name=file),
                                   by="baseline", file_id=file_id)
    reading = rules
    ctx.step("Asking the AI to check Hal's reading")
    ai_reading, why_not = parse.read_with_ai(lines)
    if ai_reading is None:
        ctx.step(f"Kept the reading from Hal's own rules: {why_not}")
    else:
        ctx.used("ai", "cv_parse")
        if to_json(ai_reading.doc) != to_json(rules.doc):
            version_id = store.new_version(ai_reading.doc, origin="read", what=READ_WHAT, by="hal",
                                           parent=version_id)
        reading = ai_reading
    rows = parse.rows(reading)
    found = parse.found_words(reading.doc)
    save_report(version_id, file_id, found, rows)
    filled = fill_empty_settings(parse.prefill(reading.doc))
    unsure = [r for r in rows if not r["sure"]]
    ctx.changed(f"Read {file}: found {found}.")
    return {"version_id": version_id, "found": found, "pages": pages, "how": reading.how,
            "unsure": [{"path": r["path"], "label": r["label"], "value": r["value"]} for r in unsure],
            "filled": sorted(filled)}

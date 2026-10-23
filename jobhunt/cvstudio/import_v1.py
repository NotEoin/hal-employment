"""The one-time import of today's CV into the CV store: data task `cv.import_v1`.

- The owner's CV is the importer's copy of the website's `cv.json` (`data/import/cv/site-cv.json`) with
  `cv-overrides.json` laid over it. A USB-stick install's is `cv-built.json["cv"]` with `cv-built-overrides.json`.
  After this Hal keeps its own copy and never reads the website again.
- Overrides are matched to entries by the name rule of the old code (`NAMED_BY`) one last time, against the
  entry's original name, so an edit made after a rename is kept (the old code dropped it). Every version
  made here gets the same ids, because ids come from the original names.
- Versions: 1 the CV as it came; then each `cv-overrides.before-*.json` snapshot, oldest first; then the CV
  with today's overrides, which is put in use.
- The check: every string of the printable CV must be found in the imported document. What is not is
  listed in the report and in the last version's `extras["not_found"]`, and a clean import leaves that list empty.
- Running it again does nothing once a CV has been imported, so the server can start it every time.
"""
from __future__ import annotations

import copy
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jobhunt.core import clock, paths
from jobhunt.core.db import tasks
from jobhunt.cvstudio import model, store

log = logging.getLogger(__name__)

NAME = "cv.import_v1"
AFTER = 11                      # migration 0011 made the CV store
TITLE = "Moved your CV into Hal's own copy"
NAMED_BY = {"education": "qualification", "projects": "name", "experience": "role"}   # the rule the old `cv.py` used
PAIRS = ("skills", "additional")
SNAPSHOTS = "cv-overrides.before-*.json"
OWNER = {"site": "site-cv.json", "overrides": "cv-overrides.json", "type": "technical_grad",
         "first": "Your CV from your website, imported {date}", "from": "site-cv.json+overrides"}
FRIEND = {"site": "cv-built.json", "overrides": "cv-built-overrides.json", "type": "humanities_grad",
          "first": "Your CV from your old Hal", "from": "cv-built.json+overrides"}
LAST = "With the changes you made in Hal"
SNAPSHOT = "Your saved copy from {date}"


def folder() -> Path:
    """Where the importer left the old CV files: `data/import/cv/`, or loose in `data/` in the old layout."""
    return paths.data_dir() if paths.is_legacy_layout() else paths.data_dir() / "import" / "cv"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _find(data: dict, section: str, key):
    items = data.get(section) or []
    if section in PAIRS:
        return next((x for x in items if x[0] == key), None)
    return next((x for x in items if x.get("_key") == key), None)


def apply_overrides(site: dict, changes: list[dict]) -> dict:
    """The old CV with the person's changes laid over it, matched by original name; hidden entries kept."""
    data = copy.deepcopy(site)
    for section in NAMED_BY:
        for item in data.get(section) or []:
            item["_key"] = item.get(NAMED_BY[section]) or ""
    for c in changes:
        if "section" not in c:
            data[c["field"]] = copy.deepcopy(c["value"])
            continue
        item = _find(data, c["section"], c["key"])
        if item is None:
            log.warning("a saved change to %s names an entry that is not there any more", c["section"])
            continue
        item[c["field"]] = copy.deepcopy(c["value"])
    return data


def printable(data: dict) -> dict:
    """The old CV as its PDF took it: entries left out dropped, the editor's bookkeeping removed."""
    data = copy.deepcopy(data)
    for section in NAMED_BY:
        data[section] = [{k: v for k, v in x.items() if k not in ("_key", "_id", "hidden")}
                         for x in data.get(section) or [] if not x.get("hidden")]
    for section in PAIRS:
        data[section] = [x for x in data.get(section) or [] if x[1]]
    return data


def _date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).date().isoformat()


def plan(where: Path | None = None) -> dict | None:
    """What there is to import: {who, site, overrides, snapshots}, or None when there is nothing."""
    where = where or folder()
    origin = where / "cv-origin"
    built = origin.exists() and origin.read_text(encoding="utf-8").strip() == "built"
    who = FRIEND if built or not (where / OWNER["site"]).exists() else OWNER
    if not (where / who["site"]).exists():
        return None
    snapshots = sorted(where.glob(SNAPSHOTS), key=lambda p: (p.stat().st_mtime, p.name)) if who is OWNER else []
    return {"who": who, "site": where / who["site"], "overrides": where / who["overrides"], "snapshots": snapshots}


def _already(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM cv_versions WHERE origin = 'imported' LIMIT 1").fetchone() is not None


def import_into(con: sqlite3.Connection, where: Path | None = None) -> dict:
    """Imports the old CV as versions, once; returns {versions: [ids], not_found: [strings], skipped: why}."""
    if _already(con):
        return {"versions": [], "not_found": [], "skipped": "Your CV was imported before."}
    found = plan(where)
    if found is None:
        return {"versions": [], "not_found": [], "skipped": "There was no CV from before to import."}
    who = found["who"]
    site = _read(found["site"])
    if who is FRIEND:
        site = site["cv"]
    overrides = _read(found["overrides"]) if found["overrides"].exists() else []
    stamp = {"from": who["from"], "imported": clock.iso()}
    store.doc(con)
    con.execute("UPDATE cv_documents SET type_id = ?, look_id = ?, route = 'improve', send = 'hal_copy' "
                "WHERE id = ?", (who["type"], store.DEFAULT_LOOK, store.DOC_ID))
    con.commit()

    def version(data: dict, what: str, by: str) -> tuple[str, list[str]]:
        doc = model.from_v1(data)
        missing = model.not_found(printable(data), doc)
        doc.extras.update(stamp)
        if missing:
            doc.extras["not_found"] = missing
        return store.new_version(doc, origin="imported", what=what, by=by, type_id=who["type"], con=con), missing

    made = []
    first, _ = version(apply_overrides(site, []), who["first"].format(date=_date(found["site"])), "baseline")
    made.append(first)
    for snap in found["snapshots"]:
        vid, _ = version(apply_overrides(site, _read(snap)), SNAPSHOT.format(date=_date(snap)), "you")
        made.append(vid)
    missing: list[str] = []
    if overrides:
        vid, missing = version(apply_overrides(site, overrides), LAST, "you")
        made.append(vid)
    else:
        missing = model.not_found(printable(site), store.get(first, con=con))
    log.info("imported the CV as %d versions; %d strings not found", len(made), len(missing))
    return {"versions": made, "not_found": missing, "skipped": ""}


def summary(out: dict) -> str:
    """One plain line for Activity: how many versions, and whether anything was not found."""
    if out["skipped"]:
        return out["skipped"]
    n = len(out["versions"])
    line = f"Your CV is now Hal's own copy, in {n} version{'s' if n != 1 else ''}."
    if out["not_found"]:
        line += f" {len(out['not_found'])} pieces of text were not found in it; see My CV, Versions."
    return line


@tasks.data_task(NAME, after=AFTER, title=TITLE)
def run(ctx) -> None:
    """The data task body: imports the old CV once and says what happened."""
    out = import_into(ctx.con)
    step = getattr(ctx, "step", None)
    if callable(step):
        step(summary(out))

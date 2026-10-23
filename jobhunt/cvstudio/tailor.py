"""Adjusting Hal's copy of the CV for one job ("Adjust for each job").

- No: the current version as it is.
- A little: entries in the sections that may move (projects, skill groups and the like) are reordered by
  how many of the job's words they share, generalising L cv.py:211-235, plus one AI change to the profile
  line, which must name its evidence.
- More: also up to three bullets rewritten by the AI, through the same guards as every suggestion.

A job's CV goes out after one look, so an AI change never carries a gap and never adds what came of the
work (a Results or Add suggestion): those need the person's own facts and stay on My CV's Suggestions.

While the person sends their original file the level is forced to No, with the words the documents step
shows. With `cvdocs.pick_first`, `priorities` are the three to five requirements the person ticked, and only
they are used. The result is kept in `job_cvs` with its change list, so each
change can be undone and redone before approval; `approve` saves the CV as a tailored version for that job.

Design decision: a change list entry stores the model change that makes it (`op`, `target`, `value`), and
the job's CV is always rebuilt from the base version plus the changes still on. Undo and Redo are then order
free and can never drift from what the list shows.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3

from jobhunt import db
from jobhunt.core import clock, events
from jobhunt.cvstudio import suggest

log = logging.getLogger(__name__)

LEVELS = ("no", "little", "more")
MAX_BULLETS = 3                     # "More": up to three bullets rewritten
MIN_WORD = 3                        # shorter words ("a", "of", "to") say nothing about a job
# Sections whose entries Hal may put in a different order for a job; dated history keeps its order.
MOVABLE_KINDS = ("projects", "skills", "achievements", "certifications", "publications", "portfolio", "talks")
REWRITE_KINDS = ("experience", "volunteering", "projects")
ADDS_FACTS = ("Results", "Add", "Gap")   # suggestion kinds that add what came of the work; a job's CV only rewords
STOP = frozenset("and the for with you your our will who are this that from have has can able into any all "
                 "role team work working job jobs using use including such other strong good".split())
ORIGINAL_WHY = ("Adjusting for each job is off while you send your original file. Employers get it exactly as "
                "you uploaded it.")
LEVEL_WORDS = {"little": "Adjusting a little", "more": "Adjusting more"}


def _store():
    from jobhunt.cvstudio import store

    return store


def _model():
    from jobhunt.cvstudio import model

    return model


def words(text: str) -> set[str]:
    """The words of `text` that can tell one job from another."""
    return {w for w in re.findall(r"[a-z][a-z0-9+#.-]*", (text or "").lower()) if len(w) >= MIN_WORD and w not in STOP}


def level_for(level: str | None = None) -> tuple[str, str]:
    """The level to use and, when it was forced to No, why in plain words."""
    from jobhunt.core import settings

    if _store().doc()["send"] == "original":
        return "no", ORIGINAL_WHY
    level = level or str(settings.get("cvdocs.tailor"))
    if level not in LEVELS:
        raise ValueError(f"Adjust for each job is one of {', '.join(LEVELS)}, not {level!r}.")
    return level, ""


def requirements(job: dict, con: sqlite3.Connection | None = None) -> list[str]:
    """The job's requirements the person has not switched off, else the advert's own list."""
    own = con is None
    con = con or db.connect()
    try:
        rows = con.execute("SELECT text, quote FROM job_requirements WHERE job_id = ? AND state != 'off' ORDER BY n",
                           (job.get("id"),)).fetchall()
    finally:
        if own:
            con.close()
    if rows:
        return [r["text"] for r in rows]
    found = job.get("requirements") or []
    return [str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in found]


def _overlap(entry, wanted: set[str]) -> int:
    text = " ".join([entry.heading, entry.org, entry.meta, entry.text, *entry.items,
                     *(b.text for b in entry.bullets)])
    return len(words(text) & wanted)


def _reorders(doc, type_, wanted: set[str]) -> list[dict]:
    out = []
    for sec in doc.sections:
        rules = type_.sections.get(sec.id) or type_.sections.get(sec.kind) or {}
        if sec.kind not in MOVABLE_KINDS or sec.locked or rules.get("fixed_order") or not sec.visible:
            continue
        shown = [e for e in sec.entries if not e.hidden]
        ranked = sorted(shown, key=lambda e: -_overlap(e, wanted))
        if [e.id for e in ranked] == [e.id for e in shown] or _overlap(ranked[0], wanted) == 0:
            continue
        hit = sorted(words(" ".join([ranked[0].heading, *ranked[0].items])) & wanted)[:3]
        out.append({"kind": "Moved", "where": sec.title, "op": "reorder", "target": f"s:{sec.id}",
                    "value": [e.id for e in ranked] + [e.id for e in sec.entries if e.hidden],
                    "before": ", ".join(e.heading or e.org for e in shown),
                    "after": ", ".join(e.heading or e.org for e in ranked),
                    "why": f"The advert asks for {', '.join(hit) or 'what this entry shows'}. "
                           f"Putting {ranked[0].heading or ranked[0].org} first makes it easy to spot.",
                    "ev": ""})
    return out


def _ai_change(con, doc, type_, sids: list[str], job: dict, ctx, *, bullets: bool) -> dict | None:
    draft = suggest.ask_ai(con, doc, type_, sids, [], suggest.passages_for(doc, sids), job=job)
    if draft is None or ("/b:" in draft.target) != bullets or (not bullets and not draft.evidence):
        return None
    if draft.kind in ADDS_FACTS:
        return None    # Claude added "giving staff a searchable record" to a job's CV (checked 2026-10-23)
    ok = suggest.guard(draft, ctx)
    if ok is None or suggest.GAP.search(ok.afters[0]):
        return None    # a job's CV goes out after one look, so it never carries a gap to fill in
    return {"kind": "Reworded", "where": suggest.where(doc, ok.target), "op": "replace", "target": ok.target,
            "value": ok.afters[0], "before": ok.before, "after": ok.afters[0], "why": ok.why,
            "ev": "; ".join(e["label"] for e in ok.evidence)}


def for_job(job: dict, *, level: str | None = None, priorities: list[str] | None = None) -> tuple[object, list[dict]]:
    """(the CV for this job, its change list); level defaults to `cvdocs.tailor`, No while the original is sent."""
    _, doc = _store().current()
    level, _ = level_for(level)
    if level == "no":
        return doc, []
    type_ = suggest.cv_type()
    con = db.connect()
    try:
        wanted = words(" ".join(priorities or requirements(job, con)) + " " + str(job.get("title") or ""))
        changes = _reorders(doc, type_, wanted)
        if suggest.ai_ready():
            ctx = suggest.context(con, doc, type_)
            summary = [s.id for s in doc.sections if s.kind == "summary" and s.visible]
            got = _ai_change(con, doc, type_, summary, job, ctx, bullets=False) if summary else None
            changes += [got] if got else []
            if level == "more":
                for sec in [s for s in doc.sections if s.kind in REWRITE_KINDS and s.visible]:
                    if sum(1 for c in changes if "/b:" in c["target"]) >= MAX_BULLETS:
                        break
                    got = _ai_change(con, doc, type_, [sec.id], job, ctx, bullets=True)
                    changes += [got] if got else []
    finally:
        con.close()
    for i, change in enumerate(changes, 1):
        change.update(id=f"c{i}", undone=False)
    return build(doc, changes), changes


def build(base, changes: list[dict]):
    """The base document with every change that is still on."""
    model = _model()
    doc = base
    for c in changes:
        if not c.get("undone"):
            doc = model.apply(doc, model.Change(c["op"], c["target"], c["value"]))
    return doc


def prepare(job: dict, *, level: str | None = None, priorities: list[str] | None = None) -> dict:
    """Works out the job's CV and keeps it in `job_cvs`; returns the row as the documents step shows it."""
    base_vid, _ = _store().current()
    used, why = level_for(level)
    _, changes = for_job(job, level=level, priorities=priorities)
    con = db.connect()
    try:
        con.execute("INSERT OR REPLACE INTO job_cvs (job_id, base_version_id, version_id, level, priorities, changes, "
                    "approved) VALUES (?, ?, NULL, ?, ?, ?, NULL)",
                    (job["id"], base_vid, used,
                     json.dumps(priorities) if priorities else None, json.dumps(changes)))
        con.commit()
    finally:
        con.close()
    events.publish("application", id=job["id"])
    return shown(job["id"]) | {"why_no": why}


def _row(con: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM job_cvs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("Hal has not adjusted your CV for this job.")
    return row


def shown(job_id: str) -> dict:
    """The job's change list with each change's state, for the documents step."""
    con = db.connect()
    try:
        row = _row(con, job_id)
    finally:
        con.close()
    changes = json.loads(row["changes"])
    return {"job_id": job_id, "level": row["level"], "base_version_id": row["base_version_id"],
            "version_id": row["version_id"], "approved": row["approved"],
            "changes": [{k: c.get(k, "") for k in ("id", "kind", "where", "target", "before", "after", "why", "ev")}
                        | {"on": not c.get("undone")} for c in changes]}


def set_change(job_id: str, change_id: str, on: bool) -> dict:
    """Undo (`on` false) or Redo one change before approval."""
    con = db.connect()
    try:
        row = _row(con, job_id)
        if row["approved"]:
            raise RuntimeError("You approved this CV already. Make the documents again to change it.")
        changes = json.loads(row["changes"])
        found = next((c for c in changes if c["id"] == change_id), None)
        if found is None:
            raise LookupError("That change is not in this job's CV.")
        found["undone"] = not on
        con.execute("UPDATE job_cvs SET changes = ? WHERE job_id = ?", (json.dumps(changes), job_id))
        con.commit()
    finally:
        con.close()
    events.publish("application", id=job_id)
    return shown(job_id)


def document(job_id: str):
    """The job's CV as it stands: the base version with the changes still on."""
    con = db.connect()
    try:
        row = _row(con, job_id)
    finally:
        con.close()
    return build(_store().get(row["base_version_id"]), json.loads(row["changes"]))


def approve(job_id: str, job: dict | None = None) -> str:
    """Saves the job's CV as a tailored version (or keeps the base when nothing changed); returns its id."""
    con = db.connect()
    try:
        row = _row(con, job_id)
    finally:
        con.close()
    changes = [c for c in json.loads(row["changes"]) if not c.get("undone")]
    vid = row["base_version_id"]
    if changes:
        job = job or {}
        what = f"Adjusted for {job.get('title') or 'one job'}" + (f" at {job['company']}" if job.get("company") else "")
        vid = _store().new_version(document(job_id), origin="tailored", what=what, by="you",
                                   parent=row["base_version_id"], job_id=job_id)
    con = db.connect()
    try:
        con.execute("UPDATE job_cvs SET version_id = ?, approved = ? WHERE job_id = ?", (vid, clock.iso(), job_id))
        con.commit()
    finally:
        con.close()
    return vid

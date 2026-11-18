"""Adjusting Hal's copy of the CV for one job.

- No: the current version as it is.
- A little: entries in the sections that may move (projects, skill groups and the like) are reordered by
  how many of the job's words they share, plus one AI change to the profile
  line, which must name its evidence.
- More: also up to three bullets rewritten by the AI, through the same guards as every suggestion.

A job's CV goes out after one look, so an AI change never carries a gap and never adds what came of the
work (a Results or Add suggestion): those need the person's own facts and stay on My CV's Suggestions.

While the person sends their original file the level is forced to No, with the words the documents step
shows. With `cvdocs.pick_first`, `priorities` are the three to five requirements the person ticked, and only
they are used. The result is kept in `job_cvs` with its change list, so each
change can be undone and redone before approval; `approve` saves the CV as a tailored version for that job.

Ticking (`to_pick`, `picked`): the documents are written before the person opens the application, so Hal
first makes the job's CV from the requirements still on, and while `cvdocs.pick_first` is on the CV step
asks for the picks before anything else on that CV. The picks make the job's CV again (`prepare`), so they
are asked only while the person has not edited it, and they are kept in `job_cvs.priorities`, so Make the
documents again uses them too.

Page limit: Hal's changes keep the job's CV to the page limit of the CV it is built from (`pagelimit.limit_of`),
counted on the PDF employers get (`fit`, in `prepare`). Over it, Hal undoes its own changes that add words,
newest first, and marks them `held`, so the CV tab says why and Redo puts one back; when the CV was over before
Hal changed anything, Hal keeps it as it was. The person's edits are never undone, only warned about.

Design decision: a change list entry stores the model change that makes it (`op`, `target`, `value`), and
the job's CV is always rebuilt from the base version plus the changes still on. Undo and Redo are then order
free and can never drift from what the list shows.

Applications: `pack.build` calls `prepare` and prints `v1(job_id)` through the same looks as My CV
(`cv.v1_of`). An application is on this path when it has a `job_cvs` row (`kept`); one whose documents were
written before keeps the older `cv.tailor` until it is sent, as nothing is stored to rebuild it here. The
base is one of the person's CVs, the one `cvstudio/choose.py` chose for the job or the person picked, kept with
the row (`base_choice`). The job's CV stays on the version it was made from, so a later change on My CV does
not reach it. A title line
chosen by the advert's words (`cv.TRACKS`, `cv.track_for`) is a change like the others.

The application's CV step edits the job's CV with the same editor as My CV (`editor`, `save_edits`): each
save of the person's edits is appended to the same list with `by` "you", after Hal's changes, so the CV is
still base plus changes and Undo of one of Hal's changes keeps the person's edits. Where both changed one line,
the person's words stand. `copy_to_main` copies one entry's lines into the CV the job's CV was built from
through `edit.save`, the same base-checked save as My CV's editor.
"""
from __future__ import annotations

import contextlib
import copy
import json
import logging
import re
import sqlite3
from collections.abc import Iterator

from jobhunt import db
from jobhunt.core import clock, events
from jobhunt.cvstudio import inline, suggest

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
YOU = "you"                         # `by` of a change the person made in the job's CV editor
APPROVED = "You approved this CV already. Make the documents again to change it."
ORIGINAL_WHY = ("Adjusting for each job is off while you send your original file. Employers get it exactly as "
                "you uploaded it.")
LEVEL_WORDS = {"little": "Adjusting a little", "more": "Adjusting more"}
MAIN_LABEL = "your main CV"         # what Copy names the CV it copies into while only one CV is in use
MIN_PICKS, MAX_PICKS = 3, 5       # "the three to five things the job cares about most" (`cvdocs.pick_first`)
# `held` on a change of Hal's it undid to keep the job's CV to its page limit (`fit`): to fit, or because the CV
# was over before Hal changed anything. Redo clears it.
HELD_FIT, HELD_BASE = "fit", "base"
# Counts `fit` makes at most: Hal's changes for one job are a title line, a few reorders, the profile line and
# up to `MAX_BULLETS` lines, and each count draws the CV once (milliseconds).
FIT_TRIES = 8


def _store():
    from jobhunt.cvstudio import store

    return store


def _model():
    from jobhunt.cvstudio import model

    return model


@contextlib.contextmanager
def _conn(con: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """The caller's connection, which the caller commits, else one of our own, committed and closed here."""
    if con is not None:
        yield con
        return
    own = db.connect()
    try:
        yield own
        own.commit()
    finally:
        own.close()


def words(text: str) -> set[str]:
    """The words of `text` that can tell one job from another."""
    return {w for w in re.findall(r"[a-z][a-z0-9+#.-]*", (text or "").lower()) if len(w) >= MIN_WORD and w not in STOP}


def level_for(level: str | None = None, doc_id: str | None = None) -> tuple[str, str]:
    """The level to use for a job's CV built on CV `doc_id` (the lead CV by default) and, when it was forced to
    No, why in plain words."""
    from jobhunt.core import settings

    if _store().doc(doc_id=doc_id)["send"] == "original":
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
    text = " ".join([entry.heading, entry.org, entry.meta, inline.plain(entry.text), *entry.items,
                     *(inline.plain(b.text) for b in entry.bullets)])
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


def _track(doc, job: dict) -> list[dict]:
    """The title line `cv.track_for` picks from the advert's words, as a change; none when it picks none."""
    from jobhunt.cvstudio import cv

    track = cv.track_for(f"{job.get('title') or ''}\n{job.get('description') or ''}")
    title = cv.TRACKS[track][0] if track else ""
    if not title or title == doc.headline:
        return []
    return [{"kind": "Reworded", "where": "The line under your name", "op": "replace", "target": "headline",
             "value": title, "before": doc.headline, "after": title,
             "why": f"The advert reads as a {track} role.", "ev": ""}]


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


def for_job(job: dict, *, level: str | None = None, priorities: list[str] | None = None,
            base=None) -> tuple[object, list[dict]]:
    """(the CV for this job, its change list); level defaults to `cvdocs.tailor`, No while the original is sent.

    Built on `base` (a `choose.Choice`: its CV and version), else on the lead CV's version in use.
    """
    if base is None:
        doc_id, (_, doc) = None, _store().current()
    else:
        doc_id, doc = base.doc_id, _store().get(base.version_id)
    level, _ = level_for(level, doc_id)
    if level == "no":
        return doc, []
    type_ = suggest.cv_type(doc_id=doc_id)
    con = db.connect()
    try:
        wanted = words(" ".join(priorities or requirements(job, con)) + " " + str(job.get("title") or ""))
        changes = _track(doc, job) + _reorders(doc, type_, wanted)
        if suggest.ai_ready():
            ctx = suggest.context(con, doc, type_, doc_id=doc_id)
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
            doc = model.apply(doc, model.Change(c["op"], c["target"], c.get("value"), c.get("position")))
    return doc


def _longer(change: dict) -> bool:
    """True when a change of Hal's adds words: its wording is longer than the words it replaced."""
    return len(str(change.get("after") or "").split()) > len(str(change.get("before") or "").split())


def _counted(base, changes: list[dict], count) -> int | None:
    """The pages of the base with `changes` still on, by `count`; None when it cannot be counted."""
    try:
        return count(build(base, changes))
    except _model().CVError as e:
        log.info("could not count the pages of a job's CV: %s", e)
        return None


def fit(base, changes: list[dict], limit: int, count) -> list[dict]:
    """Holds Hal's changes for a job to the page limit; `changes` with some marked undone and `held`.

    `count(doc)` gives a document's pages as employers get it, None when not known. When the CV is over the
    limit, Hal undoes its own changes that add words, newest first, then its other changes, and counts again
    after each, at most `FIT_TRIES` times. When the CV is over with none of Hal's changes on, Hal keeps the CV
    as it was (`HELD_BASE`). The person's own edits are never undone. No limit, or no count: nothing changes.
    """
    hal, yours = _split(changes)
    on = [c for c in hal if not c.get("undone")]
    if not limit or not on:
        return changes
    pages = _counted(base, changes, count)
    if pages is None or pages <= limit:
        return changes
    alone = _counted(base, yours, count)
    if alone is not None and alone > limit:
        for c in on:
            c.update(undone=True, held=HELD_BASE)
        log.info("a job's CV is %s pages before Hal's changes, over its %s: kept as it was", alone, limit)
        return changes
    order = [c for c in reversed(on) if _longer(c)] + [c for c in reversed(on) if not _longer(c)]
    for c in order[:FIT_TRIES]:
        c.update(undone=True, held=HELD_FIT)
        pages = _counted(base, changes, count)
        if pages is None or pages <= limit:
            break
    return changes


def _split(changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """(Hal's changes, the person's own edits), each in the order they were made."""
    return [c for c in changes if c.get("by") != YOU], [c for c in changes if c.get("by") == YOU]


def _rev(row: sqlite3.Row) -> str:
    """What the editor opened: the base version and how many of the person's edits were on it then.

    Undo and Redo of Hal's changes leave it alone: Hal only rewords and reorders, so the person's edits
    still fit either way."""
    return f"{row['base_version_id']}+{len(_split(json.loads(row['changes']))[1])}"


def prepare(job: dict, *, level: str | None = None, priorities: list[str] | None = None, base=None,
            con: sqlite3.Connection | None = None) -> dict:
    """Works out the job's CV and keeps it in `job_cvs`; returns the row as the documents step shows it.

    It is built on `base` (a `choose.Choice`), kept as `base_version_id` and `base_choice`; without one, on the
    CV the job's CV was built on before, else the CV Hal chooses for it (`choose.for_writing`). The person's
    own edits from an earlier row are kept after Hal's new changes; an edit that no longer fits the new CV is
    left out and logged. RuntimeError once approved, as `drop_your_edits`: the approved CV is what the form is
    filled with, and only `reopen` lets it change. The CV is chosen and stored under the job's lock
    (`choose.job_lock`), so a second write for the job waits and then builds on what this one stored.
    """
    from jobhunt.cvstudio import choose

    with choose.job_lock(job["id"]):
        return _prepare(job, level=level, priorities=priorities, base=base, con=con)


def _prepare(job: dict, *, level: str | None, priorities: list[str] | None, base,
             con: sqlite3.Connection | None) -> dict:
    """`prepare` while holding the job's lock."""
    from jobhunt.cvstudio import choose, cv, pagelimit

    model = _model()
    if base is None:
        with _conn(con) as c:
            base = choose.for_writing(c, job, again=False)
    used, why = level_for(level, base.doc_id)
    doc, changes = for_job(job, level=level, priorities=priorities, base=base)
    with _conn(con) as c:
        limit = pagelimit.limit_of(base.doc_id, c)[0]
        start = _store().get(base.version_id, con=c)
    with _conn(con) as c, _store().writing(c):
        old = c.execute("SELECT changes, approved FROM job_cvs WHERE job_id = ?", (job["id"],)).fetchone()
        if old is not None and old["approved"]:
            raise RuntimeError(APPROVED)
        kept_n = 0
        for edit in _split(json.loads(old["changes"]))[1] if old else []:
            try:
                doc = model.apply(doc, model.Change(edit["op"], edit["target"], edit.get("value"),
                                                    edit.get("position")))
            except model.CVError as e:
                log.warning("job %s: your edit %s no longer fits its new CV: %s", job["id"], edit["id"], e)
                continue
            kept_n += 1
            changes.append(edit | {"id": f"y{kept_n}"})
        fit(start, changes, limit, lambda d: pagelimit.drawn_pages(cv.v1_of(d)))
        c.execute("INSERT OR REPLACE INTO job_cvs (job_id, base_version_id, version_id, level, priorities, changes, "
                  "approved, base_choice) VALUES (?, ?, NULL, ?, ?, ?, NULL, ?)",
                  (job["id"], base.version_id, used, json.dumps(priorities) if priorities else None,
                   json.dumps(changes), base.stored()))
        c.commit()
    events.publish("application", id=job["id"])
    return shown(job["id"], con) | {"why_no": why}


def picked(job_id: str, con: sqlite3.Connection | None = None) -> list[str] | None:
    """The requirements the person ticked for this job while `cvdocs.pick_first` is on; None otherwise."""
    from jobhunt.core import settings

    if not settings.get("cvdocs.pick_first"):
        return None
    with _conn(con) as c:
        row = c.execute("SELECT priorities FROM job_cvs WHERE job_id = ?", (job_id,)).fetchone()
    return json.loads(row["priorities"]) if row is not None and row["priorities"] else None


def to_pick(job: dict, con: sqlite3.Connection | None = None) -> list[str]:
    """The job's requirements for the person to tick before Hal adjusts its CV; empty when nothing is to ask.

    Asked while `cvdocs.pick_first` is on and the job's CV Hal made is adjusted (not No), not approved, has no
    picks yet and has none of the person's edits, which the picks would make again. A job with fewer than
    `MIN_PICKS` requirements is not asked.
    """
    from jobhunt.core import settings

    if not settings.get("cvdocs.pick_first"):
        return []
    with _conn(con) as c:
        row = c.execute("SELECT * FROM job_cvs WHERE job_id = ?", (job["id"],)).fetchone()
        if (row is None or row["level"] == "no" or row["approved"] or row["priorities"]
                or _split(json.loads(row["changes"]))[1]):
            return []
        found = requirements(job, c)
    return found if len(found) >= MIN_PICKS else []


def check_picks(job: dict, picks: object, con: sqlite3.Connection | None = None) -> list[str]:
    """The person's picks, once they are `MIN_PICKS` to `MAX_PICKS` of `to_pick`'s list; ValueError otherwise."""
    offered = to_pick(job, con)
    if not offered:
        raise ValueError("Hal is not asking what this job cares about: the setting is off, or its CV changed.")
    if not isinstance(picks, list) or not all(isinstance(p, str) for p in picks):
        raise ValueError("Send the requirements you ticked as a list.")
    picks = list(dict.fromkeys(picks))
    if not MIN_PICKS <= len(picks) <= MAX_PICKS:
        raise ValueError(f"Tick {MIN_PICKS} to {MAX_PICKS} of the things this job cares about.")
    if any(p not in offered for p in picks):
        raise ValueError("Tick only the requirements shown for this job.")
    return picks


def kept(job_id: str, con: sqlite3.Connection | None = None) -> bool:
    """True when Hal made this job's CV here, so the application prints and approves it from `job_cvs`."""
    with _conn(con) as c:
        return c.execute("SELECT 1 FROM job_cvs WHERE job_id = ?", (job_id,)).fetchone() is not None


def _row(con: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM job_cvs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("Hal has not adjusted your CV for this job.")
    return row


def shown(job_id: str, con: sqlite3.Connection | None = None) -> dict:
    """The job's change list with each change's state, for the documents step."""
    with _conn(con) as c:
        row = _row(c, job_id)
    hal, yours = _split(json.loads(row["changes"]))
    return {"job_id": job_id, "level": row["level"], "base_version_id": row["base_version_id"],
            "version_id": row["version_id"], "approved": row["approved"], "edits": len(yours), "rev": _rev(row),
            "changes": [{k: c.get(k, "") for k in ("id", "kind", "where", "target", "before", "after", "why", "ev",
                                                    "held")}
                        | {"on": not c.get("undone")} for c in hal]}


def set_change(job_id: str, change_id: str, on: bool, con: sqlite3.Connection | None = None) -> dict:
    """Undo (`on` false) or Redo one change before approval."""
    with _conn(con) as c, _store().writing(c):
        row = _row(c, job_id)
        if row["approved"]:
            raise RuntimeError(APPROVED)
        changes = json.loads(row["changes"])
        found = next((x for x in _split(changes)[0] if x["id"] == change_id), None)
        if found is None:
            raise LookupError("That change is not in this job's CV.")
        found["undone"] = not on
        found.pop("held", None)          # the person's choice now, whatever the page limit says
        c.execute("UPDATE job_cvs SET changes = ? WHERE job_id = ?", (json.dumps(changes), job_id))
        c.commit()
    events.publish("application", id=job_id)
    return shown(job_id, con)


def drop_your_edits(job_id: str, con: sqlite3.Connection | None = None) -> int:
    """Takes the person's own edits out of the job's CV, leaving Hal's changes; returns how many went.

    For writing the documents again fresh: the CV is base plus changes, so this leaves the CV `prepare` made
    as if the person had never edited it. RuntimeError once approved.
    """
    with _conn(con) as c, _store().writing(c):
        row = _row(c, job_id)
        if row["approved"]:
            raise RuntimeError(APPROVED)
        hal, yours = _split(json.loads(row["changes"]))
        if yours:
            c.execute("UPDATE job_cvs SET changes = ? WHERE job_id = ?", (json.dumps(hal), job_id))
            c.commit()
    if yours:
        events.publish("application", id=job_id)
    return len(yours)


def lost_edits(job_id: str, version_id: str, con: sqlite3.Connection | None = None) -> int:
    """How many of the person's own edits to the job's CV would not fit a job's CV built on version
    `version_id`, as `prepare` would leave them out: each is tried in order on that version. Hal's changes are
    left out of the trial, as they only reword and reorder lines, so they never decide whether a line is
    there. 0 for a job with no job's CV."""
    model = _model()
    with _conn(con) as c:
        row = c.execute("SELECT changes FROM job_cvs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return 0
        doc = _store().get(version_id, con=c)
    lost = 0
    for edit in _split(json.loads(row["changes"]))[1]:
        try:
            doc = model.apply(doc, model.Change(edit["op"], edit["target"], edit.get("value"), edit.get("position")))
        except model.CVError:
            lost += 1
    return lost


def _made(job_id: str, con: sqlite3.Connection | None) -> tuple[sqlite3.Row, object, object]:
    """(the row, the base version, the base with Hal's changes still on)."""
    with _conn(con) as c:
        row = _row(c, job_id)
        base = _store().get(row["base_version_id"], con=c)
    return row, base, build(base, _split(json.loads(row["changes"]))[0])


def document(job_id: str, con: sqlite3.Connection | None = None):
    """The job's CV as it stands: the base version with Hal's changes still on, then the person's edits, whose
    new and reworded lines are marked as their own words (`edit.mark_your_words`)."""
    from jobhunt.cvstudio import edit

    row, _, made = _made(job_id, con)
    yours = _split(json.loads(row["changes"]))[1]
    return edit.mark_your_words(made, build(made, yours)) if yours else made


def v1(job_id: str, con: sqlite3.Connection | None = None) -> dict:
    """The job's CV in cv.json shape, for its PDF, the letter's header and the critique (`cv.v1_of`)."""
    from jobhunt.cvstudio import cv

    return cv.v1_of(document(job_id, con))


def version_of(job_id: str, con: sqlite3.Connection | None = None) -> str | None:
    """The version the person approved for this job; None before approval, or for an older application."""
    with _conn(con) as c:
        row = c.execute("SELECT version_id FROM job_cvs WHERE job_id = ? AND approved IS NOT NULL",
                        (job_id,)).fetchone()
    return row["version_id"] if row else None


def reopen(job_id: str, con: sqlite3.Connection | None = None) -> None:
    """Takes the approval back, so the job's CV can change again; the version it saved is kept."""
    with _conn(con) as c:
        c.execute("UPDATE job_cvs SET approved = NULL WHERE job_id = ?", (job_id,))
        c.commit()


def approve(job_id: str, job: dict | None = None, con: sqlite3.Connection | None = None) -> str:
    """Saves the job's CV as a tailored version (or keeps the base when nothing changed); returns its id."""
    with _conn(con) as c:
        row = _row(c, job_id)
        on = [x for x in json.loads(row["changes"]) if not x.get("undone")]
        vid = row["base_version_id"]
        if on:
            job = job or {}
            what = (f"Adjusted for {job.get('title') or 'one job'}"
                    + (f" at {job['company']}" if job.get("company") else ""))
            vid = _store().new_version(document(job_id, c), origin="tailored", what=what, by="you",
                                       parent=row["base_version_id"], job_id=job_id, con=c)
        c.execute("UPDATE job_cvs SET version_id = ?, approved = ? WHERE job_id = ?", (vid, clock.iso(), job_id))
    return vid


# ------------------------------------------------------------------------------- the job's CV editor

def save_edits(job_id: str, base: str, changes: list, con: sqlite3.Connection | None = None) -> dict:
    """Keeps one save of the person's edits (`model.Change`s) to the job's CV, after Hal's changes.

    `store.StaleVersion` when `base` is not what the editor opened (`_rev`); RuntimeError once approved;
    CVError when a change does not fit or nothing changed.
    """
    model = _model()
    with _conn(con) as c, _store().writing(c):
        row = _row(c, job_id)
        if row["approved"]:
            raise RuntimeError(APPROVED)
        if base != _rev(row):
            raise _store().StaleVersion("This job's CV changed since you started editing. Your changes are not saved.")
        before = document(job_id, c)
        after = before
        for change in changes:
            after = model.apply(after, change)
        if model.to_json(after) == model.to_json(before):
            raise model.CVError("changes: they leave this job's CV as it was")
        problems = model.validate(after)
        if problems:
            raise model.CVError(problems[0])
        listed = json.loads(row["changes"])
        n = len(_split(listed)[1])
        listed += [{"id": f"y{n + i}", "by": YOU, "op": x.op, "target": x.target, "value": x.value,
                    "position": x.position} for i, x in enumerate(changes, 1)]
        c.execute("UPDATE job_cvs SET changes = ? WHERE job_id = ?", (json.dumps(listed), job_id))
        c.commit()
    events.publish("application", id=job_id)
    return shown(job_id, con)


def _main_label(con: sqlite3.Connection, main: str) -> str:
    """How the page names the CV `main`: "your Museum CV", or `MAIN_LABEL` while only one CV is in use."""
    from jobhunt.cvstudio import choose

    return choose.your(_store().doc(con, main)["name"]) if len(_store().starred(con)) > 1 else MAIN_LABEL


def main_label(job_id: str, con: sqlite3.Connection) -> str:
    """How the page names the CV the job's CV was built from (`editor`'s `main_label`), for its messages."""
    return _main_label(con, _store().version(_row(con, job_id)["base_version_id"], con=con)["doc_id"])


def editor(job_id: str, con: sqlite3.Connection | None = None) -> dict:
    """The job's CV for Edit your CV in job mode, with what changed marked by `model.diff`.

    `marks` lists each part Hal changed (`by` "hal", with its change's id; only changes still on) and each part
    the person changed after them (`by` "you"). `version_id` is what a save sends back as `base`, and
    `main_version_id` the version in use of the CV this job's CV was built from (`main_doc_id`, called
    `main_label` on the page, "your main CV" while only one CV is in use), for Copy to my main CV, which copies
    into that CV. `pages` is the job's CV against its page limit (`pagelimit.job_state`).
    """
    model = _model()
    row, base, made = _made(job_id, con)
    hal, _ = _split(json.loads(row["changes"]))
    marks = []
    for c in hal:
        if not c.get("undone"):
            one = build(base, [c])
            marks += [{"target": d["target"], "kind": d["kind"], "by": "hal", "change": c["id"]}
                      for d in model.diff(base, one)]
    doc = document(job_id, con)
    marks += [{"target": d["target"], "kind": d["kind"], "by": YOU} for d in model.diff(made, doc)]
    from jobhunt.cvstudio import pagelimit

    main = _store().version(row["base_version_id"], con=con)["doc_id"]
    return {**model.to_dict(doc), "version_id": _rev(row), "n": None, "job_id": job_id,
            "main_version_id": _store().current(con, main)[0], "main_doc_id": main,
            "main_label": _main_label(con, main), "approved": row["approved"],
            "changes": shown(job_id, con)["changes"], "marks": marks, "pages": pagelimit.job_state(job_id, con)}


def copy_to_main(job_id: str, target: str, base: str, con: sqlite3.Connection, job: dict | None = None) -> str:
    """Copies the lines of one entry of the job's CV into the CV it was built from, made on that CV's version
    `base` (`editor`'s `main_version_id`), as one new version of it (`edit.save`); returns its id. An entry
    that CV does not have is added to its section.

    `store.StaleVersion` when `base` is no longer the main version in use; CVError when `target` is not an
    entry, its section is gone from that CV, or that CV already has these lines.
    """
    from jobhunt.cvstudio import edit as editing

    model = _model()
    entry = model.find(document(job_id, con), target)
    if not isinstance(entry, model.Entry):
        raise model.CVError("target: choose an entry of this job's CV to copy")
    main = _store().get(base, con=con)
    label = _main_label(con, _store().version(base, con=con)["doc_id"])
    section = target.split("/")[0]
    try:
        there = model.find(main, target)
    except model.CVError:
        there = None
    if there is None:
        try:
            model.find(main, section)
        except model.CVError as e:
            raise model.CVError(f"target: {label} no longer has this section, so there is nowhere to copy "
                                "these lines") from e
        change = model.Change("insert", section, copy.deepcopy(entry))
    elif [b.text for b in there.bullets] == [b.text for b in entry.bullets]:
        raise model.CVError(f"target: {label} already has these lines")
    else:
        change = model.Change("replace", f"{target}/bullets", copy.deepcopy(entry.bullets))
    title = (job or {}).get("title") or "one job"
    vid, _, _ = editing.save(con, base, [change], f"Copied {entry.heading or 'an entry'} from your CV for {title}",
                             mark=False)
    return vid

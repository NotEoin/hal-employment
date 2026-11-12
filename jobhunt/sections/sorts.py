"""The sorts the person asked for and their proposals (`record_sorts`, `record_sort_rows`, migration 0031).

A sort is made (`create`), filled by the `record.sort` action (`run`, which calls `sorter.propose` or, for a
move, `sections.move.propose`), reviewed on the page a page of rows at a time (`counts`, `rows`,
`change_row`, `tick`), and accepted by section or all at once (`accept`). Accepting only marks the rows; the
`record.sort_accept` action then makes each a source through `pipeline.add_uploads` or `add_link` with its
section, kind and card (`add_accepted`) and reads it with its section's reader.

Design decisions, each with its reason:

- Proposals are kept in the database, not in the page, so a sort of ten thousand files can be reviewed over
  several sittings and paged (`PAGE`); the page shows counts per section from one grouped query.
- A row the section needs a card for (coursework, lecture material, a job's document) cannot be accepted
  until it has one (`sorter.needs_card`): it stays proposed, and after an accepted CV is read the cards it
  gave are matched again (`rematch`), so accepting the CVs first lets the rest find their courses.
- What the person changes on a row is theirs (`by = "you"`), and a sort run again keeps every row already
  accepted or added, so nothing they did is undone by it.
- Files from the page or a zip are kept in Hal's data folder (`work_dir`) until the sort is removed, as the
  rows point at them; a folder on this computer and the drop folder are read where they are.
- Lecture material starts unticked (`UNTICKED`): it only teaches Hal what the person studied, and a real dump
  held 6,808 lecture files of 7,032, hours of AI reading added by one press (2026-11-09).
- The page warns before adding many files with an estimate of the reading time (`read_estimate`): the
  measured seconds per file of the latest reads on this computer, else `READ_S_DEFAULT`.
- Each press of an add button is an add (`record_sort_adds`, migration 0032), named on the rows it took
  (`add_id`), so its reading can be stopped (`stop_add`: the files not read yet are taken out again and their
  rows proposed) and the whole add undone (`undo_add`: every source it made removed, with its facts and
  notes, and its rows proposed again). A graduate pressed "Add everything ticked" by mistake and had no way
  to stop hours of reading (2026-11-09).
- The review goes through the sort in steps, each of one or more sections (CVs and papers together), so
  `rows`, `tick` and `accept` take a section, a comma-separated list of them or a list (`section_filter`).
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from jobhunt import db
from jobhunt.core import clock, paths
from jobhunt.evidence.routes import UPLOAD_MAX_BODY
from jobhunt.ingest import discover
from jobhunt.profile import store as profile_store
from jobhunt.sections import pipeline, registry, sorter, store

log = logging.getLogger(__name__)

KINDS = ("folder", "upload", "inbox", "move")
LOCAL_KINDS = ("folder", "inbox", "move")    # read where they are, on this computer
STATES = ("new", "sorting", "ready", "failed", "undone")
ROW_STATES = ("proposed", "left_out", "copy", "accepted", "added", "failed")
# A ready sort waits for the person while it has proposals and either nothing was added from it yet or some are
# still ticked: after an add, the files left unticked were the person's choice, not more to check (2026-11-12).
WAITS = ("s.state = 'ready' AND EXISTS (SELECT 1 FROM record_sort_rows r WHERE r.sort_id = s.id "
         "AND r.state = 'proposed' AND (r.ticked = 1 OR NOT EXISTS (SELECT 1 FROM record_sort_rows a "
         "WHERE a.sort_id = s.id AND a.state IN ('accepted', 'added'))))")
PAGE = 50                  # rows a page of the review table shows
PAGE_MAX = 500             # a client asking for more is paged anyway
FOLDER = "record-sort"
INSERT_CHUNK = 500         # rows written per statement batch, so a big sort commits in steps
# A project folder copied into Hal's data folder is held in memory once, like an upload from the page.
FOLDER_BYTES_MAX = UPLOAD_MAX_BODY
CHANGEABLE = {"section", "doc_kind", "target_id", "ticked", "star"}
UNTICKED = ("lecture",)    # sections whose proposals start unticked: the person ticks the modules they want
UNREAD = ("new", "reading")  # source states of a file not read yet: a stopped add takes it out again
ACTIVE_ACTIONS = ("queued", "running", "waiting_for_you")    # `core.runner.ACTIVE`
READ_KINDS = ("record.sources", "record.sort_accept")   # the actions that read sources, timed for the estimate
READ_SAMPLE = 10           # the latest reads averaged: recent enough to follow a change of AI or computer
# Seconds to read one file when this computer has read none yet, replaced by the measured figure after the first
# reads: Hal's writer (Gemma 4 31B on one card) read 110 lecture files one at a time in a median 24.5 s and a
# mean 36 s each, the mean pulled up by long decks (2026-11-11); a first guess near the mean does not promise
# too little.
READ_S_DEFAULT = 35


def work_dir(sort_id: int) -> Path:
    """Where the files of an upload sort are kept until the sort is removed."""
    return paths.data_dir() / FOLDER / str(int(sort_id))


def create(con: sqlite3.Connection, kind: str, roots: list[str], label: str) -> int:
    """Adds a sort of `kind` over the folders `roots`, state `new`; returns its id."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of sort")
    now = db.now()
    with con:
        return con.execute("INSERT INTO record_sorts (kind, roots, label, created, updated) VALUES (?, ?, ?, ?, ?)",
                           (kind, json.dumps([str(r) for r in roots]), label, now, now)).lastrowid


def set_roots(con: sqlite3.Connection, sort_id: int, roots: list[str], label: str) -> None:
    """Sets the folders a sort walks and its words, once its files are in place; commits."""
    with con:
        con.execute("UPDATE record_sorts SET roots = ?, label = ?, updated = ? WHERE id = ?",
                    (json.dumps([str(r) for r in roots]), label, db.now(), int(sort_id)))


def row_of(con: sqlite3.Connection, sort_id) -> sqlite3.Row:
    """The row of one sort; LookupError when there is none."""
    try:
        key = int(sort_id)
    except (TypeError, ValueError):
        raise LookupError("That sort is not there.") from None
    found = con.execute("SELECT * FROM record_sorts WHERE id = ?", (key,)).fetchone()
    if found is None:
        raise LookupError("That sort is not there.")
    return found


def set_state(con: sqlite3.Connection, sort_id: int, state: str, error: str = "") -> None:
    """Sets a sort's state, with the words on why it failed; commits."""
    if state not in STATES:
        raise ValueError(f"{state!r} is not a state of a sort")
    with con:
        con.execute("UPDATE record_sorts SET state = ?, error = ?, updated = ? WHERE id = ?",
                    (state, error, db.now(), sort_id))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""


def save(con: sqlite3.Connection, sort_id: int, found: sorter.Sorted) -> int:
    """Replaces the sort's proposals with `found`, keeping the rows already accepted or added; returns how many
    rows it now has."""
    with con:
        con.execute("DELETE FROM record_sort_rows WHERE sort_id = ? AND state IN ('proposed', 'left_out', 'copy', "
                    "'failed')", (sort_id,))
        con.execute("UPDATE record_sorts SET skipped = ?, updated = ? WHERE id = ?",
                    (json.dumps(found.skipped), db.now(), sort_id))
    rows = [(sort_id, str(p.path), p.rel, p.held, p.size, p.sha256, _iso(p.mtime), p.section, p.doc_kind,
             p.target_id, int(p.star), p.by, p.why, int(p.state == sorter.PROPOSED and p.section not in UNTICKED),
             p.state, p.copy_of,
             p.note_rel) for p in found.proposals]
    for start in range(0, len(rows), INSERT_CHUNK):
        with con:
            con.executemany("""INSERT OR IGNORE INTO record_sort_rows (sort_id, path, rel, held, size, sha256, mtime,
                               section, doc_kind, target_id, star, by, why, ticked, state, copy_of, note_rel)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            rows[start:start + INSERT_CHUNK])
    return con.execute("SELECT count(*) FROM record_sort_rows WHERE sort_id = ?", (sort_id,)).fetchone()[0]


def run(con: sqlite3.Connection, sort_id: int, progress=None) -> dict:
    """Proposes where everything in the sort's folders goes (`sorter.propose`, or `move.propose` for a move) and
    keeps the proposals; returns the sort's counts."""
    s = row_of(con, sort_id)
    set_state(con, s["id"], "sorting")
    try:
        found = sorter.Sorted()
        if s["kind"] == "move":
            from jobhunt.sections import move

            found = move.propose(con)
        known = inbox_known(con, but=s["id"]) if s["kind"] == "inbox" else set()
        for root in json.loads(s["roots"]) if s["kind"] != "move" else []:
            one = sorter.propose(con, Path(root), local=s["kind"] in LOCAL_KINDS,
                                 prefix="" if s["kind"] == "upload" else None, known=known, progress=progress)
            found.proposals += one.proposals
            for why, n in one.skipped.items():
                found.skipped[why] = found.skipped.get(why, 0) + n
        if s["kind"] != "move":
            sorter.across_roots(con, found.proposals)
        sorter.cap_stars(found.proposals)
        save(con, s["id"], found)
    except (OSError, ValueError) as e:
        log.warning("could not sort %s: %s", s["id"], e)
        set_state(con, s["id"], "failed", f"Hal could not sort it: {e}")
        raise
    set_state(con, s["id"], "ready")
    return counts(con, s["id"])


def inbox_known(con: sqlite3.Connection, but: int | None = None) -> set[str]:
    """The paths an earlier sort of the drop folder proposed, so a new one proposes only new files."""
    return {r[0] for r in con.execute("SELECT r.path FROM record_sort_rows r JOIN record_sorts s ON s.id = r.sort_id "
                                      "WHERE s.kind = 'inbox' AND s.id != ?", (-1 if but is None else but,))}


def inbox_has_new(con: sqlite3.Connection, folder: Path) -> bool:
    """True when the drop folder holds a file no sort of it has proposed (alone or inside a project folder)."""
    known = inbox_known(con)
    for f in discover.walk(folder).files:
        if not any(str(p) in known for p in (f.path, *f.path.parents)):
            return True
    return False


def card_titles(con: sqlite3.Connection, ids: set[int]) -> dict[int, str]:
    """The titles of the cards `ids`, by id, for the rows that name them."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: profile_store.title(r["kind"], json.loads(r["fields"])) for r in con.execute(
        f"SELECT id, kind, fields FROM profile_items WHERE id IN ({marks})", sorted(ids))}


def older_copies(con: sqlite3.Connection, sort_id: int, rels: list[str]) -> dict[str, list[dict]]:
    """The copies set aside for each row kept at `rels` (a page, at most `PAGE_MAX`), as {rel: [{"id", "rel"}]},
    by path."""
    out: dict[str, list[dict]] = {}
    marks = ",".join("?" * len(rels))      # placeholders only; the values are bound
    for r in con.execute(f"SELECT id, rel, copy_of FROM record_sort_rows WHERE sort_id = ? AND state = 'copy' AND "
                         f"copy_of IN ({marks}) ORDER BY rel", (sort_id, *rels)):
        out.setdefault(r["copy_of"], []).append({"id": r["id"], "rel": r["rel"]})
    return out


def shape_row(r: sqlite3.Row, titles: dict[int, str], copies: dict[str, list[dict]] | None = None) -> dict:
    """One proposal as the page shows it, with the older copies set aside for it (`older_copies`)."""
    section = registry.SECTIONS.get(r["section"])
    kind = registry.DOC_KINDS.get(r["doc_kind"])
    return {"id": r["id"], "rel": r["rel"], "name": PurePosixPath(r["rel"]).name or r["rel"], "held": r["held"],
            "size": r["size"], "mtime": r["mtime"], "section": r["section"],
            "section_label": section.label if section else "", "doc_kind": r["doc_kind"],
            "doc_kind_label": kind.label if kind else "", "target_id": r["target_id"],
            "target_title": titles.get(r["target_id"], ""), "target_kind": section.target if section else "",
            "needs_card": sorter.needs_card(r["section"], r["target_id"]), "star": bool(r["star"]),
            "can_star": r["section"] == "project", "by": r["by"], "why": r["why"], "ticked": bool(r["ticked"]),
            "state": r["state"], "copy_of": r["copy_of"], "source_id": r["source_id"], "note_rel": r["note_rel"],
            "copies": (copies or {}).get(r["rel"], []) if r["state"] == "proposed" else []}


def counts(con: sqlite3.Connection, sort_id: int) -> dict:
    """How many proposals each section has (ticked, and waiting for a card), and the rows in each other state."""
    by_state = dict(con.execute("SELECT state, count(*) FROM record_sort_rows WHERE sort_id = ? GROUP BY state",
                                (sort_id,)).fetchall())
    sections = []
    for r in con.execute("""SELECT section, count(*) AS n, sum(ticked) AS ticked,
                            sum(CASE WHEN target_id IS NULL THEN 1 ELSE 0 END) AS no_card
                            FROM record_sort_rows WHERE sort_id = ? AND state = 'proposed'
                            GROUP BY section""", (sort_id,)):
        s = registry.SECTIONS.get(r["section"])
        if s is None:
            continue
        sections.append({"id": s.id, "label": s.label, "proposed": r["n"], "ticked": r["ticked"] or 0,
                         "needs_card": r["no_card"] if s.target_required else 0})
    order = list(registry.SECTIONS)
    sections.sort(key=lambda x: order.index(x["id"]))
    return {"sections": sections, **{state: by_state.get(state, 0) for state in ROW_STATES},
            "total": sum(by_state.values())}


def shape(con: sqlite3.Connection, sort_id) -> dict:
    """A sort as the page shows it, with its counts and the cards its rows may be for."""
    s = row_of(con, sort_id)
    waits = con.execute(f"SELECT 1 FROM record_sorts s WHERE s.id = ? AND {WAITS}", (s["id"],)).fetchone()
    return {"id": s["id"], "kind": s["kind"], "label": s["label"], "state": s["state"], "error": s["error"],
            "created": s["created"], "updated": s["updated"], "skipped": json.loads(s["skipped"] or "{}"),
            "counts": counts(con, s["id"]), "waits": waits is not None,
            "undone": s["state"] == "undone", "can_undo": s["kind"] == "move" and s["state"] != "undone" and bool(
                con.execute("SELECT 1 FROM record_sort_kept_out WHERE sort_id = ? LIMIT 1", (s["id"],)).fetchone()
                or con.execute("SELECT 1 FROM record_sort_rows WHERE sort_id = ? AND source_id IS NOT NULL LIMIT 1",
                               (s["id"],)).fetchone())}


def read_estimate(con: sqlite3.Connection) -> dict:
    """How long reading one added file takes on this computer: {"seconds", "measured"}, the average of the latest
    `READ_SAMPLE` finished reads (`READ_KINDS`), else `READ_S_DEFAULT` with "measured" false."""
    marks = ",".join("?" * len(READ_KINDS))     # placeholders only; the kinds are bound
    found = con.execute(f"SELECT started, ended, done_n FROM actions WHERE kind IN ({marks}) AND started IS NOT "
                        f"NULL AND ended IS NOT NULL AND done_n > 0 ORDER BY ended DESC LIMIT ?",
                        (*READ_KINDS, READ_SAMPLE)).fetchall()
    seconds = sum(max(0.0, (clock.parse(r["ended"]) - clock.parse(r["started"])).total_seconds()) for r in found)
    files = sum(r["done_n"] for r in found)
    if not files or seconds <= 0:
        return {"seconds": READ_S_DEFAULT, "measured": False}
    return {"seconds": round(seconds / files, 1), "measured": True}


def waiting(con: sqlite3.Connection) -> list[int]:
    """The ids of the sorts waiting for the person (`WAITS`), newest first."""
    return [r[0] for r in con.execute(f"SELECT s.id FROM record_sorts s WHERE {WAITS} ORDER BY s.id DESC")]


def section_ids(section) -> list[str]:
    """The sections named by `section`: one id, a comma-separated list of them, or a list; [] for none.
    LookupError for a section that is not there."""
    if not section:
        return []
    parts = section.split(",") if isinstance(section, str) else list(section)
    ids = [str(p).strip() for p in parts if str(p).strip()]
    for one in ids:
        registry.get(one)
    return ids


def section_filter(section) -> tuple[str, list[str]]:
    """The SQL that keeps the rows of `section` (`section_ids`), with its arguments; ("", []) for all."""
    ids = section_ids(section)
    if not ids:
        return "", []
    return f" AND section IN ({','.join('?' * len(ids))})", ids     # placeholders only; the ids are bound


def listing(con: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """The latest sorts, newest first."""
    return [shape(con, r["id"]) for r in con.execute("SELECT id FROM record_sorts ORDER BY id DESC LIMIT ?",
                                                     (limit,))]


def cards(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """The education and job cards a row may be for, as {kind: [{"id", "title"}]}."""
    out: dict[str, list[dict]] = {"education": [], "job": []}
    for r in con.execute("SELECT id, kind, fields FROM profile_items WHERE kind IN ('education', 'job') AND "
                         "state != 'hidden' AND merged_into IS NULL ORDER BY id"):
        out[r["kind"]].append({"id": r["id"], "title": profile_store.title(r["kind"], json.loads(r["fields"]))})
    return out


def rows(con: sqlite3.Connection, sort_id: int, *, section=None, state: str = "proposed",
         offset: int = 0, limit: int = PAGE) -> dict:
    """One page of a sort's rows in `state` (of the sections given, `section_ids`), by path; {"rows", "total"}."""
    row_of(con, sort_id)
    if state not in ROW_STATES:
        raise ValueError(f"Choose rows by one of: {', '.join(ROW_STATES)}.")
    where, only = section_filter(section)
    sql, args = "FROM record_sort_rows WHERE sort_id = ? AND state = ?" + where, [int(sort_id), state, *only]
    total = con.execute("SELECT count(*) " + sql, args).fetchone()[0]
    found = con.execute("SELECT * " + sql + " ORDER BY rel LIMIT ? OFFSET ?",
                        [*args, max(1, min(int(limit), PAGE_MAX)), max(0, int(offset))]).fetchall()
    titles = card_titles(con, {r["target_id"] for r in found if r["target_id"] is not None})
    copies = older_copies(con, int(sort_id), [r["rel"] for r in found]) if state == "proposed" else {}
    return {"rows": [shape_row(r, titles, copies) for r in found], "total": total}


def _row(con: sqlite3.Connection, row_id) -> sqlite3.Row:
    try:
        key = int(row_id)
    except (TypeError, ValueError):
        raise LookupError("That file is not in the sort.") from None
    found = con.execute("SELECT * FROM record_sort_rows WHERE id = ?", (key,)).fetchone()
    if found is None:
        raise LookupError("That file is not in the sort.")
    return found


def change_row(con: sqlite3.Connection, row_id, changes: dict) -> dict:
    """Changes one proposal's section, kind of document, card, tick or showcase guess; returns the row.

    A row left out or a copy given a section is proposed again, as the person's own choice. ValueError for a
    change that does not fit (a kind the section does not take, a card of another kind); LookupError for a row
    or card that is not there.
    """
    r = _row(con, row_id)
    unknown = sorted(set(changes) - CHANGEABLE)
    if unknown:
        raise ValueError(f"A proposal has no {', '.join(unknown)} to change.")
    if r["state"] in ("accepted", "added"):
        raise ValueError("That file is in your record already. Change it there.")
    sets: dict = {}
    section_id = changes.get("section", r["section"])
    if "section" in changes or "doc_kind" in changes:
        if not section_id:
            sets.update({"section": "", "doc_kind": "", "target_id": None, "state": "left_out", "ticked": 0,
                         "why": "You left it out."})
        else:
            section = registry.get(str(section_id))
            kind = changes.get("doc_kind") or (r["doc_kind"] if section.accepts(r["doc_kind"]) else None)
            sets.update({"section": section.id, "doc_kind": registry.doc_kind(section, kind).id, "by": "you",
                         "state": "proposed", "why": "You chose it."})
            if section.id != r["section"]:
                sets["target_id"] = None
                sets["ticked"] = 1
    if "target_id" in changes and sets.get("section", r["section"]):
        section = registry.get(sets.get("section", r["section"]))
        target = changes["target_id"]
        sets["target_id"] = None if target in (None, "") and not section.target_required else \
            store.target_card(con, section, target)
        sets["by"] = "you"
    for flag in ("ticked", "star"):
        if flag in changes:
            if not isinstance(changes[flag], bool):
                raise ValueError(f"Say whether it is {flag}: true or false.")
            if flag == "star" and sets.get("section", r["section"]) != "project":
                raise ValueError("Only a project can be a showcase piece here; star coursework on its course.")
            sets[flag] = int(changes[flag])
    if sets:
        with con:
            con.execute(f"UPDATE record_sort_rows SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        (*sets.values(), r["id"]))
    found = _row(con, r["id"])
    return shape_row(found, card_titles(con, {found["target_id"]} - {None}))


def keep_copy(con: sqlite3.Connection, row_id) -> dict:
    """Keeps the copy at `row_id` instead of the one Hal kept: it is proposed in that row's place, with its
    tick and showcase guess, and the other copies name it. Returns the row now kept.

    ValueError when the row is no copy, or the copy kept is in the record already; LookupError for a row not
    in the sort."""
    r = _row(con, row_id)
    if r["state"] != "copy":
        raise ValueError("Hal already keeps that one.")
    kept = con.execute("SELECT * FROM record_sort_rows WHERE sort_id = ? AND rel = ?",
                       (r["sort_id"], r["copy_of"])).fetchone()
    if kept is None or kept["state"] != "proposed":
        raise ValueError("The copy Hal kept is in your record already. Change it there.")
    why = f"Another copy of {r['rel']}, which you keep."
    with con:
        con.execute("UPDATE record_sort_rows SET state = 'proposed', copy_of = '', ticked = ?, star = ?, by = 'you', "
                    "why = 'You chose to keep this copy.' WHERE id = ?", (kept["ticked"], kept["star"], r["id"]))
        con.execute("UPDATE record_sort_rows SET state = 'copy', copy_of = ?, ticked = 0, star = 0, why = ? "
                    "WHERE sort_id = ? AND state = 'copy' AND copy_of = ?", (r["rel"], why, r["sort_id"], kept["rel"]))
        con.execute("UPDATE record_sort_rows SET state = 'copy', copy_of = ?, ticked = 0, star = 0, why = ? "
                    "WHERE id = ?", (r["rel"], why, kept["id"]))
    found = _row(con, r["id"])
    return shape_row(found, card_titles(con, {found["target_id"]} - {None}),
                     older_copies(con, found["sort_id"], [found["rel"]]))


def tick(con: sqlite3.Connection, sort_id, ticked: bool, section=None) -> int:
    """Ticks or unticks every proposal of a sort (of the sections given, `section_ids`); returns how many."""
    s = row_of(con, sort_id)
    where, only = section_filter(section)
    sql = "UPDATE record_sort_rows SET ticked = ? WHERE sort_id = ? AND state = 'proposed'" + where
    args = [int(ticked), s["id"], *only]
    with con:
        return con.execute(sql, args).rowcount


def accept(con: sqlite3.Connection, sort_id, section=None) -> dict:
    """Marks the ticked proposals (of the sections given, `section_ids`) accepted, all but those still needing a card,
    as one add; returns {"accepted": n, "need_card": n, "add_id": the add, or None when nothing was accepted}. The
    `record.sort_accept` action makes them sources."""
    s = row_of(con, sort_id)
    if s["state"] != "ready":
        raise ValueError("Wait until Hal has finished sorting, then accept." if s["state"] in ("new", "sorting")
                         else "This sort cannot be accepted any more.")
    where, only = section_filter(section)
    sql = "SELECT id, section, target_id FROM record_sort_rows WHERE sort_id = ? AND state = 'proposed' AND " \
          "ticked = 1" + where
    args = [s["id"], *only]
    ready, waiting = [], 0
    for r in con.execute(sql, args).fetchall():
        if sorter.needs_card(r["section"], r["target_id"]):
            waiting += 1
        else:
            ready.append((r["id"],))
    if not ready:
        return {"accepted": 0, "need_card": waiting, "add_id": None}
    pipeline.ensure_record()
    with con:
        add_id = con.execute("INSERT INTO record_sort_adds (sort_id, files, created) VALUES (?, ?, ?)",
                             (s["id"], len(ready), db.now())).lastrowid
        con.executemany("UPDATE record_sort_rows SET state = 'accepted', add_id = ? WHERE id = ?",
                        [(add_id, row_id) for (row_id,) in ready])
    return {"accepted": len(ready), "need_card": waiting, "add_id": add_id}


def set_add_action(con: sqlite3.Connection, add_id: int, action_id: str) -> None:
    """Names the action reading an add; commits."""
    with con:
        con.execute("UPDATE record_sort_adds SET action_id = ? WHERE id = ?", (action_id, int(add_id)))


def _add_state(r: sqlite3.Row) -> str:
    """An add's state from its action's: reading, done or stopped (an action that failed or was cut short by a
    restart stopped too), or undone."""
    if r["undone"]:
        return "undone"
    if r["action_state"] in ACTIVE_ACTIONS or r["action_state"] is None:
        return "reading"
    return "done" if r["action_state"] == "done" else "stopped"


def adds(con: sqlite3.Connection, sort_id) -> list[dict]:
    """A sort's adds, newest first: {"id", "files", "added", "state", "action_id", "created", "done", "total"},
    with `added` the sources it made that are still there, and the files read so far of the action's total."""
    s = row_of(con, sort_id)
    found = con.execute("""SELECT a.*, x.state AS action_state, x.done_n, x.done_total,
                           (SELECT count(*) FROM record_sort_rows r WHERE r.add_id = a.id AND r.state = 'added'
                            AND r.source_id IS NOT NULL) AS added
                           FROM record_sort_adds a LEFT JOIN actions x ON x.id = a.action_id
                           WHERE a.sort_id = ? ORDER BY a.id DESC""", (s["id"],)).fetchall()
    return [{"id": r["id"], "files": r["files"], "added": r["added"], "state": _add_state(r),
             "action_id": r["action_id"], "created": r["created"], "done": r["done_n"] or 0,
             "total": r["done_total"] or r["files"]} for r in found]


def _add_of(con: sqlite3.Connection, sort_id, add_id) -> sqlite3.Row:
    """One add of a sort; LookupError when the sort has no such add."""
    s = row_of(con, sort_id)
    try:
        key = int(add_id)
    except (TypeError, ValueError):
        raise LookupError("That add is not in this sort.") from None
    found = con.execute("SELECT * FROM record_sort_adds WHERE id = ? AND sort_id = ?", (key, s["id"])).fetchone()
    if found is None:
        raise LookupError("That add is not in this sort.")
    return found


def _propose_again(con: sqlite3.Connection, row_ids: list[int], why: str) -> None:
    """Puts rows back as proposals, ticked, with no source or add; commits."""
    with con:
        con.executemany("UPDATE record_sort_rows SET state = 'proposed', ticked = 1, source_id = NULL, add_id = NULL, "
                        "why = ? WHERE id = ?", [(why, row_id) for row_id in row_ids])


def stop_add(con: sqlite3.Connection, add_id: int) -> dict:
    """What a stopped add leaves: the sources it made that were read stay; those not read yet are taken out
    (`pipeline.remove`) and their rows, with the rows not made sources yet, are proposed again. Returns
    {"kept": n, "back": n}."""
    rows = con.execute("SELECT r.id, r.state, r.source_id, x.state AS source_state FROM record_sort_rows r "
                       "LEFT JOIN record_sources x ON x.id = r.source_id WHERE r.add_id = ?", (int(add_id),)).fetchall()
    back, kept = [], 0
    for r in rows:
        if r["state"] == "accepted":
            back.append(r["id"])
        elif r["state"] == "added" and r["source_state"] in UNREAD:
            pipeline.remove(con, r["source_id"])
            back.append(r["id"])
        elif r["state"] == "added":
            kept += 1
    _propose_again(con, back, "Not read yet: you stopped reading. Add it again to read it.")
    return {"kept": kept, "back": len(back)}


def undo_add(con: sqlite3.Connection, sort_id, add_id) -> dict:
    """Undoes one add: every source it made is removed with its facts and notes (`pipeline.remove`), and every
    row it took is proposed again. Returns {"removed": sources, "back": rows}.

    ValueError while the add is still reading, for an add already undone, or in a move (whose own undo puts the
    older notes back too); LookupError for an add not in the sort."""
    a = _add_of(con, sort_id, add_id)
    if row_of(con, sort_id)["kind"] == "move":
        raise ValueError("Use Undo the move: it puts back what your older notes gave too.")
    if a["undone"]:
        raise ValueError("That add is undone already.")
    if a["action_id"] and con.execute("SELECT 1 FROM actions WHERE id = ? AND state IN (?, ?, ?)",
                                      (a["action_id"], *ACTIVE_ACTIONS)).fetchone():
        raise ValueError("Hal is still reading what you added. Stop reading first, then undo it.")
    removed = 0
    rows = con.execute("SELECT id, state, source_id FROM record_sort_rows WHERE add_id = ?", (a["id"],)).fetchall()
    for r in rows:
        if r["state"] == "added" and r["source_id"] is not None:
            try:
                pipeline.remove(con, r["source_id"])
                removed += 1
            except LookupError:     # the person removed it from its section already
                pass
    _propose_again(con, [r["id"] for r in rows], "Back from an add you undid.")
    with con:
        con.execute("UPDATE record_sort_adds SET undone = 1 WHERE id = ?", (a["id"],))
    return {"removed": removed, "back": len(rows)}


def _incoming(r: sqlite3.Row) -> list[pipeline.Incoming]:
    """A row's files as the pipeline takes uploads: one file, or a project folder's files under its name."""
    path = Path(r["path"])
    if r["held"] == registry.UPLOAD:
        return [pipeline.Incoming(path.name, path.read_bytes())]
    found = discover.walk(path, "").files
    if sum(f.size for f in found) > FOLDER_BYTES_MAX:
        raise ValueError(f"{path.name} is too big to copy. Add it from Projects as a folder on this computer.")
    return [pipeline.Incoming(f.path.name, f.path.read_bytes(), f"{path.name}/{f.rel}") for f in found]


def _make_source(con: sqlite3.Connection, r: sqlite3.Row) -> dict:
    """Adds one accepted row as a source of its section; the pipeline's {"added", "duplicates", ...}."""
    if r["held"] == registry.FOLDER:
        return pipeline.add_link(con, r["section"], r["path"], doc_kind=r["doc_kind"], target_id=r["target_id"])
    return pipeline.add_uploads(con, r["section"], _incoming(r), doc_kind=r["doc_kind"], target_id=r["target_id"])


def add_accepted(con: sqlite3.Connection, sort_id: int, add_id: int | None = None, checkpoint=None) -> list[int]:
    """Makes each accepted row of a sort (of one add when given) a source of its section, with its kind and card;
    returns the ids of the new sources, to be read. A row that cannot be added is `failed` with the reason; one
    whose file is in the record already is left out, linked to that source. `checkpoint()` runs before each row,
    so a stop is seen while thousands of files are copied."""
    new = []
    sql, args = "SELECT * FROM record_sort_rows WHERE sort_id = ? AND state = 'accepted'", [sort_id]
    if add_id is not None:
        sql += " AND add_id = ?"
        args.append(int(add_id))
    for r in con.execute(sql + " ORDER BY id", args).fetchall():
        if checkpoint is not None:
            checkpoint()
        try:
            out = _make_source(con, r)
        except (OSError, ValueError, LookupError) as e:
            with con:
                con.execute("UPDATE record_sort_rows SET state = 'failed', why = ? WHERE id = ?",
                            (f"Hal could not add it: {e}", r["id"]))
            continue
        with con:
            if out["added"]:
                con.execute("UPDATE record_sort_rows SET state = 'added', source_id = ? WHERE id = ?",
                            (out["added"][0], r["id"]))
            elif out.get("duplicates"):    # in the record already: linked, never counted as this sort's own
                same = out["duplicates"][0]
                con.execute("UPDATE record_sort_rows SET state = 'left_out', source_id = ?, why = ? WHERE id = ?",
                            (same["id"], f"Already in your record, under {same['section_label']}.", r["id"]))
            else:
                con.execute("UPDATE record_sort_rows SET state = 'failed', why = ? WHERE id = ?",
                            ("Hal found nothing in it to keep.", r["id"]))
        new += out["added"]
    return new


def added_sources(con: sqlite3.Connection, sort_id: int, add_id: int | None = None) -> list[int]:
    """The ids of the sources a sort's rows (of one add when given) were made, read or not."""
    sql, args = "SELECT source_id FROM record_sort_rows WHERE sort_id = ? AND state = 'added'", [sort_id]
    if add_id is not None:
        sql += " AND add_id = ?"
        args.append(int(add_id))
    return [r[0] for r in con.execute(sql + " AND source_id IS NOT NULL ORDER BY id", args)]


def star_projects(con: sqlite3.Connection, sort_id: int) -> list[int]:
    """Stars the project cards each added row guessed a showcase gave, once read; returns their ids."""
    starred = []
    added = con.execute("SELECT source_id FROM record_sort_rows WHERE sort_id = ? AND state = 'added' AND star = 1 "
                        "AND section = 'project' AND source_id IS NOT NULL", (sort_id,)).fetchall()
    for (source_id,) in added:
        for (item_id,) in con.execute("SELECT DISTINCT e.item_id FROM profile_evidence e JOIN profile_items i "
                                      "ON i.id = e.item_id WHERE e.source_id = ? AND i.kind = 'project' AND "
                                      "i.star = 0", (source_id,)).fetchall():
            profile_store.set_star(con, item_id, True)
            starred.append(item_id)
    return starred


def rematch(con: sqlite3.Connection, sort_id: int, skip: Iterable[str] = ()) -> int:
    """Finds a card again for each proposal still waiting for one (the cards an accepted CV gave); returns how
    many found one. Rows under a folder in `skip` (a course still waiting for its own card) keep waiting."""
    found = 0
    skip = tuple(folder + "/" for folder in skip)
    for r in con.execute("SELECT * FROM record_sort_rows WHERE sort_id = ? AND state = 'proposed' AND "
                         "target_id IS NULL AND section != ''", (sort_id,)).fetchall():
        if not sorter.needs_card(r["section"], None) or r["rel"].startswith(skip):
            continue
        p = sorter.Proposal(Path(r["path"]), r["rel"], r["held"], section=r["section"], doc_kind=r["doc_kind"],
                            mtime=_ts(r["mtime"]))
        sorter.fill_card(con, p)
        if p.target_id is not None:
            found += 1
            with con:
                con.execute("UPDATE record_sort_rows SET target_id = ?, section = ? WHERE id = ?",
                            (p.target_id, p.section, r["id"]))
    return found


def _ts(iso: str | None) -> float:
    try:
        return datetime.strptime(iso or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return 0.0


def remove(con: sqlite3.Connection, sort_id) -> None:
    """Forgets a sort and its proposals, and the files Hal kept for it; the sources it added stay."""
    s = row_of(con, sort_id)
    if s["kind"] == "move" and s["state"] != "undone" and con.execute(
            "SELECT 1 FROM record_sort_kept_out WHERE sort_id = ? LIMIT 1", (s["id"],)).fetchone():
        raise ValueError("Undo the move first, or keep it: forgetting it would lose the way back.")
    with con:
        con.execute("DELETE FROM record_sorts WHERE id = ?", (s["id"],))
    shutil.rmtree(work_dir(s["id"]), ignore_errors=True)

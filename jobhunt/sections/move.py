"""Move my record to sections: a record an older Hal read in whole becomes sources of its sections, undoably.

An older Hal's bulk read wrote a note under `Sources/` for each file it read and filed facts whose evidence
names that note (`profile_evidence.source_rel`) and no source of a section (`source_id` NULL). `propose`
proposes a section for each such note, from what its frontmatter says (`doc_type`, `role`, `category`, and
`source`, where the file came from) and the sorter's rules (`sorter.place_kind`, `sorter.fill_card`); the
person reviews them in the same table as a sorted folder (`sections.sorts`, a sort of kind `move`).

Accepting makes each accepted note a source of its section with its original file (or, when Hal no longer has
the file, the note's own text), read again by the section's reader through `profile.store.swap_source`. Then
the older note's evidence is set aside (`after_accept`): kept in `record_sort_kept_out`, never deleted, so the
move can be undone. "Finish the move" (`finish`) sets aside the evidence of every note nobody accepted. `undo`
takes out every source the move added, puts every piece of evidence back, and gives each card the state it had.

Design decisions, each with its reason:

- Cards the person edited, confirmed or merged keep their state: a read through a section sends a confirmed
  card whose facts changed back to be checked, and a card with no evidence left is hidden, which is right for a
  new source but not for a move of facts the person already checked. Each card's state before the reads is
  noted and the person's own (`PERSONS_STATES`) is put back after (`_keep_states`).
- A found card nobody checked that no evidence names any more is hidden with its state kept in `orphaned_from`,
  as removing a source does, so a source naming it again brings it back.
- The older notes themselves are left where they are: the sections write their own notes, and undoing the move
  leaves the record exactly as it was.
- Someone else's notes (role `others`) are never proposed, and notes of kinds the sections do not take (emails,
  pictures, code files, course summaries) are left out with the reason, as the sorter does.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from jobhunt import db
from jobhunt.ingest import discover, frontmatter, notes, rank
from jobhunt.profile import conflicts, index
from jobhunt.profile import store as profile_store
from jobhunt.sections import pipeline, registry, sorter, sorts, store

log = logging.getLogger(__name__)

OLD_ROLES_OUT = ("others",)
PERSONS_STATES = ("confirmed", "edited")       # the card states only the person sets
ORIGINAL = re.compile(r"^## Original\s*\n+\s*!?\[\[([^\]|]+)", re.M)
# The older read's kinds of document that a section takes, as the sorter's rule of that kind would place them.
RULE_OF = {r.doc_type: r for r in (*rank.RULES, rank.REPO, rank.COURSE, rank.EMAIL, rank.CODE, rank.IMAGE,
                                   rank.MEDIA, rank.ARTWORK, rank.OTHER)}
SAME_AS = {"personal_statement": "letter", "cover_letter": "letter", "award": "certificate", "contract":
           "job_description", "coursework": "report", "essay": "report", "dissertation": "report"}
LEFT_OUT_KINDS = {"email": "An email. Hal does not move emails.", "course": "A summary of a course Hal wrote.",
                  "code": "A code file outside a project.", "image": "A picture with no words.",
                  "media": "A recording.", "artwork": "A picture. Add it to a project by hand if it is your work."}
COURSE_CATEGORIES = ("Study notes", "Slides", "Coursework", "Education")
EVIDENCE_COLUMNS = ("id", "item_id", "source_rel", "quote", "field", "doc_type", "said", "verified", "found_at",
                    "role", "moved_from", "source_id", "place", "line")


def _old_rels(con: sqlite3.Connection) -> list[str]:
    """The older read's notes that facts name: evidence with no source of a section, under `Sources/`."""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT source_rel FROM profile_evidence WHERE source_id IS NULL AND source_rel LIKE ? "
        "ORDER BY source_rel", (notes.SOURCES + "/%",))]


def can_move(con: sqlite3.Connection) -> bool:
    """True when the record holds facts from an older Hal's notes that no move has set aside yet."""
    return bool(_old_rels(con))


def _file_of(vault: Path, note: Path, fm: dict, body: str) -> Path:
    """The file to add for a note: the original where it came from, else the record's copy, else the note."""
    source = str(fm.get("source") or "")
    if source and Path(source).is_absolute() and Path(source).exists():
        return Path(source)
    if (found := ORIGINAL.search(body)) and (vault / found.group(1)).is_file():
        return vault / found.group(1)
    return note


def _placed(p: sorter.Proposal, fm: dict, text: str) -> None:
    """Places one older note by what its frontmatter says, with the sorter's rules."""
    doc_type = str(fm.get("doc_type") or "other").lower()
    role = str(fm.get("role") or "").lower()
    if role in OLD_ROLES_OUT:
        p.leave_out("Someone else's file, so it is not yours to move.")
        return
    if doc_type in LEFT_OUT_KINDS:
        p.leave_out(LEFT_OUT_KINDS[doc_type])
        return
    if doc_type in ("repository", "project") and p.held == registry.FOLDER:
        p.place("project", "repo" if doc_type == "repository" else "local_folder", "A project an older Hal read.")
        return
    rule = RULE_OF.get(SAME_AS.get(doc_type, doc_type), rank.OTHER)
    in_course = str(fm.get("category") or "") in COURSE_CATEGORIES or role == "taught"
    if not sorter.place_kind(p, rule, text, in_course):
        p.leave_out("Hal could not tell where it goes. Give it a place by hand if it belongs in your record.")
        return
    p.why = f"{p.why} (An older Hal filed it as {doc_type.replace('_', ' ')}.)"


def propose(con: sqlite3.Connection) -> sorter.Sorted:
    """A proposal for each older note that facts name, by its frontmatter and the sorter's rules."""
    vault = pipeline.vault()
    out = sorter.Sorted()
    for rel in _old_rels(con):
        note = vault / rel
        if not note.is_file():
            p = sorter.Proposal(note, rel, registry.UPLOAD, note_rel=rel)
            out.proposals.append(p.leave_out("The note is gone from your record."))
            continue
        fm, body = frontmatter.parse(note.read_text(encoding="utf-8", errors="replace"))
        source = str(fm.get("source") or "")
        if source and Path(source).is_dir():
            p = sorter.Proposal(Path(source), rel, registry.FOLDER, note_rel=rel, mtime=Path(source).stat().st_mtime)
        else:
            path = _file_of(vault, note, fm, body)
            st = path.stat()
            digest, _ = discover.hash_and_zero(path)
            p = sorter.Proposal(path, rel, registry.UPLOAD, size=st.st_size, sha256=digest, mtime=st.st_mtime,
                                note_rel=rel)
            if (same := store.by_hash(con, digest)) is not None:
                out.proposals.append(p.leave_out(
                    f"Already in your record, under {registry.get(same['section']).label}."))
                continue
        _placed(p, fm, body)
        if p.state == sorter.PROPOSED:
            # the place it came from names its course or job better than the note's own folder
            p_where = sorter.Proposal(p.path, source or rel, p.held, section=p.section, doc_kind=p.doc_kind,
                                      mtime=p.mtime)
            sorter.fill_card(con, p_where, body)
            p.target_id, p.section = p_where.target_id, p_where.section
        out.proposals.append(p)
    sorter.mark_copies(con, out.proposals)
    return out


def _card_states(con: sqlite3.Connection, item_ids: set[int]) -> dict[int, sqlite3.Row]:
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    return {r["id"]: r for r in con.execute(
        f"SELECT id, state, orphaned_from FROM profile_items WHERE id IN ({marks})", sorted(item_ids))}


def before_accept(con: sqlite3.Connection, sort_id: int) -> dict[int, str]:
    """Notes, once per move, the state of every card an older note names (for `undo`); returns each one's state
    now, for `after_accept` to keep the person's."""
    items = {r[0] for r in con.execute(
        "SELECT DISTINCT e.item_id FROM profile_evidence e JOIN record_sort_rows r ON r.note_rel = e.source_rel "
        "WHERE r.sort_id = ? AND e.source_id IS NULL", (sort_id,))}
    kept = {json.loads(r[0])["id"] for r in con.execute(
        "SELECT row FROM record_sort_kept_out WHERE sort_id = ? AND what = 'card'", (sort_id,))}
    now = _card_states(con, items)
    with con:
        con.executemany("INSERT INTO record_sort_kept_out (sort_id, what, row) VALUES (?, 'card', ?)",
                        [(sort_id, json.dumps({"id": i, "state": r["state"], "orphaned_from": r["orphaned_from"]}))
                         for i, r in now.items() if i not in kept])
    return {i: r["state"] for i, r in now.items()}


def _set_aside(con: sqlite3.Connection, sort_id: int, note_rels: list[str]) -> set[int]:
    """Keeps the older evidence of these notes in `record_sort_kept_out` and takes it off its cards; hides a found
    card nobody checked that nothing names any more. Returns the cards it touched."""
    touched: set[int] = set()
    with con:
        for rel in note_rels:
            found = con.execute("SELECT * FROM profile_evidence WHERE source_id IS NULL AND source_rel = ?",
                                (rel,)).fetchall()
            con.executemany("INSERT INTO record_sort_kept_out (sort_id, what, row) VALUES (?, 'evidence', ?)",
                            [(sort_id, json.dumps({k: e[k] for k in EVIDENCE_COLUMNS})) for e in found])
            con.execute("DELETE FROM profile_evidence WHERE source_id IS NULL AND source_rel = ?", (rel,))
            touched |= {e["item_id"] for e in found}
        now = db.now()
        for item_id in touched:
            r = con.execute("SELECT state, origin, merged_into FROM profile_items WHERE id = ?", (item_id,)).fetchone()
            if r is None:
                continue
            named = con.execute("SELECT 1 FROM profile_evidence WHERE item_id = ? LIMIT 1", (item_id,)).fetchone()
            if not named and r["origin"] == "found" and r["state"] == "hal" and r["merged_into"] is None:
                con.execute("UPDATE profile_items SET state = 'hidden', orphaned_from = 'hal', updated = ? "
                            "WHERE id = ?", (now, item_id))
            else:
                profile_store.refold(con, item_id)
        conflicts.refresh(con)
    index.forget(con)
    return touched


def _keep_states(con: sqlite3.Connection, states: dict[int, str]) -> None:
    """Gives back each card the person had confirmed or edited the state the move's reads took from it."""
    now = _card_states(con, set(states))
    with con:
        for item_id, was in states.items():
            r = now.get(item_id)
            if r is not None and was in PERSONS_STATES and r["state"] != was:
                con.execute("UPDATE profile_items SET state = ?, orphaned_from = '', updated = ? WHERE id = ?",
                            (was, db.now(), item_id))


def after_accept(con: sqlite3.Connection, sort_id: int, states: dict[int, str]) -> int:
    """Sets aside the older evidence of every note now added as a source, and keeps the person's card states;
    returns how many notes it set aside."""
    rels = [r[0] for r in con.execute("SELECT note_rel FROM record_sort_rows WHERE sort_id = ? AND state = 'added' "
                                      "AND note_rel IS NOT NULL", (sort_id,))]
    _set_aside(con, sort_id, rels)
    _keep_states(con, states)
    return len(rels)


def finish(con: sqlite3.Connection, sort_id) -> int:
    """Sets aside the older evidence of every note of the move nobody accepted, so the record is all sections;
    returns how many notes. ValueError for a sort that is not a move, or one undone."""
    s = _move(con, sort_id)
    states = before_accept(con, s["id"])
    rels = [r[0] for r in con.execute("SELECT note_rel FROM record_sort_rows WHERE sort_id = ? AND state != 'added' "
                                      "AND note_rel IS NOT NULL", (s["id"],))]
    _set_aside(con, s["id"], rels)
    _keep_states(con, states)
    return len(rels)


def _move(con: sqlite3.Connection, sort_id) -> sqlite3.Row:
    """The move's sort; ValueError for another kind of sort, one undone, or one still adding what was accepted
    (its sources would be read while they are taken out)."""
    from jobhunt.sections import sort_actions

    s = sorts.row_of(con, sort_id)
    if s["kind"] != "move":
        raise ValueError("Only a move of your record can be finished or undone.")
    if s["state"] == "undone":
        raise ValueError("This move was undone already.")
    if sort_actions.adding(s["id"]):
        raise ValueError("Hal is still adding what you accepted. Try again when it is done.")
    return s


def undo(con: sqlite3.Connection, sort_id) -> dict:
    """Undoes the whole move: removes every source it added, puts back every piece of evidence it set aside, and
    gives every card the state it had before; returns {"removed": sources, "restored": evidence}.

    A card the person confirmed or edited since keeps their word. ValueError for a sort that is not a move, or
    one undone already.
    """
    s = _move(con, sort_id)
    added = [r[0] for r in con.execute("SELECT source_id FROM record_sort_rows WHERE sort_id = ? AND state = 'added' "
                                       "AND source_id IS NOT NULL", (s["id"],))]
    for source_id in added:
        try:
            pipeline.remove(con, source_id)
        except LookupError:      # the person removed it already
            continue
    kept = con.execute("SELECT what, row FROM record_sort_kept_out WHERE sort_id = ? ORDER BY id",
                       (s["id"],)).fetchall()
    evidence = [json.loads(r["row"]) for r in kept if r["what"] == "evidence"]
    cards = [json.loads(r["row"]) for r in kept if r["what"] == "card"]
    marks = ",".join("?" * len(EVIDENCE_COLUMNS))
    with con:
        for e in evidence:
            if con.execute("SELECT 1 FROM profile_items WHERE id = ?", (e["item_id"],)).fetchone() is None:
                continue      # a card the person deleted since stays deleted
            values = [e[k] for k in EVIDENCE_COLUMNS]
            if con.execute("SELECT 1 FROM profile_evidence WHERE id = ?", (e["id"],)).fetchone():
                values[0] = None
            con.execute(f"INSERT INTO profile_evidence ({', '.join(EVIDENCE_COLUMNS)}) VALUES ({marks})", values)
        now = db.now()
        for c in cards:
            r = con.execute("SELECT state FROM profile_items WHERE id = ?", (c["id"],)).fetchone()
            if r is None or r["state"] in PERSONS_STATES:
                continue
            con.execute("UPDATE profile_items SET state = ?, orphaned_from = ?, updated = ? WHERE id = ?",
                        (c["state"], c["orphaned_from"], now, c["id"]))
        for item_id in {e["item_id"] for e in evidence} | {c["id"] for c in cards}:
            profile_store.refold(con, item_id)
        conflicts.refresh(con)
        con.execute("DELETE FROM record_sort_kept_out WHERE sort_id = ?", (s["id"],))
        con.execute("UPDATE record_sort_rows SET state = 'proposed', source_id = NULL WHERE sort_id = ? AND "
                    "state IN ('accepted', 'added')", (s["id"],))
    index.forget(con)
    sorts.set_state(con, s["id"], "undone")
    log.info("undid move %s: %s sources removed, %s pieces of evidence back", s["id"], len(added), len(evidence))
    return {"removed": len(added), "restored": len(evidence)}


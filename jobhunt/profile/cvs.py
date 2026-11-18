"""Which of the CVs the person added to Your record leads: the lead CV, and the others that only fill gaps.

The CVs section (`jobhunt.sections`) keeps one row per CV in `record_sources`. The lead CV is the first one,
in the person's order on My CVs, that is in use for applications ("Use this for applications", matched by the
file's SHA-256 through `cvstudio.store.file_cvs`) and was read; when none is, the newest-dated CV read
(`record_sources.dated`, the CV's own date). Every other CV in the section only fills gaps.

Design decisions, each with its reason:

- One answer for everyone: folding a card (`store.refold`), the Inconsistencies list (`conflicts`), writing
  (`writing.CvAuthority`) and the page all ask this module, so the card, the letters and the list of CVs
  never disagree on which CV leads (sections plan, 2026-11-09).
- One "main CV" for the person, not two: the CV Hal writes applications from is the CV whose facts lead
  (2026-11-18). Until then the record had its own "Make this my current CV" flag beside My CVs' star, and the
  two could name different CVs. A CV in use that is not read yet does not lead until its read ends, so its
  arrival never leaves the record with no leading facts.
- `record_sources.current` no longer holds a choice: `settle` writes the lead there, so the column always
  names the CV the cards were last folded for, and a change of lead (a CV turned on or off, the order on My
  CVs, a read, a CV removed, or an update from a version that kept the person's old flag) is seen by
  comparing it with the rule's answer. A migration may not drop a column, so it is kept and kept true.
- An older CV's facts rank at `merge.OLDER_CV_RANK`: below the lead CV and below documents such as a
  reference or a contract, above notes. A field the lead CV or a document gives never takes another CV's
  value; another CV only fills what nothing above it gives, and the card says "from an older CV".
- A project's facts from a CV are the person's summary of it, not the project itself; a project card that
  only CVs give is marked (`cv_only`) so the page says "from your CV only" until a project source backs it.
"""
from __future__ import annotations

import sqlite3

from jobhunt.core import events
from jobhunt.profile import merge

SECTION = "cv"
NO_DATE = -1               # a CV with no date of its own is older than every dated one
NO_SOURCE = -1             # no source has this id: with no lead CV, none is marked


def _rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT id, current, dated, added, state, note_rel, sha256 FROM record_sources "
                       "WHERE section = ? ORDER BY id", (SECTION,)).fetchall()


def in_use(con: sqlite3.Connection) -> dict[str, int]:
    """The SHA-256 of every file of a CV in use for applications, with that CV's place in the person's order."""
    from jobhunt.cvstudio import store as cv_store

    return {sha: cv["rank"] for sha, cv in cv_store.file_cvs(con).items() if cv["rank"] is not None}


def _lead(rows: list[sqlite3.Row], used: dict[str, int]) -> sqlite3.Row | None:
    read = [r for r in rows if r["state"] == "read"]
    chosen = [r for r in read if r["sha256"] in used]
    if chosen:
        return min(chosen, key=lambda r: (used[r["sha256"]], r["id"]))
    if not read:
        return None
    return max(read, key=lambda r: (merge.month_index(r["dated"] or "", "end") or NO_DATE, r["added"], r["id"]))


def _lead_of(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    return _lead(rows, in_use(con)) if rows else None


def current_source(con: sqlite3.Connection) -> int | None:
    """The id of the lead CV: the first read CV in use for applications, else the newest-dated CV read; None for
    no CV."""
    lead = _lead_of(con, _rows(con))
    return lead["id"] if lead is not None else None


def settle(con: sqlite3.Connection, *, refold: bool = False) -> list[int]:
    """Writes the lead CV into `record_sources.current`; when it changed (or with `refold`), works out again every
    card the CVs give. A change of lead also raises the record's revision and publishes a `profile` event, so
    documents waiting on the record and open pages follow. Returns the ids of the cards worked out again.

    Commits `con`. Call it after anything that may change the lead: a CV in use turned on or off or put in
    another order, a CV read, moved or removed.
    """
    rows = _rows(con)
    lead = _lead_of(con, rows)
    want = [lead["id"]] if lead is not None else []
    changed = [r["id"] for r in rows if r["current"]] != want
    if changed:
        with con:
            con.execute("UPDATE record_sources SET current = (id = ?) WHERE section = ?",
                        (want[0] if want else NO_SOURCE, SECTION))
    if not (changed or refold):
        return []
    from jobhunt import record
    from jobhunt.profile import store

    ids = store.refold_sources(con, [r["id"] for r in rows])
    if changed:
        record.bump_revision(con)
        events.publish("profile", lead=want[0] if want else None)
    return ids


def older_sources(con: sqlite3.Connection) -> frozenset[int]:
    """The ids of every CV in the CVs section except the lead one."""
    rows = _rows(con)
    lead = _lead_of(con, rows)
    return frozenset(r["id"] for r in rows if lead is None or r["id"] != lead["id"])


def cv_sources(con: sqlite3.Connection) -> list[int]:
    """The ids of every CV in the CVs section."""
    return [r["id"] for r in _rows(con)]


def notes(con: sqlite3.Connection) -> tuple[str, frozenset[str]]:
    """The record notes of the lead CV and of the others: ("", empty) when no CV was read."""
    rows = _rows(con)
    lead = _lead_of(con, rows)
    if lead is None or not lead["note_rel"]:
        return "", frozenset()
    return lead["note_rel"], frozenset(r["note_rel"] for r in rows if r["note_rel"] and r["id"] != lead["id"])


def rank(doc_type: str, source_id: int | None, older: frozenset[int]) -> float:
    """How much one piece of evidence is trusted: an older CV's at `merge.OLDER_CV_RANK`, any other by its
    document type (`merge.rank`)."""
    if source_id is not None and source_id in older:
        return merge.OLDER_CV_RANK
    return merge.rank(doc_type)


def cv_only(item: dict) -> bool:
    """True for a project card that only CVs give: no project source of its own backs it."""
    evidence = item.get("evidence") or []
    return item.get("kind") == "project" and item.get("origin") != "you" and bool(evidence) and all(
        merge.doc_group(e.get("doc_type", "")) == "cv" for e in evidence)

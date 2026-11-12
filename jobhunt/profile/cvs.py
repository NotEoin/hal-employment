"""Which of the CVs the person added to Your record leads: the current CV, and the older ones that fill gaps.

The CVs section (`jobhunt.sections`) keeps one row per CV in `record_sources`. The current CV is the one the
person marked current (`record_sources.current`, set by "Make this my current CV"), else the newest-dated CV
that was read (`record_sources.dated`, the CV's own date). Every other CV in the section is older.

Design decisions, each with its reason:

- One answer for everyone: folding a card (`store.refold`), the Inconsistencies list (`conflicts`), writing
  (`writing.CvAuthority`) and the page all ask this module, so the card, the letters and the list of CVs
  never disagree on which CV leads (sections plan, 2026-11-09).
- The flag is kept only for the person's choice. Hal's own choice is worked out each time from the dates,
  so a newer CV added later leads at once, and clearing the flag gives the choice back to the dates.
- An older CV's facts rank at `merge.OLDER_CV_RANK`: below the current CV and below documents such as a
  reference or a contract, above notes. A field the current CV or a document gives never takes an older CV's
  value; an older CV only fills what nothing above it gives, and the card says "from an older CV".
- A project's facts from a CV are the person's summary of it, not the project itself; a project card that
  only CVs give is marked (`cv_only`) so the page says "from your CV only" until a project source backs it.
"""
from __future__ import annotations

import sqlite3

from jobhunt.profile import merge

SECTION = "cv"
NO_DATE = -1               # a CV with no date of its own is older than every dated one


def _rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT id, current, dated, added, state, note_rel FROM record_sources WHERE section = ? "
                       "ORDER BY id", (SECTION,)).fetchall()


def _lead(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    chosen = [r for r in rows if r["current"]]
    if chosen:
        return chosen[0]
    read = [r for r in rows if r["state"] == "read"]
    if not read:
        return None
    return max(read, key=lambda r: (merge.month_index(r["dated"] or "", "end") or NO_DATE, r["added"], r["id"]))


def current_source(con: sqlite3.Connection) -> int | None:
    """The id of the current CV: the one the person marked, else the newest-dated CV read; None for no CV."""
    lead = _lead(_rows(con))
    return lead["id"] if lead is not None else None


def chosen_by_person(con: sqlite3.Connection) -> bool:
    """True when the person marked a CV current themselves, rather than Hal choosing the newest."""
    return any(r["current"] for r in _rows(con))


def older_sources(con: sqlite3.Connection) -> frozenset[int]:
    """The ids of every CV in the CVs section except the current one."""
    rows = _rows(con)
    lead = _lead(rows)
    return frozenset(r["id"] for r in rows if lead is None or r["id"] != lead["id"])


def cv_sources(con: sqlite3.Connection) -> list[int]:
    """The ids of every CV in the CVs section."""
    return [r["id"] for r in _rows(con)]


def notes(con: sqlite3.Connection) -> tuple[str, frozenset[str]]:
    """The record notes of the current CV and of the older ones: ("", empty) when no CV was read."""
    rows = _rows(con)
    lead = _lead(rows)
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

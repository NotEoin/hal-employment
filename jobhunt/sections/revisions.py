"""The reads of a source kept side by side: what each gave, what changed since the last, and going back one.

After each read the pipeline calls `record`: the facts it filed and the reader's `details` (tools, commit
counts, dates, a hash) are kept as that revision (`record_source_revisions`, migration 0030), and the
source's `what_changed` says in a few words how the details moved from the read before (`record_sources.details`,
which the pipeline replaces at each read and the refresh, `sections.refresh`, compares against). `go_back` puts
the previous read's facts and details back.

Design decisions, each with its reason:

- Only the last `KEEP` reads are kept: going back is for a read that went wrong, not a history of the
  project (git keeps that).
- Going back swaps the facts through `profile.store.swap_source`, so a card the person edited keeps their
  words, as on any read. The newest revision is dropped, so going back twice is not possible: there is
  only one read to go back to.
- "What changed" compares details only, never the AI's words: tools added or removed, commits since, new
  dates. A first read has nothing to compare and says nothing.
"""
from __future__ import annotations

import json
import sqlite3

from jobhunt import db
from jobhunt.profile import store as profile_store

KEEP = 2                   # this read and the one before it
TOOLS_SHOWN = 5            # tools named in a "what changed" note; past this it says how many more

def _names(items: list[str]) -> str:
    shown = ", ".join(items[:TOOLS_SHOWN])
    return shown + (f" and {len(items) - TOOLS_SHOWN} more" if len(items) > TOOLS_SHOWN else "")


def words(before: dict, after: dict) -> str:
    """What changed from one read's details to the next, in a short sentence or two; "" for no change."""
    if not before:
        return ""
    parts = []
    old_tools, new_tools = list(before.get("tools") or []), list(after.get("tools") or [])
    added = [t for t in new_tools if t not in old_tools]
    gone = [t for t in old_tools if t not in new_tools]
    if added:
        parts.append(f"Tools added: {_names(added)}.")
    if gone:
        parts.append(f"Tools removed: {_names(gone)}.")
    since = int(after.get("commits") or 0) - int(before.get("commits") or 0)
    if since > 0:
        parts.append(f"{since} new commit{'s' if since != 1 else ''}.")
    if after.get("last") and after.get("last") != before.get("last"):
        parts.append(f"Last changed {after['last']}.")
    if after.get("role") != before.get("role") and after.get("role"):
        parts.append(f"Your part is now: {after['role']}.")
    if not parts and after.get("hash") and after.get("hash") != before.get("hash"):
        parts.append("The page's words changed.")
    return " ".join(parts)


def record(con: sqlite3.Connection, source_id: int, facts: list, details: dict, *, before: dict) -> str:
    """Keeps this read's facts and details as the source's current revision, and says what changed since the
    read whose details were `before`."""
    r = con.execute("SELECT revision FROM record_sources WHERE id = ?", (source_id,)).fetchone()
    changed = words(before, details)
    now = db.now()
    with con:
        con.execute("INSERT OR REPLACE INTO record_source_revisions (source_id, revision, facts, details, what_changed,"
                    " read_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (source_id, r["revision"], json.dumps(facts), json.dumps(details), changed, now))
        con.execute("DELETE FROM record_source_revisions WHERE source_id = ? AND id NOT IN (SELECT id FROM "
                    "record_source_revisions WHERE source_id = ? ORDER BY revision DESC LIMIT ?)",
                    (source_id, source_id, KEEP))
        con.execute("UPDATE record_sources SET what_changed = ? WHERE id = ?", (changed, source_id))
    return changed


def can_go_back(con: sqlite3.Connection, source_id: int) -> bool:
    """True when an earlier read of the source is kept."""
    n = con.execute("SELECT count(*) FROM record_source_revisions WHERE source_id = ?", (source_id,)).fetchone()[0]
    return n >= KEEP


def go_back(con: sqlite3.Connection, source_id: int) -> dict:
    """Puts the previous read's facts back in the profile; returns {"hidden": [card ids], "revision"}.

    LookupError when no earlier read is kept.
    """
    rows = con.execute("SELECT * FROM record_source_revisions WHERE source_id = ? ORDER BY revision DESC LIMIT 2",
                       (source_id,)).fetchall()
    if len(rows) < KEEP:
        raise LookupError("Hal keeps no earlier read of this to go back to.")
    newest, earlier = rows
    facts = [tuple(f) for f in json.loads(earlier["facts"])]
    swapped = profile_store.swap_source(con, source_id, facts)
    with con:
        con.execute("DELETE FROM record_source_revisions WHERE id = ?", (newest["id"],))
        con.execute("UPDATE record_sources SET what_changed = ?, last_changed = ?, details = ? WHERE id = ?",
                    (f"Went back to the read of {earlier['read_at'][:10]}.", db.now(), earlier["details"],
                     source_id))
    return {"hidden": swapped["hidden"], "revision": earlier["revision"]}

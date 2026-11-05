"""The `ingest_items` table: one row per file or unit an ingest found, with the stage it reached.

A row is written when the file is found and updated as each stage ends, and the action commits after each
item, so a restart loses at most the item in hand. `summary` is the shape `GET /api/v2/record/ingest`
returns. Paths are stored, never document text: the text lives in the note the item produced. A zip member
refused while unpacking is an item too (state error), so the page lists it with the other failures.
"""
from __future__ import annotations

import json
import sqlite3

from jobhunt.core import clock

FINAL = ("done", "duplicate", "not_downloaded", "no_text", "skipped", "error")
FIRST_PATHS = 10            # not-downloaded files named on the page; the rest are counted
RECENT_ERRORS = 10
STAGES = ("discovered", "converted", "understood", "written")
KIND = "record.ingest"


def add(con: sqlite3.Connection, batch: str, *, path: str, rel: str, kind: str = "file", unit: str | None = None,
        size: int | None = None, sha256: str = "", mtime: str = "", magic: str = "", rank: int | None = None,
        category: str = "", doc_type: str = "", state: str = "pending") -> int:
    """Adds one item to `batch` (or finds it, when this batch already has the path); returns its id."""
    at = clock.iso()
    con.execute("""INSERT OR IGNORE INTO ingest_items (batch, path, rel, kind, unit, size, sha256, mtime, magic,
                       rank, category, doc_type, stage, state, created, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, ?)""",
                (batch, path, rel, kind, unit, size, sha256, mtime, magic, rank, category, doc_type, state, at, at))
    return con.execute("SELECT id FROM ingest_items WHERE batch = ? AND path = ?", (batch, path)).fetchone()[0]


def seen_paths(con: sqlite3.Connection, batch: str) -> set[str]:
    """The paths this batch already holds, so a resumed ingest does not hash them again."""
    return {r[0] for r in con.execute("SELECT path FROM ingest_items WHERE batch = ?", (batch,))}


def update(con: sqlite3.Connection, item_id: int, **fields) -> None:
    """Sets columns of one item and its `updated` time."""
    fields["updated"] = clock.iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE ingest_items SET {cols} WHERE id = ?", (*fields.values(), item_id))


def first_copy(con: sqlite3.Connection, sha256: str, item_id: int) -> sqlite3.Row | None:
    """The first other item with these bytes that was read into a note, in this ingest or an earlier one."""
    if not sha256:
        return None
    return con.execute("""SELECT * FROM ingest_items WHERE sha256 = ? AND id != ? AND state = 'done'
                          ORDER BY id LIMIT 1""", (sha256, item_id)).fetchone()


def pending(con: sqlite3.Connection, batch: str) -> list[sqlite3.Row]:
    """The items still to read, best rank first, units before the files in them."""
    return con.execute("""SELECT * FROM ingest_items WHERE batch = ? AND state = 'pending'
                          ORDER BY rank, kind = 'file', rel""", (batch,)).fetchall()


def counts(con: sqlite3.Connection, batch: str) -> dict:
    """Items by state, by stage reached and, for those read, by category."""
    by_state = dict(con.execute("SELECT state, count(*) FROM ingest_items WHERE batch = ? GROUP BY state",
                                (batch,)).fetchall())
    by_stage = dict(con.execute("SELECT stage, count(*) FROM ingest_items WHERE batch = ? GROUP BY stage",
                                (batch,)).fetchall())
    by_category = dict(con.execute("""SELECT category, count(*) FROM ingest_items
                                      WHERE batch = ? AND state = 'done' AND category != ''
                                      GROUP BY category ORDER BY category""", (batch,)).fetchall())
    return {"states": by_state, "stages": {s: by_stage.get(s, 0) for s in STAGES}, "categories": by_category}


def latest_action(con: sqlite3.Connection) -> sqlite3.Row | None:
    """The newest ingest action, running or ended."""
    return con.execute("SELECT * FROM actions WHERE kind = ? ORDER BY created DESC LIMIT 1", (KIND,)).fetchone()


def _eta(con: sqlite3.Connection, batch: str, action: sqlite3.Row, left: int) -> int | None:
    """Seconds left from this run's pace on the items it read; earlier runs of a resumed ingest do not count."""
    if action["state"] != "running" or not action["started"] or not left:
        return None
    started = action["started"]
    done = con.execute("""SELECT count(*) FROM ingest_items WHERE batch = ? AND state != 'pending'
                              AND updated >= ? AND stage != 'discovered'""", (batch, started)).fetchone()[0]
    if not done:
        return None
    elapsed = (clock.now_utc() - clock.parse(started)).total_seconds()
    return max(0, round(elapsed / done * left))


def summary(con: sqlite3.Connection) -> dict:
    """The latest ingest for the page: state, done, total, eta_s, counts, current item, not downloaded, errors."""
    action = latest_action(con)
    if action is None:
        return {"state": "none", "action_id": None, "done": 0, "total": 0, "eta_s": None, "stages": {},
                "states": {}, "categories": {}, "current": "", "not_downloaded": 0, "not_downloaded_paths": [],
                "errors": [], "left_out": {}}
    params = json.loads(action["params"] or "{}")
    batch = params.get("batch") or ""
    c = counts(con, batch)
    total = sum(c["states"].values())
    left = c["states"].get("pending", 0)
    missing = [r[0] for r in con.execute("""SELECT rel FROM ingest_items WHERE batch = ? AND state = 'not_downloaded'
                                            ORDER BY rel LIMIT ?""", (batch, FIRST_PATHS))]
    errors = [{"path": r["rel"], "error": r["error"]} for r in con.execute(
        """SELECT rel, error FROM ingest_items WHERE batch = ? AND state = 'error'
           ORDER BY updated DESC LIMIT ?""", (batch, RECENT_ERRORS))]
    result = json.loads(action["result"]) if action["result"] else {}
    result = result if isinstance(result, dict) else {}
    return {"state": action["state"], "action_id": action["id"], "batch": batch, "done": total - left,
            "total": total, "eta_s": _eta(con, batch, action, left), "stages": c["stages"], "states": c["states"],
            "categories": c["categories"], "current": (action["message"] or "") if action["state"] == "running" else "",
            "not_downloaded": c["states"].get("not_downloaded", 0), "not_downloaded_paths": missing,
            "errors": errors,
            "left_out": result.get("left_out", {}), "started": action["started"], "ended": action["ended"]}

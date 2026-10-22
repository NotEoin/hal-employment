"""The settings store: values in the `settings` table of jobs.db, their history, and a per-process cache.

Only values that differ from their default are stored, so a better default in an update reaches
everyone who never changed it. One change is one transaction: the value row, its `settings_history` row,
`meta.settings_rev + 1`, the Activity row with Undo and, for a goal setting, the `goal_versions` row. So a
crash never leaves a value without its history, or a goal without its version.

Design notes:
- Each process caches the table, keyed by the database file, so a test or importer that switches homes
  never sees another home's values. The cache is dropped when this process writes, and re-read when
  `meta.settings_rev` moved, checked at most every `REV_CHECK_S` seconds (the server sees a command-line
  change within that time).
- This module knows nothing about the registry: it holds JSON values by key. Defaults, parsing,
  validation and the words of the Activity row come from `core.settings`.
- Secret and private kinds never reach this table; history records "(saved)" for them in case a caller
  passes one, so no value of theirs is ever written here.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

REV_CHECK_S = 2.0          # the server sees a CLI write within 2 seconds
SECRET_KINDS = ("secret", "private")
SAVED = "(saved)"          # what history records for secrets and private answers
AREA = "settings"          # the Activity area of every settings change
HISTORY_COLUMNS = ("id", "at", "key", "old", "new", "by", "session_id", "activity_id")

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_rev = -1
_cache_db = ""
_checked_at = 0.0


@dataclass
class Activity:
    """The Activity row a change records, in the same transaction as the value."""

    text: str
    why: str
    kind: str = "setting.changed"
    icon: str = "settings"
    tone: str = "info"
    undo: dict | None = None
    changed: dict | None = None
    remote: bool = False


@dataclass
class Written:
    """What one stored change produced."""

    old: Any
    activity_id: int | None
    goal_version: int | None
    history_id: int | None


def _missing_table(e: sqlite3.OperationalError) -> bool:
    # Before migration 0003 has run, the table is absent and every key has its default.
    return "no such table" in str(e)


def _connect() -> sqlite3.Connection:
    from ... import db

    return db.connect()


def _db_name(con: sqlite3.Connection | None) -> str:
    """The file behind `con`, or the one `db.connect()` would open, which keys the cache."""
    if con is None:
        from .. import paths

        return os.path.realpath(paths.db_path())
    row = con.execute("PRAGMA database_list").fetchone()
    return os.path.realpath(row[2]) if row and row[2] else ""


def revision(con: sqlite3.Connection | None = None) -> int:
    """`meta.settings_rev`: rises on every write by any process."""
    own = con is None
    con = con or _connect()
    try:
        row = con.execute("SELECT v FROM meta WHERE k = 'settings_rev'").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError as e:
        if _missing_table(e):
            return 0
        raise
    finally:
        if own:
            con.close()


def read_all(con: sqlite3.Connection) -> dict[str, Any]:
    """Every stored value by key, decoded from JSON."""
    try:
        rows = con.execute("SELECT key, value FROM settings").fetchall()
    except sqlite3.OperationalError as e:
        if _missing_table(e):
            return {}
        raise
    return {r[0]: json.loads(r[1]) for r in rows}


def values(con: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The cached stored values, refreshed when another process has written since."""
    global _cache, _cache_rev, _cache_db, _checked_at
    with _lock:
        now = time.monotonic()
        name = _db_name(con)
        if _cache is not None and name == _cache_db and now - _checked_at < REV_CHECK_S:
            return dict(_cache)
        own = con is None
        con = con or _connect()
        try:
            rev = revision(con)
            if _cache is None or name != _cache_db or rev != _cache_rev:
                _cache, _cache_rev, _cache_db = read_all(con), rev, name
            _checked_at = now
            return dict(_cache)
        finally:
            if own:
                con.close()


def stored(key: str, con: sqlite3.Connection | None = None) -> Any | None:
    """The stored value of `key`, or None when it has its default."""
    return values(con).get(key)


def drop_cache() -> None:
    """Forgets the cache, so the next read goes to the database."""
    global _cache, _cache_rev, _cache_db
    with _lock:
        _cache, _cache_rev, _cache_db = None, -1, ""


def _insert_activity(con: sqlite3.Connection, act: Activity, at: str, session_id: str | None) -> int | None:
    """The Activity row, once the activity table exists (migration 0004)."""
    if not act.why.strip():
        raise ValueError("every Activity row needs a why")
    try:
        cur = con.execute(
            """INSERT INTO activity (at, area, kind, icon, tone, text, why, undo, changed, session_id, remote)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (at, AREA, act.kind, act.icon, act.tone, act.text, act.why,
             json.dumps(act.undo) if act.undo is not None else None,
             json.dumps(act.changed) if act.changed is not None else None, session_id, int(act.remote)))
    except sqlite3.OperationalError as e:
        if _missing_table(e):
            return None
        raise
    return cur.lastrowid


def _insert_goal_version(con: sqlite3.Connection, at: str, by: str, summary: str, data: dict) -> int | None:
    try:
        cur = con.execute("INSERT INTO goal_versions (at, by, summary, data) VALUES (?, ?, ?, ?)",
                          (at, by, summary, json.dumps(data, sort_keys=True)))
    except sqlite3.OperationalError as e:
        if _missing_table(e):
            return None
        raise
    return cur.lastrowid


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One `BEGIN IMMEDIATE` transaction: committed at the end of the block, rolled back on any error."""
    if con.in_transaction:
        raise RuntimeError("a settings change needs its own transaction; commit the open one first")
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        drop_cache()


def change_in(con: sqlite3.Connection, key: str, value: Any, *, default: Any, by: str, at: str, kind: str = "",
              session_id: str | None = None, activity: Activity | Callable[[Any], Activity] | None = None,
              goal: tuple[str, Callable[[sqlite3.Connection], dict]] | None = None) -> Written:
    """Stores one value with its history, revision, Activity row and goal version, inside the open transaction.

    `activity` may be a function of the old value, so its Undo can name it. `goal` is (summary, data_fn):
    `data_fn(con)` runs after the value is written, so the version holds the goal as it now is.
    """
    secret = kind in SECRET_KINDS
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    old = json.loads(row[0]) if row else default
    if value == default:
        con.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        con.execute("""INSERT INTO settings (key, value, updated, by) VALUES (?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated,
                                                      by = excluded.by""",
                    (key, json.dumps(value), at, by))
    if callable(activity):
        activity = activity(old)
    activity_id = _insert_activity(con, activity, at, session_id) if activity is not None else None
    cur = con.execute(
        "INSERT INTO settings_history (at, key, old, new, by, session_id, activity_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (at, key, SAVED if secret else json.dumps(old), SAVED if secret else json.dumps(value), by, session_id,
         activity_id))
    con.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('settings_rev', '0')")
    con.execute("UPDATE meta SET v = CAST(CAST(v AS INTEGER) + 1 AS TEXT) WHERE k = 'settings_rev'")
    goal_version = None
    if goal is not None:
        summary, data_fn = goal
        goal_version = _insert_goal_version(con, at, by, summary, data_fn(con))
    return Written(old=old, activity_id=activity_id, goal_version=goal_version, history_id=cur.lastrowid)


def change(con: sqlite3.Connection, key: str, value: Any, **kw: Any) -> Written:
    """`change_in` in a transaction of its own."""
    with transaction(con):
        return change_in(con, key, value, **kw)


def write(con: sqlite3.Connection, key: str, value: Any, *, default: Any, by: str, kind: str = "",
          session_id: str | None = None, at: str) -> Any:
    """Stores `value` (or removes the row when it equals `default`) with its history; returns the old value."""
    return change(con, key, value, default=default, by=by, kind=kind, session_id=session_id, at=at).old


def history(con: sqlite3.Connection, key: str | None = None, limit: int = 50) -> list[dict]:
    """The newest changes first, for one key or all; secret and private values read "(saved)"."""
    sql = f"SELECT {', '.join(HISTORY_COLUMNS)} FROM settings_history"
    args: tuple = ()
    if key is not None:
        sql += " WHERE key = ?"
        args = (key,)
    out = []
    for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall():
        item = dict(zip(HISTORY_COLUMNS, tuple(r), strict=True))
        for side in ("old", "new"):
            raw = item[side]
            item[side] = raw if raw in (None, SAVED) else json.loads(raw)
        out.append(item)
    return out

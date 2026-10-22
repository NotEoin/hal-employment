"""The minimal settings store: values in the `settings` table of jobs.db, their history, and a per-process cache.

Only values that differ from their default are stored, so a better default in an update reaches
everyone who never changed it. Every write bumps `meta.settings_rev` in the same transaction; each process
keeps a cache of the table and drops it when it writes, or when the revision moved (checked at most every
`REV_CHECK_S` seconds), so the server sees a command-line change within that time.

This store knows nothing about the registry: it holds JSON values by key. Defaults, parsing and validation
live in `core.settings` over the registry.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

REV_CHECK_S = 2.0          # the server sees a CLI write within 2 seconds
SECRET_KINDS = ("secret", "private")
SAVED = "(saved)"          # what history records for secrets and private answers

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_rev = -1
_checked_at = 0.0


def _missing_table(e: sqlite3.OperationalError) -> bool:
    # Before migration 0003 has run, the table is absent and every key has its default.
    return "no such table" in str(e)


def _connect() -> sqlite3.Connection:
    from ... import db

    return db.connect()


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
    global _cache, _cache_rev, _checked_at
    with _lock:
        now = time.monotonic()
        if _cache is not None and now - _checked_at < REV_CHECK_S:
            return dict(_cache)
        own = con is None
        con = con or _connect()
        try:
            rev = revision(con)
            if _cache is None or rev != _cache_rev:
                _cache, _cache_rev = read_all(con), rev
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
    global _cache, _cache_rev
    with _lock:
        _cache, _cache_rev = None, -1


def write(con: sqlite3.Connection, key: str, value: Any, *, default: Any, by: str, kind: str = "",
          session_id: str | None = None, at: str) -> Any:
    """Stores `value` (or removes the row when it equals `default`) with its history; returns the old value.

    Runs in one transaction with the history row and the revision bump; the caller commits nothing.
    """
    secret = kind in SECRET_KINDS
    with con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        old = json.loads(row[0]) if row else default
        if value == default:
            con.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            con.execute("""INSERT INTO settings (key, value, updated, by) VALUES (?, ?, ?, ?)
                           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated,
                                                          by = excluded.by""",
                        (key, json.dumps(value), at, by))
        con.execute("INSERT INTO settings_history (at, key, old, new, by, session_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (at, key, SAVED if secret else json.dumps(old), SAVED if secret else json.dumps(value), by,
                     session_id))
        con.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('settings_rev', '0')")
        con.execute("UPDATE meta SET v = CAST(CAST(v AS INTEGER) + 1 AS TEXT) WHERE k = 'settings_rev'")
    drop_cache()
    return old

"""Which AI Hal uses: `ai.provider`, chosen once and then never changed by itself.

Every other setting follows its default until the person changes it. This one must not: its default depends
on whether Ollama answers right now, so a stopped Ollama would otherwise send the person's data to Claude.
So the first time Hal needs an answer (the first server start, or the first AI call) with no choice stored,
it stores the suggestion once, `by="setup"`, with an Activity row saying so. Setup and the importers write
their own choice the same way.

The minimal settings store deletes a row whose value equals the default, so `keep_stored` puts the row back after any
change.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading

from ..core import clock

log = logging.getLogger(__name__)

KEY = "ai.provider"
BY_SETUP = "setup"
_lock = threading.Lock()


def _con() -> sqlite3.Connection:
    from .. import db

    return db.connect()


def _label(value: str) -> str:
    from ..core import settings

    return dict(settings.setting(KEY).choices).get(value, value)


def _stored(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def _bump(con: sqlite3.Connection) -> None:
    con.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('settings_rev', '0')")
    con.execute("UPDATE meta SET v = CAST(CAST(v AS INTEGER) + 1 AS TEXT) WHERE k = 'settings_rev'")


def _pin(con: sqlite3.Connection, value: str, by: str, *, history: bool) -> bool:
    """Stores `value` unless a choice is already stored; True when this call stored it."""
    con.execute("BEGIN IMMEDIATE")
    try:
        if _stored(con) is not None and history:
            con.execute("ROLLBACK")
            return False
        at = clock.iso()
        con.execute("""INSERT INTO settings (key, value, updated, by) VALUES (?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated,
                                                      by = excluded.by""", (KEY, json.dumps(value), at, by))
        if history:
            con.execute("INSERT INTO settings_history (at, key, old, new, by) VALUES (?, ?, NULL, ?, ?)",
                        (at, KEY, json.dumps(value), by))
            con.execute("""INSERT INTO activity (at, area, kind, icon, tone, text, why)
                           VALUES (?, 'settings', 'ai.provider_chosen', 'settings', 'info', ?, ?)""",
                        (at, f"Hal will use {_label(value)} for its AI. Change it in Settings, Hal's brain.",
                         "Nobody chose an AI in setup, so Hal chose the one this computer can use, once."))
        _bump(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    from ..core.settings import store

    store.drop_cache()
    return True


def suggestion() -> str:
    """The choice setup offers: Claude on Windows; on Linux this computer when Ollama answers, else Claude."""
    from ..core import settings
    from ..core.settings import registry

    return registry.default_of(settings.setting(KEY), settings.get)


def current() -> str:
    """The stored choice; the first call with none stores the suggestion once."""
    from ..core.settings import store

    value = store.stored(KEY)
    if value:
        return value
    return choose_once()


def choose_once() -> str:
    """Stores the suggestion when no choice is stored yet, and returns the choice in force."""
    with _lock:
        con = _con()
        try:
            found = _stored(con)
            if found:
                return found
            value = suggestion()
            if _pin(con, value, BY_SETUP, history=True):
                log.info("no AI was chosen yet; stored the suggestion %r once", value)
            return _stored(con) or value
        finally:
            con.close()


def keep_stored(key: str, old: object, new: object) -> None:
    """After a change to `ai.provider`, makes sure the new choice stays stored even when it equals the default."""
    if key != KEY or not isinstance(new, str) or not new:
        return
    con = _con()
    try:
        if _stored(con) != new:
            _pin(con, new, BY_SETUP, history=False)
    finally:
        con.close()

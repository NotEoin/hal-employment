"""Stub of the migration runner: the tables the server reads, made the way the real migrations make them.

Only migrations 1 to 4 (baseline, auth, settings, actions and activity) exist here, enough for sessions,
open links, settings and Activity. The real runner adds backups, locking and 0005 to 0017.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LATEST = 4


class MigrationError(RuntimeError):
    """A migration failed."""


class DataTooNew(RuntimeError):
    """The database was saved by a newer Hal."""


@dataclass(frozen=True)
class Migration:
    """One numbered migration."""

    number: int
    name: str
    title: str
    up: Callable[[sqlite3.Connection], None]


AUTH = [
    """CREATE TABLE IF NOT EXISTS sessions (id_hash TEXT PRIMARY KEY, created TEXT NOT NULL, last_seen TEXT NOT NULL,
       expires TEXT NOT NULL, remote INTEGER NOT NULL DEFAULT 0, remote_host TEXT, browser_id TEXT NOT NULL,
       user_agent TEXT, label TEXT, ended TEXT)""",
    """CREATE TABLE IF NOT EXISTS open_tokens (hash TEXT PRIMARY KEY, created TEXT NOT NULL, expires TEXT NOT NULL,
       remote INTEGER NOT NULL DEFAULT 0, remote_host TEXT, next TEXT, used TEXT)""",
    """CREATE TABLE IF NOT EXISTS helpers (id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, browser TEXT, version TEXT,
       protocol INTEGER, extension_id TEXT, session_id TEXT, browser_id TEXT, remote INTEGER NOT NULL DEFAULT 0,
       paired TEXT NOT NULL, last_seen TEXT, label TEXT, revoked TEXT)""",
    """CREATE TABLE IF NOT EXISTS pairing_codes (hash TEXT PRIMARY KEY, session_id TEXT NOT NULL, browser TEXT,
       created TEXT NOT NULL, expires TEXT NOT NULL, used TEXT)""",
]
SETTINGS = [
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT NOT NULL,
       by TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS settings_history (id INTEGER PRIMARY KEY, at TEXT NOT NULL, key TEXT NOT NULL,
       old TEXT, new TEXT, by TEXT NOT NULL, session_id TEXT, activity_id INTEGER)""",
    """CREATE TABLE IF NOT EXISTS prompt_overrides (id TEXT PRIMARY KEY, text TEXT NOT NULL, default_sha TEXT NOT NULL,
       updated TEXT NOT NULL)""",
    "INSERT OR IGNORE INTO meta (k, v) VALUES ('settings_rev', '0')",
]
ACTIVITY = [
    """CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT, job_id TEXT,
       state TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 1, params TEXT, result TEXT, message TEXT, error TEXT,
       step_n INTEGER NOT NULL DEFAULT 0, step_total INTEGER, resumable INTEGER NOT NULL DEFAULT 0, session_id TEXT,
       helper_id TEXT, activity_id INTEGER, created TEXT NOT NULL, started TEXT, ended TEXT)""",
    """CREATE TABLE IF NOT EXISTS action_steps (id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, n INTEGER NOT NULL,
       at TEXT NOT NULL, text TEXT NOT NULL, why TEXT, used TEXT, ai TEXT, took_ms INTEGER, state TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, at TEXT NOT NULL, area TEXT NOT NULL,
       kind TEXT NOT NULL, icon TEXT, tone TEXT, text TEXT NOT NULL, why TEXT NOT NULL CHECK (length(why) > 0),
       used TEXT, ai TEXT, took_ms INTEGER, changed TEXT, undo TEXT, undone TEXT, job_id TEXT, action_id TEXT,
       session_id TEXT, remote INTEGER NOT NULL DEFAULT 0, running INTEGER NOT NULL DEFAULT 0, detail TEXT)""",
]


def _baseline(con: sqlite3.Connection) -> None:
    from jobhunt import db

    con.executescript(db.SCHEMA)
    if "contacts" not in {r[1] for r in con.execute("PRAGMA table_info(applications)")}:
        con.execute("ALTER TABLE applications ADD COLUMN contacts TEXT")


def _run(statements: list[str]) -> Callable[[sqlite3.Connection], None]:
    def up(con: sqlite3.Connection) -> None:
        for sql in statements:
            con.execute(sql)
    return up


def discover() -> list[Migration]:
    """The stub's four migrations."""
    return [Migration(1, "baseline", "Set up the records", _baseline),
            Migration(2, "auth", "Added sign-in", _run(AUTH)),
            Migration(3, "settings", "Added settings", _run(SETTINGS)),
            Migration(4, "activity", "Added Activity", _run(ACTIVITY))]


def pending(con: sqlite3.Connection) -> list[Migration]:
    """Migrations above the database's user_version."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    return [m for m in discover() if m.number > version]


def migrate(con: sqlite3.Connection, *, db_path: Path, backup_dir: Path) -> list[int]:
    """Runs pending migrations in order, each with its user_version."""
    done = []
    for m in pending(con):
        m.up(con)
        con.execute(f"PRAGMA user_version = {m.number}")
        con.commit()
        done.append(m.number)
    return done

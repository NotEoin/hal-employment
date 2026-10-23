"""Database migrations: one contiguous numbered series, with `PRAGMA user_version` as the truth.

Each migration is a module `migrations/NNNN_<name>.py` with a `TITLE` in plain words and `up(con)`.
`migrate()` runs the pending ones in order, after a backup, under a file lock shared by every process.

Design notes:
- Each migration runs in its own `BEGIN IMMEDIATE ... COMMIT`, statement by statement through
  `con.execute`, with `PRAGMA user_version = N` inside the same transaction. `executescript()` is never used,
  because it commits first and a failure would leave half a migration behind.
- Migrations are additive (CREATE, ADD COLUMN with a default, CREATE INDEX, data copies), so a newer Hal's
  records stay readable after a rollback. The one exception is 0018, which drops the old `answers` table
  once 0007 has copied it; a test reads the migration files for anything else destructive. File-system work
  belongs to data tasks (`tasks.py`).
- The lock is a file in `<data>/state/migrate.lock`, so a CLI call from the Hal tools in the middle of a
  server start waits instead of migrating twice. The user_version is read again after the lock is taken,
  because another process may have finished the work meanwhile.
- A brand-new database (no tables) is not backed up: there is nothing in it to lose.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobhunt.core import backup

log = logging.getLogger(__name__)

LATEST = 18                                # highest migration in this code; numbers are contiguous
PACKAGE = "jobhunt.core.db.migrations"
FOLDER = Path(__file__).resolve().parent / "migrations"
FILE_NAME = re.compile(r"^(?P<number>\d{4})_(?P<name>[a-z0-9_]+)\.py$")
LOCK_TIMEOUT = 60.0                        # seconds before "Another part of Hal is updating its records"
LOCK_POLL = 0.05
ACTIVITY_FROM = 4                          # the activity table exists from migration 0004
FAKE_NOW = "HAL_EMPLOYMENT_FAKE_NOW"
# Python 3.12 added sqlite3 autocommit modes; before that every connection behaves as the legacy mode.
LEGACY = getattr(sqlite3, "LEGACY_TRANSACTION_CONTROL", -1)
BUSY = "Another part of Hal is updating its records. Try again in a minute."


@dataclass(frozen=True)
class Migration:
    """One numbered step of the schema."""

    number: int                            # from the file name NNNN_<name>.py
    name: str
    title: str                             # plain words for Activity: "Added notes about job sites"
    up: Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    """A migration could not run; the message is in plain words and names the backup file."""


class DataTooNew(RuntimeError):
    """The database was saved by a newer Hal (user_version above LATEST), for example after a rollback."""


def utc_now() -> datetime:
    """The current UTC time, or HAL_EMPLOYMENT_FAKE_NOW when a test fixes it."""
    fake = os.environ.get(FAKE_NOW)
    if fake:
        return datetime.fromisoformat(fake.replace("Z", "+00:00")).astimezone(UTC)
    return datetime.now(UTC)


def iso(when: datetime | None = None) -> str:
    """UTC ISO 8601 with a Z, to the second: 2026-10-22T09:00:00Z."""
    return (when or utc_now()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_version(con: sqlite3.Connection) -> int:
    """The schema version stored in the database file."""
    return con.execute("PRAGMA user_version").fetchone()[0]


def statements(script: str) -> Iterator[str]:
    """Splits SQL into whole statements, so each runs through `con.execute` inside the open transaction."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            text = buffer.strip()
            buffer = ""
            if text.strip(";").strip():
                yield text
    if buffer.strip():
        raise ValueError(f"incomplete SQL statement: {buffer.strip()[:80]!r}")


def run_sql(con: sqlite3.Connection, script: str) -> None:
    """Runs each statement of `script` with `con.execute`, never `executescript`."""
    for statement in statements(script):
        con.execute(statement)


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    """The column names of `table`."""
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def add_column(con: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    """ALTER TABLE ... ADD COLUMN, skipped when the column is already there (an older Hal may have added it)."""
    if column not in columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def discover() -> list[Migration]:
    """Every migration file in number order; raises MigrationError when the numbers are not 1..LATEST."""
    found = []
    for path in sorted(FOLDER.glob("[0-9]*.py")):
        m = FILE_NAME.match(path.name)
        if not m:
            raise MigrationError(f"migration file {path.name} is not named NNNN_<name>.py")
        module = importlib.import_module(f"{PACKAGE}.{path.stem}")
        found.append(Migration(int(m["number"]), m["name"], module.TITLE, module.up))
    numbers = [mig.number for mig in found]
    if numbers != list(range(1, LATEST + 1)):
        raise MigrationError(f"migrations must be numbered 1 to {LATEST} with no gaps; found {numbers}")
    return found


def pending(con: sqlite3.Connection) -> list[Migration]:
    """The migrations this database has not had yet; DataTooNew when it is ahead of this code."""
    version = user_version(con)
    if version > LATEST:
        raise DataTooNew(too_new_message(version))
    return [mig for mig in discover() if mig.number > version]


def too_new_message(version: int) -> str:
    """The words for a database saved by a newer Hal."""
    return (f"This data was saved by a newer Hal (records version {version}; this Hal knows up to {LATEST}). "
            "Update Hal, or go back with Settings, Updates.")


@contextlib.contextmanager
def file_lock(path: Path, timeout: float | None = None) -> Iterator[None]:
    """Holds an exclusive lock on `path` across processes (fcntl on Linux, msvcrt on Windows)."""
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise MigrationError(BUSY) from None
                time.sleep(LOCK_POLL)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


if sys.platform == "win32":
    import msvcrt

    def _lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lock_path(db_path: Path) -> Path:
    """`<data>/state/migrate.lock` for the database at `<data>/jobs.db`."""
    return Path(db_path).parent / "state" / "migrate.lock"


def _is_empty(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0


def _run_one(con: sqlite3.Connection, mig: Migration, saved: Path | None) -> None:
    where = f" A copy of your records from before is in {saved}." if saved else ""
    con.execute("BEGIN IMMEDIATE")
    try:
        mig.up(con)
        if not con.in_transaction:
            raise MigrationError(f"migration {mig.number:04d} ended its own transaction")
        con.execute(f"PRAGMA user_version = {mig.number}")
        con.execute("COMMIT")
    except Exception as exc:
        if con.in_transaction:
            con.execute("ROLLBACK")
        log.exception("migration %04d_%s failed", mig.number, mig.name)
        raise MigrationError(f"Hal could not update its records to version {mig.number} ({mig.title}). "
                             f"Nothing from that step was kept.{where}") from exc


def _record(con: sqlite3.Connection, start: int, done: list[int], saved: Path | None) -> None:
    """One Activity row per run, once the activity table exists."""
    if not done or done[-1] < ACTIVITY_FROM:
        return
    detail = json.dumps({"from": start, "to": done[-1], "ran": done, "backup": saved.name if saved else None})
    if start == 0 and saved is None:
        text, why = "Hal set up its records", "This is a new install, so Hal made a fresh, empty set of records."
    else:
        text = f"Hal updated its records to version {done[-1]}"
        why = ("This version of Hal keeps more about your jobs and applications, so it added the new parts to "
               "your records." + (" A copy from before is in the backups folder." if saved else ""))
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO activity (at, area, kind, icon, tone, text, why, detail) "
                "VALUES (?, 'system', 'db.migrate', 'database', 'info', ?, ?, ?)", (iso(), text, why, detail))
    con.execute("COMMIT")


def migrate(con: sqlite3.Connection, *, db_path: Path, backup_dir: Path) -> list[int]:
    """Runs pending migrations in order and returns their numbers ([] when there were none).

    1. Takes the file lock state/migrate.lock (60 s, then MigrationError in plain words).
    2. Re-reads PRAGMA user_version (another process may have finished).
    3. Unless the database is brand new (no tables), writes a backup with the sqlite3 backup API to
       backups/jobs-v<from>-to-v<to>-<UTC>Z.db and checks it with PRAGMA integrity_check.
    4. Runs each migration in its own BEGIN IMMEDIATE ... COMMIT with PRAGMA user_version = N inside it.
       A failure rolls back that migration, keeps earlier ones and the backup, and raises MigrationError.
    5. Records one Activity row per run ("Hal updated its records to version 17"), once 0004 exists.
    A database already at LATEST costs one PRAGMA, so connect() may call this every time.
    """
    version = user_version(con)
    if version > LATEST:
        raise DataTooNew(too_new_message(version))
    if version == LATEST:
        return []
    if con.in_transaction:
        raise MigrationError("migrate() needs a connection with no open transaction")
    if getattr(con, "autocommit", LEGACY) != LEGACY:
        raise MigrationError("migrate() needs a connection in the default transaction mode")
    with file_lock(lock_path(db_path)):
        start = user_version(con)
        todo = pending(con)
        if not todo:
            return []
        saved = None
        if not _is_empty(con):
            try:
                saved = backup.write_copy(con, Path(backup_dir),
                                          f"jobs-v{start}-to-v{todo[-1].number}-{backup.utc_stamp()}")
            except (backup.BackupError, sqlite3.Error, OSError) as exc:
                raise MigrationError(f"Hal could not make a copy of your records before updating them, so it "
                                     f"changed nothing. ({exc})") from exc
        isolation = con.isolation_level
        con.isolation_level = None   # explicit BEGIN/COMMIT only; the module's implicit transactions are off
        done: list[int] = []
        try:
            for mig in todo:
                _run_one(con, mig, saved)
                done.append(mig.number)
            _record(con, start, done, saved)
        finally:
            con.isolation_level = isolation
        if saved:
            backup.note_last(con, saved)
            backup.prune(Path(backup_dir))
        log.info("migrated %s from version %s to %s", db_path, start, done[-1])
        return done

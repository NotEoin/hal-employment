"""Copies of the database made with SQLite's backup API, and how long they are kept.

`snapshot(con, reason)` writes `backups/jobs-<UTC>Z-<reason>.db` next to the database and checks the copy
with `PRAGMA integrity_check`; the migration runner writes `jobs-v<from>-to-v<to>-<UTC>Z.db` the same way.

Design notes:
- The backup API gives a consistent copy while other processes hold the WAL open; copying the file would not.
- A copy is written under a temporary name and renamed only after its integrity check passes, so a crash
  never leaves a half-written file that looks like a good backup.
- Retention keeps the newest 10 migration and update copies, the newest 7 daily ones, and anything newer
  than 30 days; everything else in the folder that Hal named is deleted. Files Hal did not name are left alone.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_CHANGES = 10      # migration and update copies
KEEP_DAILY = 7
KEEP_DAYS = 30
BACKUP_PAGES = 1024    # pages copied per step, so a large database does not hold the lock in one go
STAMP = "%Y%m%dT%H%M%SZ"
NAME = re.compile(r"^jobs-(?:(?P<mig>v\d+-to-v\d+)-)?(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<reason>[a-z0-9-]+))?"
                  r"(?:-\d+)?\.db$")
META_KEY = "backup:last"


class BackupError(RuntimeError):
    """A copy could not be written or did not pass its integrity check."""


def utc_stamp(now: datetime | None = None) -> str:
    """A file-name-safe UTC time: 20261022T090000Z."""
    return (now or datetime.now(UTC)).astimezone(UTC).strftime(STAMP)


def slug(reason: str) -> str:
    """A reason in lower-case words joined by hyphens: "Before update" -> "before-update"."""
    words = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")
    return words or "manual"


def database_file(con: sqlite3.Connection) -> Path:
    """The file behind a connection's main database; BackupError for an in-memory database."""
    row = next((r for r in con.execute("PRAGMA database_list") if r[1] == "main"), None)
    if row is None or not row[2]:
        raise BackupError("This database has no file, so it cannot be backed up.")
    return Path(row[2])


def _free_name(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}.db"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{stem}-{n}.db"
    return path


def write_copy(con: sqlite3.Connection, folder: Path, stem: str) -> Path:
    """Copies the database to `folder/<stem>.db` with the backup API, checks it, and returns its path."""
    folder.mkdir(parents=True, exist_ok=True)
    final = _free_name(folder, stem)
    partial = final.with_suffix(".partial")
    dest = sqlite3.connect(partial)
    try:
        con.backup(dest, pages=BACKUP_PAGES)
        result = dest.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dest.close()
    if result != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"The backup copy failed its check ({result}); the original was not changed.")
    partial.replace(final)
    return final


def note_last(con: sqlite3.Connection, path: Path) -> None:
    """Records the newest backup in `meta`, when the database has that table and no transaction is open."""
    has_meta = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
    if not has_meta or con.in_transaction:
        return
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (META_KEY, path.name))
    con.commit()


def snapshot(con: sqlite3.Connection, reason: str, *, folder: Path | None = None) -> Path:
    """Writes `backups/jobs-<UTC>Z-<reason slug>.db` beside the database, prunes old copies, returns the path."""
    folder = folder or database_file(con).parent / "backups"
    path = write_copy(con, folder, f"jobs-{utc_stamp()}-{slug(reason)}")
    note_last(con, path)
    prune(folder)
    return path


def _kind(m: re.Match) -> str:
    words = (m["reason"] or "").split("-")
    if m["mig"] or "update" in words:
        return "change"
    if "daily" in words:
        return "daily"
    return "other"


def prune(folder: Path, *, now: datetime | None = None) -> list[Path]:
    """Deletes copies the retention rule no longer keeps; returns what it deleted."""
    now = now or datetime.now(UTC)
    named = []
    for path in folder.glob("jobs-*.db"):
        m = NAME.match(path.name)
        if m:
            named.append((datetime.strptime(m["stamp"], STAMP).replace(tzinfo=UTC), _kind(m), path))
    named.sort(key=lambda item: (item[0], item[2].name), reverse=True)
    keep = {p for at, _, p in named if now - at < timedelta(days=KEEP_DAYS)}
    for kind, count in (("change", KEEP_CHANGES), ("daily", KEEP_DAILY)):
        keep.update([p for _, k, p in named if k == kind][:count])
    deleted = []
    for _, _, path in named:
        if path not in keep:
            path.unlink(missing_ok=True)
            deleted.append(path)
            log.info("deleted old backup %s", path.name)
    return deleted

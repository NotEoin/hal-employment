"""SQLite tracker: every job ever seen, its score, and its application history.

`connect()` finds the database through `core.paths` when it is called, never at import, so tests, the
importer and worktrees each open their own file. The schema is made and upgraded only by the migration runner
(`core.db.migrate`); migration 0001 holds the tables this module used to create itself.
`DB_PATH` is kept as a live alias of `paths.db_path()` for code and tests that read it.
"""
import json
import sqlite3
from pathlib import Path

from .core import clock, paths
from .core.db import migrate

BUSY_TIMEOUT_S = 30  # the web server, its worker and the CLI share the file; each waits out the others' writes


def __getattr__(name):  # old constant, read live (PEP 562)
    if name == "DB_PATH":
        return paths.db_path()
    raise AttributeError(name)

# Pipeline order. Anything from 'applied' on is an application in flight.
STATUSES = [
    "new",          # discovered, not looked at
    "shortlisted",  # worth applying, pack to be drafted
    "ready",        # pack drafted, waiting in the queue
    "applying",     # chosen from the queue; documents and form under review
    "applied",
    "assessment",   # online test / take-home
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "skipped",      # reviewed and rejected
    "closed",       # posting removed before an application was made
]


def now() -> str:
    """Now in UTC as `2026-10-22T09:00:00Z` (older rows end in `+00:00`; both compare on 19 characters)."""
    return clock.iso()


def json_text(value) -> str:
    return json.dumps(value)


def json_value(text: str):
    return json.loads(text)


def _upgrade(con: sqlite3.Connection, path: Path) -> None:
    """Runs pending migrations; a database saved by a newer Hal raises `DataTooNew`."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > migrate.LATEST:
        raise migrate.DataTooNew(
            "This data was saved by a newer Hal. Update Hal, or go back with Settings, Updates.")
    if version < migrate.LATEST:
        # data/backups for the real database (paths.backups_dir()); beside the file for one opened by path
        migrate.migrate(con, db_path=path, backup_dir=path.parent / "backups")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Opens the database (default `paths.db_path()`) with WAL and foreign keys, migrated to this code."""
    path = Path(path) if path is not None else paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S, check_same_thread=False)
    con.execute("PRAGMA journal_mode = WAL")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    _upgrade(con, path)
    return con


def upsert(con: sqlite3.Connection, job: dict) -> bool:
    """Insert or refresh a job. Returns True if it was new."""
    ts = now()
    existing = con.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    if existing:
        con.execute(
            """UPDATE jobs SET title=:title, location=:location, url=:url,
                   description=:description, salary=:salary, posted_at=:posted_at,
                   board_size=:board_size, last_seen=:ts, active=1
               WHERE id=:id""",
            {**job, "ts": ts},
        )
        return False
    con.execute(
        """INSERT INTO jobs (id, source, company, title, location, url, description,
                             salary, posted_at, board_size, first_seen, last_seen)
           VALUES (:id, :source, :company, :title, :location, :url, :description,
                   :salary, :posted_at, :board_size, :ts, :ts)""",
        {**job, "ts": ts},
    )
    return True


def mark_gone(con: sqlite3.Connection, source: str, company: str, seen_ids: set[str]) -> int:
    """Postings missing from a board we just fetched in full are no longer open."""
    rows = con.execute(
        "SELECT id, status FROM jobs WHERE source=? AND company=? AND active=1",
        (source, company),
    ).fetchall()
    gone = [r for r in rows if r["id"] not in seen_ids]
    for r in gone:
        con.execute("UPDATE jobs SET active=0 WHERE id=?", (r["id"],))
        if r["status"] in ("new", "shortlisted", "ready"):
            set_status(con, r["id"], "closed", "posting removed from board")
        elif r["status"] == "applying":
            alert(con, "urgent", "Posting removed mid-application",
                  "The advert has gone from the company's board; submit soon or check it is still open.",
                  r["id"])
    return len(gone)


def set_status(con: sqlite3.Connection, job_id: str, status: str, detail: str = "", at: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; one of {', '.join(STATUSES)}")
    con.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    log(con, job_id, "status", f"{status}: {detail}" if detail else status, at)


def log(con: sqlite3.Connection, job_id: str, kind: str, detail: str, at: str | None = None) -> None:
    """`at` lets email-driven events carry the email's date rather than the sync time."""
    con.execute(
        "INSERT INTO events (job_id, at, kind, detail) VALUES (?, ?, ?, ?)",
        (job_id, at or now(), kind, detail),
    )


def alert(con: sqlite3.Connection, level: str, title: str, body: str = "",
          job_id: str | None = None) -> int:
    """Records an alert for the web interface and returns its id."""
    cur = con.execute("INSERT INTO alerts (at, level, title, body, job_id) VALUES (?, ?, ?, ?, ?)",
                      (now(), level, title, body, job_id))
    return cur.lastrowid


def add_todo(con: sqlite3.Connection, kind: str, title: str, *, job_id: str | None = None,
             detail: str = "", url: str | None = None, priority: int = 5, due: str | None = None,
             source: str | None = None) -> int | None:
    """Adds a to-do item; returns its id, or None if `source` already made one."""
    cur = con.execute(
        """INSERT OR IGNORE INTO todos (job_id, kind, title, detail, url, priority, due, source, created)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, kind, title, detail, url, priority, due, source, now()))
    return cur.lastrowid if cur.rowcount else None

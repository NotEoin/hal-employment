"""SQLite tracker: every job ever seen, its score, and its application history."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

# Pipeline order. Anything from 'applied' on is an application in flight.
STATUSES = [
    "new",          # discovered, not looked at
    "shortlisted",  # worth applying, pack to be drafted
    "ready",        # pack drafted, waiting to be submitted
    "applied",
    "assessment",   # online test / take-home
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "skipped",      # reviewed and rejected
    "closed",       # posting removed before an application was made
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,          -- '<source>:<external id>'
    source       TEXT NOT NULL,
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    location     TEXT,
    url          TEXT,
    description  TEXT,
    salary       TEXT,
    posted_at    TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    board_size   INTEGER,                   -- open roles on that company's board (size proxy)
    score        REAL,
    score_notes  TEXT,
    status       TEXT NOT NULL DEFAULT 'new',
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, score);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    job_id  TEXT NOT NULL REFERENCES jobs(id),
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,                  -- status | note | email | ...
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, at);

CREATE TABLE IF NOT EXISTS emails (
    msgid    TEXT PRIMARY KEY,              -- Gmail X-GM-MSGID
    thrid    TEXT,
    date     TEXT NOT NULL,                 -- UTC ISO
    sender   TEXT,
    rcpt     TEXT,
    subject  TEXT,
    kind     TEXT NOT NULL,                 -- see gmail.KINDS
    company  TEXT,
    role     TEXT,
    job_id   TEXT REFERENCES jobs(id),
    snippet  TEXT
);
CREATE INDEX IF NOT EXISTS emails_job ON emails(job_id, date);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
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

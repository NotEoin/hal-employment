"""Databases in the shapes today's Hal leaves behind, filled with made-up rows, for migration tests.

`build(path, layout)` writes a database as the Linux repo at ef53088 makes it (`"L"`, with
`applications.contacts`) or as an older copy of the same code on the Windows stick leaves it (`"U"`, from
before that column was added). Both have `user_version` 0, as every database made before the rebuild does.

Everything here is invented: the people, the employers, the answers. The 22 saved answers include made-up
disability, adjustments, interview-scheme and carer rows, so the tests can prove those are never copied.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The old database's tables, written out here so the fixture never changes when the code does.
_TABLES = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, company TEXT NOT NULL, title TEXT NOT NULL, location TEXT,
    url TEXT, description TEXT, salary TEXT, posted_at TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, board_size INTEGER, score REAL, score_notes TEXT,
    status TEXT NOT NULL DEFAULT 'new', notes TEXT);
CREATE INDEX jobs_status ON jobs(status, score);
CREATE TABLE events (
    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), at TEXT NOT NULL, kind TEXT NOT NULL,
    detail TEXT);
CREATE INDEX events_job ON events(job_id, at);
CREATE TABLE emails (
    msgid TEXT PRIMARY KEY, thrid TEXT, date TEXT NOT NULL, sender TEXT, rcpt TEXT, subject TEXT,
    kind TEXT NOT NULL, company TEXT, role TEXT, job_id TEXT REFERENCES jobs(id), snippet TEXT);
CREATE INDEX emails_job ON emails(job_id, date);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), stage TEXT NOT NULL, docs TEXT, form TEXT, prep TEXT,
    {contacts}updated TEXT NOT NULL);
CREATE TABLE answers (
    question TEXT PRIMARY KEY, label TEXT NOT NULL, answer TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE todos (
    id INTEGER PRIMARY KEY, job_id TEXT REFERENCES jobs(id), kind TEXT NOT NULL, title TEXT NOT NULL,
    detail TEXT, url TEXT, priority INTEGER NOT NULL DEFAULT 5, due TEXT, source TEXT UNIQUE,
    created TEXT NOT NULL, done TEXT);
CREATE INDEX todos_open ON todos(done, priority);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, level TEXT NOT NULL, title TEXT NOT NULL, body TEXT,
    job_id TEXT REFERENCES jobs(id), read INTEGER NOT NULL DEFAULT 0);
"""

TABLES = ("jobs", "events", "emails", "meta", "applications", "answers", "todos", "alerts")

# Saved answers as today's `answers` table holds them: (label, answer). Four are private questions.
ORDINARY_ANSWERS = (
    ("When can you start?", "Straight away"),
    ("What is your notice period?", "None, available immediately"),
    ("Are you willing to relocate?", "Yes"),
    ("Are you happy to work in the office three days a week?", "Yes"),
    ("What are your salary expectations?", "28,000 GBP"),
    ("How did you hear about this role?", "Company careers page"),
    ("Why do you want to work for the employer?", "I like the way the team publishes its research openly."),
    ("Do you have the right to work in the UK?", "Yes"),
    ("Will you now or in the future require sponsorship?", "No"),
    ("What is your highest level of education?", "Bachelor's degree"),
    ("Do you hold a full UK driving licence?", "No"),
    ("Please provide a link to your portfolio", "https://example.org/portfolio"),
    ("Have you worked for the employer before?", "No"),
    ("Preferred pronouns", "they/them"),
    ("Which office would you prefer?", "Leeds"),
    ("Are you able to attend an assessment day in person?", "Yes"),
    ("What programming languages do you know?", "Python, SQL"),
    ("Tell us about a project you are proud of", "A made-up timetable planner for a made-up school club."),
)
PRIVATE_ANSWERS = (
    ("Do you consider yourself to have a disability?", "SYNTH-PRIVATE-7731"),
    ("Do you require any reasonable adjustments for the interview?", "SYNTH-PRIVATE-7732"),
    ("Would you like to apply under the Disability Confident guaranteed interview scheme?", "Yes"),
    ("Do you look after, or give any help or support to, someone because of their health?", "No"),
)

# (job id, company, title, status, hours before now the application was started, or None for no event,
#  hours before now the application row was last updated)
APPLICATIONS = (
    ("greenhouse:aldermoor-1", "Aldermoor Trust", "Graduate Analyst", "applied", 300, 10),
    ("lever:birchwell-2", "Birchwell Studio", "Junior Developer", "interview", 500, 2),
    ("workable:cindervale-3", "Cindervale Ltd", "Research Assistant", "applying", 40, 1),
    ("greenhouse:dunmere-4", "Dunmere Group", "Graduate Engineer", "applying", None, 120),
)
OTHER_JOBS = (
    ("greenhouse:elmscote-5", "Elmscote Partners", "Graduate Data Analyst", "new"),
    ("lever:fallowfield-6", "Fallowfield Media", "Trainee Archivist", "shortlisted"),
    ("workable:greystone-7", "Greystone Labs", "Software Placement", "skipped"),
)
# Application numbers by job id, in the order the applications were started.
EXPECTED_NUMBERS = {"lever:birchwell-2": 1, "greenhouse:aldermoor-1": 2, "greenhouse:dunmere-4": 3,
                    "workable:cindervale-3": 4}


def _iso(now: datetime, hours: float) -> str:
    """Today's Hal writes +00:00 timestamps."""
    return (now - timedelta(hours=hours)).isoformat(timespec="seconds")


def build(path: Path, layout: str = "L", *, now: datetime | None = None) -> Path:
    """Writes a made-up database in the L or U shape at `path` and returns the path."""
    if layout not in ("L", "U"):
        raise ValueError(f"layout is 'L' or 'U', not {layout!r}")
    now = now or datetime.now(UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(_TABLES.format(contacts="contacts TEXT, " if layout == "L" else ""))
        _fill(con, now, layout)
        con.commit()
    finally:
        con.close()
    return path


def _fill(con: sqlite3.Connection, now: datetime, layout: str) -> None:
    jobs = [(j, c, t, s) for j, c, t, s, _, _ in APPLICATIONS] + list(OTHER_JOBS)
    for n, (job_id, company, title, status) in enumerate(jobs):
        seen = _iso(now, 1000 - n)
        con.execute("INSERT INTO jobs (id, source, company, title, location, url, description, first_seen, "
                    "last_seen, score, score_notes, status) VALUES (?, ?, ?, ?, 'Leeds', ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, job_id.split(":")[0], company, title, f"https://jobs.example.org/{n}",
                     f"A made-up advert for a {title.lower()}.", seen, seen, 60 + n, "entry-level title", status))
        con.execute("INSERT INTO events (job_id, at, kind, detail) VALUES (?, ?, 'status', 'new')", (job_id, seen))
    for job_id, _, _, status, started, updated in APPLICATIONS:
        if started is not None:
            con.execute("INSERT INTO events (job_id, at, kind, detail) VALUES (?, ?, 'status', ?)",
                        (job_id, _iso(now, started), "applying: chosen from the queue"))
        docs = json.dumps({"letter": "Dear hiring team, a made-up letter.", "message": "Hello."})
        columns = "job_id, stage, docs, updated" + (", contacts" if layout == "L" else "")
        values = [job_id, "documents" if status == "applying" else "submitted", docs, _iso(now, updated)]
        if layout == "L":
            values.append(json.dumps([]))
        con.execute(f"INSERT INTO applications ({columns}) VALUES ({', '.join('?' * len(values))})", values)
    for n, (label, answer) in enumerate(ORDINARY_ANSWERS + PRIVATE_ANSWERS):
        question = "".join(ch for ch in label.lower() if ch.isalnum() or ch == " ").strip()
        con.execute("INSERT INTO answers (question, label, answer, updated) VALUES (?, ?, ?, ?)",
                    (question, label, json.dumps(answer), _iso(now, 200 + n)))
    emails = (("m1", "interview", 1), ("m2", "interview", 200), ("m3", "rejected", 2), ("m4", "alert", 5),
              ("m5", "offer", 30), ("m6", "other", 600))
    for msgid, kind, hours in emails:
        con.execute("INSERT INTO emails (msgid, date, sender, subject, kind, snippet) VALUES (?, ?, ?, ?, ?, ?)",
                    (msgid, _iso(now, hours), "recruiting@example.org", f"A made-up {kind} email", kind, "..."))
    for n, hours in enumerate((2, 30, 24 * 8, 24 * 40)):
        con.execute("INSERT INTO alerts (at, level, title) VALUES (?, 'info', ?)", (_iso(now, hours), f"Alert {n}"))
    con.execute("INSERT INTO todos (kind, title, created, source) VALUES ('task', 'Book a made-up test', ?, 'x:1')",
                (_iso(now, 3),))
    con.execute("INSERT INTO meta (k, v) VALUES ('last_discover', ?)", (_iso(now, 5),))


def counts(path_or_con: Path | sqlite3.Connection, tables: list[str] | None = None) -> dict[str, int]:
    """Row counts of today's tables, or of the ones named."""
    con = sqlite3.connect(path_or_con) if isinstance(path_or_con, Path) else path_or_con
    try:
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables or TABLES}
    finally:
        if isinstance(path_or_con, Path):
            con.close()

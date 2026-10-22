"""Migration 0001: today's schema (L db.py:25-115), plus `applications.contacts` (L db.py:138-142).

On a database made by today's Hal (tables present, user_version 0) it adds only what is missing, so the
owner's database and the stick's both reach version 1 unchanged. The statements are a frozen copy: later
changes to `jobhunt/db.py` must never change what version 1 means.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Recognised your existing records"

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

CREATE TABLE IF NOT EXISTS applications (
    job_id   TEXT PRIMARY KEY REFERENCES jobs(id),
    stage    TEXT NOT NULL,                 -- documents | form | filled | submitted
    docs     TEXT,                          -- JSON: letter, message, answers, cv title and summary
    form     TEXT,                          -- JSON: the form fields and the values to enter
    prep     TEXT,                          -- interview preparation notes, Markdown
    contacts TEXT,                          -- JSON: who to message about the application (contacts.find)
    updated  TEXT NOT NULL
);

-- Answers the applicant typed into a form himself, reused when another form asks the same.
CREATE TABLE IF NOT EXISTS answers (
    question TEXT PRIMARY KEY,              -- the form's label, lower-cased without punctuation
    label    TEXT NOT NULL,                 -- the label as the form worded it
    answer   TEXT NOT NULL,                 -- JSON: a string, a list of options, or true/false
    updated  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id        INTEGER PRIMARY KEY,
    job_id    TEXT REFERENCES jobs(id),
    kind      TEXT NOT NULL,                -- offer | interview | assessment | reply | apply | message | task
    title     TEXT NOT NULL,
    detail    TEXT,
    url       TEXT,
    priority  INTEGER NOT NULL DEFAULT 5,   -- 0 is most urgent
    due       TEXT,
    source    TEXT UNIQUE,                  -- what created it, e.g. 'email:<msgid>'
    created   TEXT NOT NULL,
    done      TEXT
);
CREATE INDEX IF NOT EXISTS todos_open ON todos(done, priority);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL,
    level   TEXT NOT NULL,                  -- info | good | urgent | error
    title   TEXT NOT NULL,
    body    TEXT,
    job_id  TEXT REFERENCES jobs(id),
    read    INTEGER NOT NULL DEFAULT 0
);
"""


def up(con):
    """Creates the original tables where missing and adds the contacts column to older databases."""
    run_sql(con, SCHEMA)
    add_column(con, "applications", "contacts", "TEXT")

"""Migration 0016: interviews (Interview step), and examples."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added interviews and examples"

SCHEMA = """
CREATE TABLE interviews (
  job_id TEXT NOT NULL REFERENCES jobs(id), at TEXT NOT NULL, place TEXT, detail TEXT,
  email_msgid TEXT, questions TEXT,    -- JSON list
  PRIMARY KEY (job_id, at));
CREATE TABLE examples (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, situation TEXT, action TEXT, result TEXT,
  sources TEXT, on_ INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
"""


def up(con):
    """Added interviews and examples."""
    run_sql(con, SCHEMA)

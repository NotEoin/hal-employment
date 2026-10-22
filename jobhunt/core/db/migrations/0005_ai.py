"""Migration 0005: ai."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added a record of AI use"

SCHEMA = """
CREATE TABLE ai_usage (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, role TEXT,
  priority INTEGER NOT NULL, purpose TEXT NOT NULL,     -- prompt id
  ok INTEGER NOT NULL, error_kind TEXT,                 -- signed_out | limit | free_plan | timeout | cap | ...
  limited_until TEXT, ms INTEGER, chars_in INTEGER, chars_out INTEGER,   -- sizes only, never prompt text
  action_id TEXT, job_id TEXT);
CREATE INDEX ai_usage_day ON ai_usage(at, priority);
"""


def up(con):
    """Added a record of AI use."""
    run_sql(con, SCHEMA)

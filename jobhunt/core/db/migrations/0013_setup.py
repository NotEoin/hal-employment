"""Migration 0013: setup."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added setup steps"

SCHEMA = """
CREATE TABLE setup_steps (id TEXT PRIMARY KEY, state TEXT NOT NULL,   -- todo | done | skipped
  updated TEXT NOT NULL, data TEXT);
"""


def up(con):
    """Added setup steps."""
    run_sql(con, SCHEMA)

"""Migration 0017: imports."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added a record of imports"

SCHEMA = """
CREATE TABLE imports (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,   -- owner | usb | cv
  source TEXT NOT NULL, report TEXT NOT NULL);                    -- JSON counts and hashes, no content
"""


def up(con):
    """Added a record of imports."""
    run_sql(con, SCHEMA)

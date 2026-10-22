"""Migration 0008: schedule."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added the schedule's state"

SCHEMA = """
CREATE TABLE schedule_state (
  job TEXT PRIMARY KEY, last_run TEXT, last_ok TEXT, state TEXT, detail TEXT, next_run TEXT,
  action_id TEXT);
"""


def up(con):
    """Added the schedule's state."""
    run_sql(con, SCHEMA)

"""Migration 0012: sources."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added the list of job sites Hal searches"

SCHEMA = """
CREATE TABLE sources (
  id TEXT PRIMARY KEY,                 -- adzuna, reed, jobsacuk, ats:greenhouse:<slug>, page:<sha1>, rss:<sha1>, alerts
  kind TEXT NOT NULL,                  -- ats_board | aggregator | board | careers_page | company_watch |
                                       -- rss | email_alerts | ai_page
  name TEXT NOT NULL, area TEXT, config TEXT NOT NULL DEFAULT '{}',   -- JSON: slug, url, queries, places, kinds
  enabled INTEGER NOT NULL DEFAULT 1, builtin TEXT,                    -- seed id for built-ins
  needs_secret TEXT, added_by TEXT NOT NULL,                           -- builtin | you | import
  created TEXT NOT NULL, last_run TEXT, last_ok TEXT, last_error TEXT,
  seen INTEGER NOT NULL DEFAULT 0, kept INTEGER NOT NULL DEFAULT 0, board_size INTEGER);
CREATE TABLE source_runs (
  id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, started TEXT NOT NULL, ended TEXT,
  ok INTEGER, seen INTEGER, new INTEGER, kept INTEGER, error TEXT);
CREATE INDEX source_runs_source ON source_runs(source_id, started);
"""


def up(con):
    """Added the list of job sites Hal searches."""
    run_sql(con, SCHEMA)

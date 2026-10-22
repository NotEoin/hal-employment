"""Migration 0003: settings."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added settings with their history"

SCHEMA = """
CREATE TABLE settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL,            -- JSON
  updated TEXT NOT NULL, by TEXT NOT NULL);             -- you | setup | import | learned | hal
CREATE TABLE settings_history (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, key TEXT NOT NULL,
  old TEXT, new TEXT,                                   -- JSON; "(saved)" for secrets and private answers
  by TEXT NOT NULL, session_id TEXT, activity_id INTEGER);
CREATE INDEX settings_history_key ON settings_history(key, at);
CREATE TABLE prompt_overrides (
  id TEXT PRIMARY KEY, text TEXT NOT NULL,
  default_sha TEXT NOT NULL,                            -- the default it was edited from ("Hal's original changed")
  updated TEXT NOT NULL);
INSERT OR IGNORE INTO meta (k, v) VALUES ('settings_rev', '0');
"""


def up(con):
    """Added settings with their history."""
    run_sql(con, SCHEMA)

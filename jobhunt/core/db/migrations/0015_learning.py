"""Migration 0015: learning."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added what Hal learns"

SCHEMA = """
CREATE TABLE learned_items (
  id TEXT PRIMARY KEY,
  area TEXT NOT NULL,                  -- finding | letters | writing | cv | sites | results
  kind TEXT NOT NULL,                  -- suggestion | learned
  title TEXT NOT NULL, why TEXT NOT NULL, effect TEXT, evidence TEXT NOT NULL DEFAULT '[]',
  change TEXT,                         -- JSON {"setting": key, "value": v} | {"note": {...}} | {"cv_lock": target}
                                       -- | {"answer_scope": {...}} | {"voice_example": id}
  fingerprint TEXT NOT NULL UNIQUE,    -- so "Never suggest this" sticks
  status TEXT NOT NULL,                -- waiting | yes | later | never | active | off
  source TEXT NOT NULL, created TEXT NOT NULL, decided TEXT, ask_again TEXT);
"""


def up(con):
    """Added what Hal learns."""
    run_sql(con, SCHEMA)

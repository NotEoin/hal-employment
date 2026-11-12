"""Migration 0030: projects as sources of the record: sources inside sources, what the person said, and revisions.

- `record_sources.parent_id` is the source another was found in: each repo of a GitHub account, each project
  in a folder of projects, each piece of a portfolio and each folder of a split code project is its own
  source under it, so a star and a removal apply to one project. Removing the parent removes them too.
- `record_sources.meta` is a JSON object: what the person said of the source (`theirs`, a project whose
  commits are none of theirs; `picked`, the pieces they picked from a portfolio; a hand-made project's `form`).
  What a read found (tools, commit counts, dates, a hash) is in `details` (migration 0029), which the refresh
  compares.
- `record_sources.what_changed` is a short note on what the last read changed (tools added or removed, commits
  since, dates), shown on the project.
- `record_source_revisions` keeps the facts each of a source's last reads gave, so "Go back to the last read"
  can put the earlier ones back.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added projects inside your GitHub account or folders, what changed in each, and going back a read"

SCHEMA = """
CREATE TABLE record_source_revisions (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES record_sources(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL, facts TEXT NOT NULL DEFAULT '[]', details TEXT NOT NULL DEFAULT '{}',
  what_changed TEXT NOT NULL DEFAULT '', read_at TEXT NOT NULL,
  UNIQUE (source_id, revision));
CREATE INDEX record_sources_parent ON record_sources (parent_id);
"""


def up(con):
    """Adds sources inside sources, a source's details and what changed, and the facts of each read."""
    add_column(con, "record_sources", "parent_id", "INTEGER REFERENCES record_sources(id) ON DELETE CASCADE")
    add_column(con, "record_sources", "meta", "TEXT NOT NULL DEFAULT '{}'")
    add_column(con, "record_sources", "what_changed", "TEXT NOT NULL DEFAULT ''")
    run_sql(con, SCHEMA)

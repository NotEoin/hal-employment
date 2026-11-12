"""Migration 0019: the bulk ingest's items, and an action's progress in items as well as steps.

`ingest_items` holds one row per file or unit an ingest found, with the stage it reached, so an ingest
stopped by a restart carries on from the next item rather than starting again. `actions.done_n` and
`actions.done_total` count items (files read, pages fetched), which a long action reports beside its steps
so the page can show a bar and the time left.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added the items of a bulk read into your record"

SCHEMA = """
CREATE TABLE ingest_items (
  id INTEGER PRIMARY KEY,
  batch TEXT NOT NULL,                 -- one ingest, kept when it carries on after a restart
  path TEXT NOT NULL,                  -- the file, or a unit's folder, on this computer (or a URL)
  rel TEXT NOT NULL,                   -- the path inside the folder or zip it came from, with "/"
  unit TEXT,                           -- the unit this file belongs to (a repository or course folder)
  kind TEXT NOT NULL,                  -- file | repo | course | page
  size INTEGER, sha256 TEXT, mtime TEXT,
  magic TEXT,                          -- the kind of file by its first bytes: pdf, docx, zip, png ...
  rank INTEGER, category TEXT, doc_type TEXT,
  stage TEXT NOT NULL,                 -- discovered | converted | understood | written
  state TEXT NOT NULL,                 -- pending | done | duplicate | not_downloaded | no_text | skipped | error
  dup_of INTEGER REFERENCES ingest_items(id),
  note TEXT,                           -- the note written, as a path inside the record
  error TEXT,
  model INTEGER NOT NULL DEFAULT 0,    -- 1 when the item was read by the AI
  created TEXT NOT NULL, updated TEXT NOT NULL,
  UNIQUE (batch, path));
CREATE INDEX ingest_items_batch ON ingest_items(batch, state);
CREATE INDEX ingest_items_sha ON ingest_items(sha256);
"""


def up(con):
    """Adds `ingest_items` and the two progress columns of `actions`."""
    run_sql(con, SCHEMA)
    add_column(con, "actions", "done_n", "INTEGER")
    add_column(con, "actions", "done_total", "INTEGER")

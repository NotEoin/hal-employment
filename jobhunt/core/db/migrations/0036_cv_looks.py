"""Migration 0036: personal looks, made from the layout of a person's own CV file.

- `cv_looks` holds one look each: its `name` (the person can change it), its `measures` (the JSON of
  `cvstudio.measures.Measures`, each value checked before it is saved), `notes` (a JSON list of plain
  sentences saying what Hal could only approximate, such as a second column), `steps` (how many "Make it fit"
  steps were taken, at most `measures.TIGHTEN_STEPS`) and `file_id`, the CV file it was measured from (NULL
  once that file is gone; the look stays usable).
- Built-in looks stay files under `cvstudio/looks/`; nothing personal is written to the code folder.
"""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added your own looks, matched from the layout of your CV file"

SCHEMA = """
CREATE TABLE cv_looks (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  measures TEXT NOT NULL,                   -- JSON of cvstudio.measures.Measures
  notes TEXT NOT NULL DEFAULT '[]',         -- JSON list of what Hal approximated
  steps INTEGER NOT NULL DEFAULT 0,         -- "Make it fit" steps taken
  file_id TEXT,                             -- the cv_files row it was measured from
  created TEXT NOT NULL, updated TEXT NOT NULL);
"""


def up(con):
    """Adds the personal looks table."""
    run_sql(con, SCHEMA)

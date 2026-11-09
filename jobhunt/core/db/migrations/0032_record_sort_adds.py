"""Migration 0032: each add from a sorted folder, so its reading can be stopped and the whole add undone.

- `record_sort_adds` holds one press of an add button in the review: the sort, the `record.sort_accept` action
  reading it (`action_id`), how many files it took (`files`), when (`created`), and `undone` once the person
  undid it. Whether it is reading, done or stopped is the action's state, never copied here.
- `record_sort_rows.add_id` names the add that took a row, so "Undo this add" finds every source it made and
  "Stop reading" puts back the rows it had not reached.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added stopping and undoing an add from a sorted folder"

SCHEMA = """
CREATE TABLE record_sort_adds (
  id INTEGER PRIMARY KEY,
  sort_id INTEGER NOT NULL REFERENCES record_sorts(id) ON DELETE CASCADE,
  action_id TEXT,
  files INTEGER NOT NULL DEFAULT 0,
  undone INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL);
CREATE INDEX record_sort_adds_sort ON record_sort_adds (sort_id);
"""


def up(con):
    """Adds the adds of a sort, and the add that took each row."""
    run_sql(con, SCHEMA)
    add_column(con, "record_sort_rows", "add_id", "INTEGER REFERENCES record_sort_adds(id) ON DELETE SET NULL")

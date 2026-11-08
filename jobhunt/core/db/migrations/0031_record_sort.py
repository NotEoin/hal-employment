"""Migration 0031: "Sort a big folder for me" and "Move my record to sections": proposals the person reviews.

- `record_sorts` holds one sort each: `kind` is folder (on this computer), upload (files or a zip sent from
  the page, unpacked into Hal's data folder), inbox (the inbox folder Hal watches) or move (the notes of a
  record an older Hal built). `roots` is a JSON list of the folders walked, `label` the words the page shows,
  `state` new | sorting | ready | failed | undone, and `skipped` a JSON object of what the walk left out on
  purpose (junk folders, programs) by reason, with counts.
- `record_sort_rows` holds one proposal each: a file or project folder (`path`, `rel` inside the folder
  given) with the section, kind of document, card (`target_id`) and showcase guess (`star`) Hal proposes, who
  proposed it (`by`: rules, ai or you), and `why`, in plain words. `state` is proposed | left_out | copy |
  accepted | added | failed: only an accepted row becomes a source (`source_id`), so nothing is a fact before
  the person accepts it. `ticked` is the person's tick. `copy_of` names the newest copy, proposed instead.
  `note_rel` is the older Hal's note a move row came from.
- `record_sort_kept_out` keeps what a move set aside, so one click undoes it: `what` is `evidence` (a row
  of `profile_evidence` from a note of the older record, as JSON) or `card` (a card's state before the move).
"""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added sorting a big folder into your record, and moving a record from an older Hal"

SCHEMA = """
CREATE TABLE record_sorts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                       -- folder | upload | inbox | move
  roots TEXT NOT NULL DEFAULT '[]', label TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'new',        -- new | sorting | ready | failed | undone
  error TEXT NOT NULL DEFAULT '', skipped TEXT NOT NULL DEFAULT '{}',
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE record_sort_rows (
  id INTEGER PRIMARY KEY,
  sort_id INTEGER NOT NULL REFERENCES record_sorts(id) ON DELETE CASCADE,
  path TEXT NOT NULL, rel TEXT NOT NULL,
  held TEXT NOT NULL,                       -- upload | files | folder (`sections.registry.HELD`)
  size INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '', mtime TEXT,
  section TEXT NOT NULL DEFAULT '', doc_kind TEXT NOT NULL DEFAULT '',
  target_id INTEGER REFERENCES profile_items(id) ON DELETE SET NULL,
  star INTEGER NOT NULL DEFAULT 0, by TEXT NOT NULL DEFAULT 'rules', why TEXT NOT NULL DEFAULT '',
  ticked INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'proposed',   -- proposed | left_out | copy | accepted | added | failed
  copy_of TEXT NOT NULL DEFAULT '', note_rel TEXT,
  source_id INTEGER REFERENCES record_sources(id) ON DELETE SET NULL,
  UNIQUE (sort_id, path));
CREATE INDEX record_sort_rows_sort ON record_sort_rows (sort_id, state, section);
CREATE TABLE record_sort_kept_out (
  id INTEGER PRIMARY KEY,
  sort_id INTEGER NOT NULL REFERENCES record_sorts(id) ON DELETE CASCADE,
  what TEXT NOT NULL,                       -- evidence | card
  row TEXT NOT NULL);
CREATE INDEX record_sort_kept_out_sort ON record_sort_kept_out (sort_id, what);
"""


def up(con):
    """Adds the sorts, their proposals, and what a move set aside."""
    run_sql(con, SCHEMA)

"""Migration 0020: the profile, the facts about the person that Hal finds in their record or they add.

- `profile_items` holds one fact each (a job, a qualification, a skill and so on) as a JSON object of
  fields, with its state: `hal` (found, not checked), `confirmed`, `edited` or `hidden`, and its origin:
  `found` (from the record) or `you` (added by hand, the only kind that can be deleted).
- `profile_evidence` says where each fact came from: the file inside the record, the quote, the field the
  quote supports, the document type (which sets the source's rank) and what that source said (`said`, a
  JSON object), so a conflict can name each source's value, and whether the quote was found in the file
  (`verified`).
- `profile_conflicts` holds what does not add up. `key` is the conflict's fingerprint, so finding the same
  thing again never adds a second row and a resolved conflict stays resolved.

It uses no table of 0019, so the two can land in either order.
"""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added your profile"

SCHEMA = """
CREATE TABLE profile_items (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL,          -- job | education | skill | project | ...
  fields TEXT NOT NULL,                                 -- JSON object
  state TEXT NOT NULL DEFAULT 'hal',                    -- hal | confirmed | edited | hidden
  origin TEXT NOT NULL DEFAULT 'found',                 -- found | you
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX profile_items_kind ON profile_items (kind, state);
CREATE TABLE profile_evidence (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES profile_items(id) ON DELETE CASCADE,
  source_rel TEXT NOT NULL, quote TEXT NOT NULL DEFAULT '', field TEXT NOT NULL DEFAULT '',
  doc_type TEXT NOT NULL DEFAULT '', said TEXT NOT NULL DEFAULT '{}',   -- JSON object
  verified INTEGER NOT NULL DEFAULT 1,   -- 0: the quote was not found in the file, so the fact needs checking
  found_at TEXT NOT NULL);
CREATE INDEX profile_evidence_item ON profile_evidence (item_id);
CREATE TABLE profile_conflicts (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,   -- field | dates | overlap | gap
  item_ids TEXT NOT NULL,                               -- JSON list
  field TEXT NOT NULL DEFAULT '', values_ TEXT NOT NULL DEFAULT '[]',     -- JSON list
  why TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open',                  -- open | resolved
  resolution TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
"""


def up(con):
    """Added the profile's facts, their sources and their conflicts."""
    run_sql(con, SCHEMA)

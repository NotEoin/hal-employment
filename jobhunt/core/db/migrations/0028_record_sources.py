"""Migration 0028: the sources the person adds to each section of Your record, and a star on profile cards.

- `record_sources` holds one source each: a file or a set of files the person uploaded, a link, an account or
  a folder on this computer, added to one section (`sections.registry`: cv, letter, job_doc, coursework,
  lecture, official, project, document). The section sets what the source is and whose it is, so nothing
  guesses that later. `target_id` is the profile card it belongs to (an education card for coursework, a job
  for a job's document), NULL when none; it is cleared when that card is deleted, as a tidy rebuilds the cards
  nobody checked. `kind` is how the source is held (upload, files, link, account, folder, manual), `doc_kind`
  what it is within its section (cv, essay, transcript, repo, ...), `locator` its path, URL or account name,
  and `sha256` its content hash, so the same file is never added twice in any section. `note_rel` is the
  note Hal wrote for it in the record, `revision` counts its reads, `state` is new | reading | read | failed |
  kept_out with `error` saying why, `suggestion` is a section the source looks like it belongs in instead
  (shown to the person, never acted on alone), and `dated` is the date the reader found in it.
  The table is not called `sources`: migration 0012 gave that name to the job sites Hal searches.
- `record_source_files` lists the files of a source made of several (a folder or a zip), by their path
  inside it, with each one's hash.
- `profile_evidence.source_id`, `place` and `line` say which source a fact came from, and where in it
  (a file inside the source or a URL, and the line), so removing a source removes exactly its facts.
- `profile_items.star` marks a card the person wants told (a showcase project, a lead job), and `star_by`
  says who set it (`you`). `orphaned_from` is the state a card Hal found had when its last source was
  removed and it was hidden; a source naming it again brings it back in that state.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added the files, links and folders you add to each part of your record, and stars on your cards"

SCHEMA = """
CREATE TABLE record_sources (
  id INTEGER PRIMARY KEY, section TEXT NOT NULL,
  target_id INTEGER REFERENCES profile_items(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,                       -- upload | files | link | account | folder | manual
  doc_kind TEXT NOT NULL,                   -- cv | essay | transcript | repo | ... (`sections.registry`)
  locator TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
  starred INTEGER NOT NULL DEFAULT 0, current INTEGER NOT NULL DEFAULT 0, dated TEXT NOT NULL DEFAULT '',
  note_rel TEXT, added TEXT NOT NULL, last_checked TEXT, last_changed TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'new',        -- new | reading | read | failed | kept_out
  error TEXT NOT NULL DEFAULT '', suggestion TEXT NOT NULL DEFAULT '');
CREATE INDEX record_sources_section ON record_sources (section, target_id);
CREATE INDEX record_sources_sha ON record_sources (sha256);
CREATE TABLE record_source_files (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES record_sources(id) ON DELETE CASCADE,
  rel TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '',
  UNIQUE (source_id, rel));
CREATE INDEX profile_evidence_source ON profile_evidence (source_id);
"""


def up(con):
    """Adds the sources of each section of the record, where each fact came from in them, and stars."""
    add_column(con, "profile_evidence", "source_id", "INTEGER")
    add_column(con, "profile_evidence", "place", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "profile_evidence", "line", "INTEGER")
    add_column(con, "profile_items", "star", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "profile_items", "star_by", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "profile_items", "orphaned_from", "TEXT NOT NULL DEFAULT ''")
    run_sql(con, SCHEMA)

"""Migration 0011: cv."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added the CV store"

SCHEMA = """
CREATE TABLE cv_files (
  id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, rel TEXT NOT NULL,             -- data/cv/files/<sha256>.<ext>, read-only
  role TEXT NOT NULL,                                   -- baseline | source | export
  pages INTEGER, added TEXT NOT NULL);
CREATE TABLE cv_documents (
  id TEXT PRIMARY KEY,                                  -- "main" (one person per install)
  route TEXT NOT NULL,                                  -- as_is | improve | fresh
  send TEXT NOT NULL,                                   -- original | hal_copy
  baseline_file_id TEXT REFERENCES cv_files(id), current_version_id TEXT,
  type_id TEXT NOT NULL, look_id TEXT NOT NULL,
  layout TEXT NOT NULL DEFAULT '{}',                    -- {"sections": [{id, title, show, lock, max}],
                                                        --  "locked_lines": [target]}
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE cv_versions (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES cv_documents(id),
  n INTEGER NOT NULL,                                   -- "version 5"
  parent_id TEXT,
  origin TEXT NOT NULL,                                 -- imported | uploaded | read | corrected | generated |
                                                        -- improved | edited | manual | type_change | tailored | undo
  type_id TEXT, look_id TEXT, job_id TEXT,              -- job_id set for a per-job version
  file_id TEXT REFERENCES cv_files(id),                 -- set on the version made from an upload
  data TEXT NOT NULL,                                   -- CVDoc schema 2 JSON
  what TEXT NOT NULL,                                   -- "You accepted 3 of Hal's suggestions"
  by TEXT NOT NULL,                                     -- you | hal | baseline
  scanner_score INTEGER, created TEXT NOT NULL,
  binned TEXT,                                          -- in the bin; purged after 30 days
  UNIQUE (doc_id, n));
CREATE INDEX cv_versions_job ON cv_versions(job_id, n);
CREATE TABLE cv_parse_reports (
  version_id TEXT PRIMARY KEY REFERENCES cv_versions(id), file_id TEXT,
  found TEXT NOT NULL,                                  -- "contact details, 1 degree, 2 jobs, 14 skills"
  items TEXT NOT NULL);                                 -- [{path, label, value, sure, corrected}]
CREATE TABLE cv_suggestions (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
  based_on TEXT NOT NULL,                               -- version id it was worked out from
  job_id TEXT,                                          -- NULL for the main CV
  section_id TEXT, target TEXT NOT NULL,
  op TEXT NOT NULL,                                     -- replace | insert | delete | move | hide | reorder | retitle
  kind TEXT NOT NULL,                                   -- Wording | Results | Order | Cut | Consistency |
                                                        -- Symbols | Dates | Add | Gap
  title TEXT NOT NULL, why TEXT NOT NULL,
  trigger TEXT NOT NULL,                                -- JSON {type: check|type_rule|goal|requirement|setting,
                                                        --  id, label}
  evidence TEXT NOT NULL,                               -- JSON [{label, ref}]
  before TEXT, afters TEXT NOT NULL, alt INTEGER NOT NULL DEFAULT 0, edited TEXT,
  status TEXT NOT NULL,                                 -- pending | accepted | edited | left | superseded
  reason TEXT, words TEXT, remember INTEGER NOT NULL DEFAULT 0,
  result_version_id TEXT,
  fingerprint TEXT NOT NULL,                            -- sha1(target + trigger id + normalised after)
  ai TEXT, created TEXT NOT NULL, decided TEXT);
CREATE INDEX cv_suggestions_open ON cv_suggestions(doc_id, status);
CREATE INDEX cv_suggestions_fp ON cv_suggestions(fingerprint);
CREATE TABLE cv_preferences (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                                   -- keep_wording | never_phrase | lock_line | prefer
  target TEXT, text TEXT NOT NULL, from_suggestion TEXT,
  active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
CREATE TABLE cv_types (                                 -- copies the person made; built-ins are files
  id TEXT PRIMARY KEY, name TEXT NOT NULL, based_on TEXT, toml TEXT NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE job_cvs (                                  -- adjust for each job
  job_id TEXT PRIMARY KEY REFERENCES jobs(id), base_version_id TEXT NOT NULL, version_id TEXT,
  level TEXT NOT NULL,                                  -- no | little | more
  priorities TEXT,                                      -- the 3 to 5 things the person picked
  changes TEXT NOT NULL DEFAULT '[]',                   -- [{id, kind, where, target, before, after, why, ev, undone}]
  approved TEXT);
CREATE TABLE cv_checks (                                -- cache by version and look
  version_id TEXT NOT NULL, look_id TEXT NOT NULL, pdf_sha TEXT,
  scanner TEXT, type_checks TEXT, recruiter TEXT, recruiter_at TEXT, at TEXT NOT NULL,
  PRIMARY KEY (version_id, look_id));
CREATE TABLE cv_keywords_off (job_id TEXT NOT NULL, term TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (job_id, term));
"""


def up(con):
    """Added the CV store."""
    run_sql(con, SCHEMA)

"""Migration 0014: sites."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added notes about job sites"

SCHEMA = """
CREATE TABLE sites (
  key TEXT PRIMARY KEY,                -- "civicinsight.org.uk", "acme.wd3.myworkdayjobs.com", "ats:workday"
  kind TEXT NOT NULL,                  -- site | tenant | ats
  ats TEXT, name TEXT,
  account TEXT NOT NULL DEFAULT 'unknown',   -- unknown | none | needed | signed_in
  effort INTEGER NOT NULL DEFAULT 0,   -- "too much effort" remembered (L server.py:1048-1067)
  uses INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT, last_seen TEXT);
CREATE TABLE site_notes (
  id TEXT PRIMARY KEY,                 -- "db:<n>"; built-ins are "seed:<id>" and live in sites/seed.toml
  seed_id TEXT,                        -- the built-in note this row edits or switches off
  scope_kind TEXT NOT NULL,            -- global | ats | site | tenant | job
  scope_key TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,                  -- the kinds of 10.3
  cond_json TEXT NOT NULL DEFAULT '{}',   -- when: url pattern, step, field label pattern, control name
  act_json TEXT NOT NULL DEFAULT '{}',    -- what to do
  said TEXT,                           -- the person's own words, when reported
  text TEXT NOT NULL,                  -- plain words shown everywhere
  origin TEXT NOT NULL,                -- builtin | you | learned | hal
  status TEXT NOT NULL,                -- active | proposed | off
  private INTEGER NOT NULL DEFAULT 0,  -- never exported or shared (fixed answers)
  uses INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0,
  stale INTEGER NOT NULL DEFAULT 0,    -- "may be out of date"
  evidence TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX site_notes_scope ON site_notes(scope_kind, scope_key, status);
CREATE TABLE site_events (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, job_id TEXT, site TEXT, url TEXT, step INTEGER,
  what TEXT NOT NULL,                  -- note_used | note_failed | wall | refused | upload_ok | upload_failed |
                                       -- timeout | report | would_submit
  note_id TEXT, detail TEXT);
CREATE INDEX site_events_site ON site_events(site, at);
CREATE TABLE site_reports (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, job_id TEXT, site TEXT NOT NULL, ats TEXT,
  what TEXT NOT NULL, words TEXT, pointed TEXT,         -- pointed: JSON {hid, label, nearby} (cut-first)
  draft TEXT, state TEXT NOT NULL,                      -- new | drafted | saved | cancelled
  note_id TEXT, outcome TEXT);                          -- retry | myself | skip
"""


def up(con):
    """Added notes about job sites."""
    run_sql(con, SCHEMA)

"""Migration 0004: actions and activity."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added actions and the Activity list"

SCHEMA = """
CREATE TABLE actions (
  id TEXT PRIMARY KEY,                 -- uuid4 hex
  kind TEXT NOT NULL,                  -- "apply.fill", "cv.suggest" ...
  subject TEXT,                        -- job id, or a pseudo subject "_cv", "_mail", "_search" (L server.py:1336-1369)
  job_id TEXT REFERENCES jobs(id),
  state TEXT NOT NULL,                 -- queued | running | waiting_for_you | done | error | stopped | interrupted
  priority INTEGER NOT NULL DEFAULT 1, -- 0 you just asked, 1 a job you chose, 2 background
  params TEXT, result TEXT, message TEXT, error TEXT,
  step_n INTEGER NOT NULL DEFAULT 0, step_total INTEGER,
  resumable INTEGER NOT NULL DEFAULT 0,
  session_id TEXT, helper_id TEXT, activity_id INTEGER,
  created TEXT NOT NULL, started TEXT, ended TEXT);
CREATE INDEX actions_subject ON actions(subject, state);
CREATE INDEX actions_state ON actions(state, priority);
CREATE TABLE action_steps (
  id INTEGER PRIMARY KEY, action_id TEXT NOT NULL REFERENCES actions(id), n INTEGER NOT NULL,
  at TEXT NOT NULL, text TEXT NOT NULL, why TEXT, used TEXT, ai TEXT, took_ms INTEGER,
  state TEXT NOT NULL);                -- running | done | error | skipped
CREATE INDEX action_steps_action ON action_steps(action_id, n);
CREATE TABLE activity (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL,
  area TEXT NOT NULL,                  -- find | documents | applying | email | learning | settings | cv | system
  kind TEXT NOT NULL,                  -- action kind or event kind ("setting.changed")
  icon TEXT, tone TEXT,                -- the row's icon and tone (hal, info, good, warn, bad, why)
  text TEXT NOT NULL,                  -- one line, plain words
  why TEXT NOT NULL CHECK (length(why) > 0),
  used TEXT,                           -- JSON [{kind, label, ref}]: what Hal used to decide
  ai TEXT,                             -- JSON {provider, model, role, calls, ms}; the UI builds the words
  took_ms INTEGER, changed TEXT,
  undo TEXT, undone TEXT,              -- undo: JSON {type, ...} or NULL
  job_id TEXT, action_id TEXT, session_id TEXT,
  remote INTEGER NOT NULL DEFAULT 0,   -- started from another computer (shown under Details)
  running INTEGER NOT NULL DEFAULT 0, detail TEXT);
CREATE INDEX activity_at ON activity(at);
CREATE INDEX activity_job ON activity(job_id, at);
CREATE INDEX activity_area ON activity(area, at);
CREATE TABLE data_tasks (
  name TEXT PRIMARY KEY, after INTEGER NOT NULL, state TEXT NOT NULL,   -- pending | running | done | error
  started TEXT, ended TEXT, detail TEXT);
"""


def up(con):
    """Added actions and the Activity list."""
    run_sql(con, SCHEMA)

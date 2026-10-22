"""Migration 0002: auth."""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added sign-in sessions and browser helper pairing"

SCHEMA = """
CREATE TABLE sessions (
  id_hash TEXT PRIMARY KEY,            -- sha256 of the cookie value; the value is never stored
  created TEXT NOT NULL, last_seen TEXT NOT NULL, expires TEXT NOT NULL,
  remote INTEGER NOT NULL DEFAULT 0,   -- opened from another computer (open-url --remote, 11.5)
  remote_host TEXT,                    -- the desktop's host name as the launcher reported it
  browser_id TEXT NOT NULL,            -- long-lived per-browser id cookie, links a helper to this browser
  user_agent TEXT, label TEXT, ended TEXT);
CREATE TABLE open_tokens (
  hash TEXT PRIMARY KEY, created TEXT NOT NULL, expires TEXT NOT NULL,   -- 120 seconds, single use
  remote INTEGER NOT NULL DEFAULT 0, remote_host TEXT, next TEXT, used TEXT);
CREATE TABLE helpers (
  id TEXT PRIMARY KEY, key_hash TEXT NOT NULL,
  browser TEXT, version TEXT, protocol INTEGER, extension_id TEXT,
  session_id TEXT, browser_id TEXT, remote INTEGER NOT NULL DEFAULT 0,
  paired TEXT NOT NULL, last_seen TEXT, label TEXT, revoked TEXT);
CREATE TABLE pairing_codes (
  hash TEXT PRIMARY KEY, session_id TEXT NOT NULL, browser TEXT,
  created TEXT NOT NULL, expires TEXT NOT NULL, used TEXT);
"""


def up(con):
    """Added sign-in sessions and browser helper pairing."""
    run_sql(con, SCHEMA)

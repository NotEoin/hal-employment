"""The AI's part of Home: "Sign in to Claude again" once Claude's sign-in starts failing.

The signal is the newest Claude answer Hal asked for: when it failed as signed out, Home says so until an
answer works again. That costs one database read, so Home never waits on the Claude command line.
"""
from __future__ import annotations

import sqlite3

URGENCY = 8      # before emails (20): until it is fixed, Hal writes nothing for any job


def signed_out_since(con: sqlite3.Connection) -> str | None:
    """When Claude last refused Hal as signed out, if no answer has worked since; else None."""
    row = con.execute("SELECT at, ok, error_kind FROM ai_usage WHERE provider = 'claude_cli' "
                      "AND (ok = 1 OR error_kind = 'signed_out') ORDER BY id DESC LIMIT 1").fetchone()
    if row is None or row["ok"]:
        return None
    return row["at"]


def needs(con: sqlite3.Connection) -> list[dict]:
    """The sign-in item, while the newest Claude call failed as signed out."""
    from .health import chosen

    if chosen() != "claude_cli":
        return []
    since = signed_out_since(con)
    if since is None:
        return []
    return [{"id": "ai.claude_sign_in", "icon": "cpu", "tone": "warn", "title": "Sign in to Claude again",
             "detail": "Claude stopped accepting Hal's sign-in, so Hal cannot write documents until you sign in.",
             "due": None, "act": {"label": "Sign in again", "route": "setup-ai"}, "urgency": URGENCY}]

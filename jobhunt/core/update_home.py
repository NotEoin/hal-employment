"""Home's update items: "An update is ready" after the daily check, and "Sign in to GitHub again".

Read from the last check's result in `state/update-check.json`, so Home never runs git. A held commit (one
that failed to install) is never offered again.
"""
from __future__ import annotations

import sqlite3

from . import updates

READY_URGENCY = 60          # after what waits for you, the emails and the to-dos
SIGN_IN_URGENCY = 40


def needs(con: sqlite3.Connection) -> list[dict]:
    """The update items, when there are any."""
    check = updates.last_check()
    if not check:
        return []
    if check.get("state") == "sign_in":
        return [{"id": "updates.sign_in", "icon": "download", "tone": "warn", "title": "Sign in to GitHub again",
                 "detail": updates.SIGN_IN_AGAIN, "due": None,
                 "act": {"label": "Open Updates", "route": "settings-updates"}, "urgency": SIGN_IN_URGENCY}]
    if check.get("available") and check.get("newest") != updates.held_commit():
        n = int(check.get("behind") or 0)
        return [{"id": "updates.ready", "icon": "download", "tone": "info", "title": "An update is ready",
                 "detail": f"Hal {check.get('version') or ''} has {n} change{'s' if n != 1 else ''}. "
                           "It installs when you press Update Hal.".replace("  ", " "),
                 "due": None, "act": {"label": "Update Hal", "route": "settings-updates"},
                 "urgency": READY_URGENCY}]
    return []

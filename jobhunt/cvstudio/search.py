"""Ctrl K results from My CV: its tabs and its two dialogs, by name."""
from __future__ import annotations

import sqlite3

ITEMS = (("Your CV", "Page", "cv"), ("CV versions", "Page", "cv-versions"), ("CV type and look", "Page", "cv-style"),
         ("CV suggestions", "Page", "cv-sugg"), ("CV checks", "Page", "cv-checks"))
ACTIONS = (("Change how your CV is made", "dialog:route"),)


def search(con: sqlite3.Connection, q: str) -> list[dict]:
    """My CV's tabs and actions whose words hold the query."""
    low = q.strip().lower()
    if not low:
        return []
    out = [{"label": label, "kind": kind, "route": r} for label, kind, r in ITEMS if low in label.lower()]
    out += [{"label": label, "kind": "Action", "action": a} for label, a in ACTIONS if low in label.lower()]
    return out

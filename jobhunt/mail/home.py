"""The email area's part of Home: recent emails that need you (an interview, a test, an offer, a person)."""
from __future__ import annotations

import sqlite3
from datetime import timedelta

from ..core import clock
from . import messages

URGENCY = 20                 # after actions waiting for you (10), before documents to make again (30)
RECENT_DAYS = 3              # as gmail.sync's attention window
ICONS = {"interview": "calendar", "assessment": "calendar", "offer": "star", "human": "mail"}
TITLES = {"interview": "Interview email", "assessment": "Test or assessment email", "offer": "Offer email",
          "human": "A person wrote to you"}


def needs(con: sqlite3.Connection) -> list[dict]:
    """Unread emails of the kinds that need you, from the last few days."""
    since = clock.iso(clock.now_utc() - timedelta(days=RECENT_DAYS))[:19]
    marks = ",".join("?" * len(messages.ATTENTION))
    out = []
    for r in con.execute(f"""SELECT * FROM emails WHERE read = 0 AND archived = 0 AND kind IN ({marks})
                             AND substr(date, 1, 19) >= ? ORDER BY date DESC""", (*messages.ATTENTION, since)):
        who = messages.sender_words(r["sender"], r["company"])
        out.append({"id": f"email:{r['msgid']}", "icon": ICONS.get(r["kind"], "mail"),
                    "tone": "good" if r["kind"] in ("interview", "offer") else "info",
                    "title": f"{TITLES.get(r['kind'], 'Email')}: {r['subject'] or '(no subject)'}",
                    "detail": f"From {who}.", "due": "",
                    "act": {"label": "Read it", "route": "inbox"}, "urgency": URGENCY})
    return out

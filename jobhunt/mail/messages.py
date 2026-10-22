"""Messages: emails Hal sorted from the inbox and alerts from Hal, as one list.

A message id names its table: `e:<msgid>` for an email, `a:<id>` for an alert, so one route can mark either
read or archive it. Opening a message marks it read (migration 0010 added the column; before, nothing did).
Job alert emails count as "From Hal"'s neighbours, not as employers: they come from job boards.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from ..core import clock, events

FILTERS = ("all", "unread", "employers", "hal")
LIST_LIMIT = 200
SNIPPET_CHARS = 220
WEEK_DAYS = 6
EMPLOYER_KINDS_NOT = ("alert",)
EMAIL_TONES = {"interview": "good", "offer": "good", "assessment": "info", "human": "info"}
ALERT_TONES = {"urgent": "warn", "error": "bad", "good": "good", "info": "info"}
ATTENTION = ("assessment", "interview", "offer", "human")   # L gmail.py:41
SENDER_NAME = re.compile(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$')


def when_words(at: str, now: datetime | None = None) -> str:
    """"11:05" today, "Mon" within the week, else "14 Oct"."""
    t = clock.parse(at).astimezone()
    now = (now or clock.now_utc()).astimezone()
    if t.date() == now.date():
        return t.strftime("%H:%M")
    if now.date() - t.date() <= timedelta(days=WEEK_DAYS):
        return t.strftime("%a")
    return f"{t.day} {t.strftime('%b')}"


def sender_words(sender: str | None, company: str | None) -> str:
    """The sender as a person reads it: the name before the address, else the company, else the address."""
    m = SENDER_NAME.match(sender or "")
    if m:
        return m.group(1).strip()
    return company or (sender or "").strip() or "Unknown sender"


def _email_item(r: sqlite3.Row) -> dict:
    from .. import tasks

    kind = r["kind"]
    url = tasks.thread_url(r["thrid"]) if kind in ATTENTION else None
    action = {"label": "Open the email", "url": url} if url else None
    if action is None and r["job_id"] and kind != "alert":
        action = {"label": "See the job", "route": "job-" + r["job_id"]}
    text = (r["note"] or r["snippet"] or "").strip()
    return {"id": f"e:{r['msgid']}", "kind": "interview" if kind in ("interview", "assessment") else "email",
            "unread": not r["read"], "title": r["subject"] or "(no subject)",
            "from": sender_words(r["sender"], r["company"]), "at": r["date"], "when": when_words(r["date"]),
            "text": text[:SNIPPET_CHARS], "action": action, "tone": EMAIL_TONES.get(kind, ""),
            "employer": kind not in EMPLOYER_KINDS_NOT}


def _alert_item(r: sqlite3.Row) -> dict:
    action = None
    if r["action"]:
        try:
            action = json.loads(r["action"])
        except ValueError:
            action = None
    if action is None and r["job_id"]:
        action = {"label": "See the job", "route": "job-" + r["job_id"]}
    return {"id": f"a:{r['id']}", "kind": r["kind"] or "alert", "unread": not r["read"], "title": r["title"],
            "from": "Hal", "at": r["at"], "when": when_words(r["at"]), "text": (r["body"] or "")[:SNIPPET_CHARS],
            "action": action, "tone": ALERT_TONES.get(r["level"], ""), "employer": False}


def listing(con: sqlite3.Connection, filter: str = "all") -> list[dict]:
    """Messages not archived, newest first, for one filter (all, unread, employers, hal)."""
    if filter not in FILTERS:
        raise ValueError(f"There is no message filter called {filter!r}.")
    items = []
    if filter != "hal":
        items += [_email_item(r) for r in con.execute(
            "SELECT * FROM emails WHERE archived = 0 ORDER BY date DESC LIMIT ?", (LIST_LIMIT,))]
    if filter != "employers":
        items += [_alert_item(r) for r in con.execute(
            "SELECT * FROM alerts WHERE archived = 0 ORDER BY at DESC LIMIT ?", (LIST_LIMIT,))]
    if filter == "unread":
        items = [i for i in items if i["unread"]]
    if filter == "employers":
        items = [i for i in items if i["employer"]]
    items.sort(key=lambda i: i["at"][:19], reverse=True)
    return items[:LIST_LIMIT]


def unread_count(con: sqlite3.Connection) -> int:
    """How many messages are not read yet (the rail's count)."""
    return (con.execute("SELECT count(*) FROM emails WHERE read = 0 AND archived = 0").fetchone()[0]
            + con.execute("SELECT count(*) FROM alerts WHERE read = 0 AND archived = 0").fetchone()[0])


def _split(message_id: str) -> tuple[str, str]:
    table, _, key = message_id.partition(":")
    if table not in ("e", "a") or not key:
        raise LookupError("That message is not in Hal's list.")
    return table, key


def _update(con: sqlite3.Connection, message_id: str, column: str) -> None:
    table, key = _split(message_id)
    if table == "e":
        cur = con.execute(f"UPDATE emails SET {column} = 1 WHERE msgid = ?", (key,))
    else:
        cur = con.execute(f"UPDATE alerts SET {column} = 1 WHERE id = ?", (key,))
    if not cur.rowcount:
        raise LookupError("That message is not in Hal's list.")
    con.commit()
    events.publish("message", id=message_id)


def mark_read(con: sqlite3.Connection, message_id: str) -> None:
    """Marks one message read; LookupError when there is none."""
    _update(con, message_id, "read")


def archive(con: sqlite3.Connection, message_id: str) -> None:
    """Archives one message (it leaves the list; nothing is deleted)."""
    _update(con, message_id, "archived")


def mark_all_read(con: sqlite3.Connection) -> int:
    """Marks every message read; returns how many changed."""
    n = con.execute("UPDATE emails SET read = 1 WHERE read = 0").rowcount
    n += con.execute("UPDATE alerts SET read = 1 WHERE read = 0").rowcount
    con.commit()
    events.publish("message", id="all")
    return n

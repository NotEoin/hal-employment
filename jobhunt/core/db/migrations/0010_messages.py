"""Migration 0010: messages can be read and archived.

Today opening a message never marks it read, so every one is unread. This marks emails read, except the
kinds that need the person's attention from the last 3 days, and marks alerts older than
7 days read, so Home starts with what is new rather than with every message ever received.
"""

from datetime import timedelta

from jobhunt.core.db.migrate import add_column, iso, utc_now

TITLE = "Added read and archived messages"

# (table, column, declaration); every new column is nullable or has a default.
COLUMNS = (
    ("emails", "read", "INTEGER NOT NULL DEFAULT 0"),
    ("emails", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("emails", "note", "TEXT"),                               # what Hal did with it, in plain words
    ("alerts", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("alerts", "kind", "TEXT"),                               # alert | todo | suggestion | update | health
    ("alerts", "action", "TEXT"),                             # JSON {label, route} or {label, url}
    ("todos", "area", "TEXT"),                                # for Home grouping
)

ATTENTION = ("assessment", "interview", "offer", "human")   # the kinds the old mail reader treated as needing attention
EMAIL_DAYS = 3
ALERT_DAYS = 7


def up(con):
    """Adds the columns, then marks old messages read."""
    for table, column, declaration in COLUMNS:
        add_column(con, table, column, declaration)
    now = utc_now()
    # The first 19 characters are the same in old (+00:00) and new (Z) timestamps.
    email_cutoff = iso(now - timedelta(days=EMAIL_DAYS))[:19]
    alert_cutoff = iso(now - timedelta(days=ALERT_DAYS))[:19]
    marks = ",".join("?" * len(ATTENTION))
    con.execute(f"UPDATE emails SET read = 1 WHERE NOT (kind IN ({marks}) AND substr(date, 1, 19) >= ?)",
                (*ATTENTION, email_cutoff))
    con.execute("UPDATE alerts SET read = 1 WHERE substr(at, 1, 19) < ?", (alert_cutoff,))

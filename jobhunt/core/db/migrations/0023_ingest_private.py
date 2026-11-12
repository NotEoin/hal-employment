"""Migration 0023: files the ingest kept out of the record because they look private, and the ones let in.

`ingest_items.private` holds the reason shown beside the file's name (state `private`); the file's text,
note and facts are never stored. `ingest_items.allowed` is 1 once the person chose "Add it anyway", so the
same bytes at the same path, or with the same name in a folder or zip, are not held back again.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added the files kept out of your record because they look private"


def up(con):
    """Adds the reason a file was kept out and whether the person let it in."""
    add_column(con, "ingest_items", "private", "TEXT")
    add_column(con, "ingest_items", "allowed", "INTEGER NOT NULL DEFAULT 0")
    run_sql(con, "CREATE INDEX ingest_items_private ON ingest_items(state, allowed);")

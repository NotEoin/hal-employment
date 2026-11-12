"""Migration 0024: where each file read into the record came from, in the person's own words.

`ingest_items.origin` is `mine` (a folder of their own work), `course` (course material they were given) or
`mixed` (a mix of both, and every row from before), set from the folder or zip the item was
found under (`ingest.roles.ORIGINS`). It moves with the row when a file is read again or tidied.
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added where each file read into your record came from"


def up(con):
    """Adds where each file read into the record came from."""
    add_column(con, "ingest_items", "origin", "TEXT NOT NULL DEFAULT 'mixed'")

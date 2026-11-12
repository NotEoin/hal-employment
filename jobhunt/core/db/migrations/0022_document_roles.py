"""Migration 0022: whose each document is, kept on its ingest item and on each fact's evidence.

`ingest_items.role` and `profile_evidence.role` are `own`, `taught`, `others` or `unsure` (`ingest.roles`).
Rows from before are '' (not judged yet), which counts as unsure: the tidy pass judges them from their notes.
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added whose each document in your record is"


def up(con):
    """Added whose each document in your record is."""
    add_column(con, "ingest_items", "role", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "profile_evidence", "role", "TEXT NOT NULL DEFAULT ''")

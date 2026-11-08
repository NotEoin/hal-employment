"""Migration 0027: cards of the profile folded into another card that is the same experience, with the way back.

`profile_items.merged_into` is the card this one was folded into (NULL when none); such a card is hidden.
`profile_items.merged_why` is Hal's few plain words on why ("Part of Lantern workspace").
`profile_evidence.moved_from` is the card a piece of evidence was on before it was moved to the card it was
folded into (NULL when it never moved), so where it came from is never lost (`profile.store`).
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added a way to fold one experience into another under a different name"


def up(con):
    """Adds where each profile card was folded and why, and where each piece of evidence was before."""
    add_column(con, "profile_items", "merged_into", "INTEGER")
    add_column(con, "profile_items", "merged_why", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "profile_evidence", "moved_from", "INTEGER")

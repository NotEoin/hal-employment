"""Migration 0018: drops the old global `answers` table, so saved answers live in `answers2` alone.

Nothing is copied here, because nothing is left to copy. Migration 0007 copied every ordinary row of
`answers` into `answers2`, and nothing has written to the old table since: the form filler reads and writes
`answers2` only, and both importers migrate an old database before they take rows from it. An ordinary row
that `answers2` no longer holds is one the person deleted, and copying it again would bring it back.

The rows 0007 left in place ask private questions (disability, adjustments, guaranteed interview schemes,
caring). They were kept out of `answers2` because the AI reads it when it drafts a form; the person keeps
those answers in the private answers store, which this migration does not touch. Dropping the table takes
them out of the database too. The backup taken before migrating still holds the table.
"""

TITLE = "Removed the old list of saved answers"


def up(con):
    """Drops `answers`; 0007 copied what may be reused."""
    con.execute("DROP TABLE answers")

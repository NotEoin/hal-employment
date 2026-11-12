"""Migration 0026: where each experience in the profile was done.

`profile_items.context` is `degree` (coursework, a dissertation, module work), `team` (a university team
project), `work` (done in a job), `personal` (in the person's own time) or '' (not known);
`context_by` is `hal` or `person`, and Hal never changes a context the person chose (`profile.contexts`).
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added where each of your experiences was done"


def up(con):
    """Adds each profile item's context and who set it."""
    for column in ("context", "context_by"):
        add_column(con, "profile_items", column, "TEXT NOT NULL DEFAULT ''")

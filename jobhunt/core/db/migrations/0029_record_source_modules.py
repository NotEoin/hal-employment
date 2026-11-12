"""Migration 0029: the module a piece of coursework or lecture material is for, and what its reader found.

- `record_sources.module` is the module of its education card the person chose for a coursework or lecture
  source: NULL until they choose, '' when they say it is for no one module. Hal's own suggestion is not kept
  here, so no read ever changes what the person chose.
- `record_sources.details` is a JSON object of what the reader found about the source itself rather than the
  person, replaced at each read: for coursework its mark, summary, suggested module and the AI's reading
  (kept so a read again after a star or a module changes asks the AI nothing new).
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added the module each piece of coursework is for"


def up(con):
    """Adds each record source's module and the details its reader found."""
    add_column(con, "record_sources", "module", "TEXT")
    add_column(con, "record_sources", "details", "TEXT NOT NULL DEFAULT '{}'")

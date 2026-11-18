"""Migration 0035: a page limit per CV, and the real page counts it is held to.

- `cv_documents.page_limit` is "Keep this CV to" (0 for no limit, 1 or 2 pages). NULL, as every CV has after
  this migration, means the person has not chosen, so the CV follows its CV type's `pages` rule as before
  (`cvstudio/pagelimit.py`).
- `cv_versions.pages` is the page count of the version printed in its look, and `pages_key` the hash of the
  printed page it was counted from, so a new look or new contact details count again. A key with no count
  means no browser could print it.
- `job_cvs.pages` and `job_cvs.pages_key` are the same for a job's CV: the pages of the PDF employers get,
  keyed by the hash of what it was drawn from.
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added a page limit for each CV and its page counts"


def up(con):
    """Adds the page limit and the page counts."""
    add_column(con, "cv_documents", "page_limit", "INTEGER")
    add_column(con, "cv_versions", "pages", "INTEGER")
    add_column(con, "cv_versions", "pages_key", "TEXT")
    add_column(con, "job_cvs", "pages", "INTEGER")
    add_column(con, "job_cvs", "pages_key", "TEXT")

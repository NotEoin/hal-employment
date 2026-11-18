"""Migration 0034: which of the person's CVs an application is built from, and why.

- `job_cvs.base_choice` holds the choice as JSON: {by, reason, pick, scores}. `by` is "hal" for Hal's pick
  (`cvstudio/choose.py`), which writing the documents again makes afresh, or "you" for a CV the person picked,
  which a rewrite keeps. `pick` is the CV Hal would pick, `scores` each CV's fit. The CV itself is the one
  `base_version_id` belongs to, so it is not stored twice.
- NULL for every application written before: it was built from the lead CV, which is what Hal shows for it.
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added which CV each application is built from"


def up(con):
    """Adds the choice of CV to each job's CV."""
    add_column(con, "job_cvs", "base_choice", "TEXT")

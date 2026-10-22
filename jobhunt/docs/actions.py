"""The documents area's background work: the one-time move of the old pack folders.

The move runs once, as a data task, after migration 0009 has numbered every application; the work lives in
`docs.relayout`, so a test can run it on made-up folders without the task runner.
"""
from __future__ import annotations

from jobhunt.core.db.tasks import data_task
from jobhunt.docs import relayout

APPLICATIONS_MIGRATION = 9   # 0009_applications numbers applications and adds the files tables


@data_task("docs.relayout_packs", after=APPLICATIONS_MIGRATION,
           title="Moved your documents into one numbered folder per application")
def relayout_packs(ctx) -> None:
    """Moves each old shared pack folder into its owner's application folder."""
    counts = relayout.run(ctx.con, report=ctx.step)
    ctx.changed(f"Moved {counts['moved']} applications' documents. {counts['redraft']} need making again.")

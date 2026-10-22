"""The find area's background work: re-check every job, the AI check, preparing ahead, and the rescore data task.

Each kind is a persisted action, so it shows in the status line with its steps and can be
stopped; the work itself lives in `find.rules`, `screen` and `replenish`, which the command line also calls.
`find.rescore_all` runs once after migration 6 so every job gets its reasons.
"""

from __future__ import annotations

from jobhunt.core import runner
from jobhunt.core.db.tasks import data_task
from jobhunt.find import rules

FIND_MIGRATION = 6          # 0006_find adds job_reasons and the kind columns this task fills
PROGRESS_EVERY = 500        # jobs between progress steps


def _rescore(ctx) -> dict:
    def step(done: int, total: int) -> None:
        ctx.checkpoint()
        ctx.step(f"Checked {done:,} of {total:,} jobs", total=total)

    total = rules.rescore_all(ctx.con, step, every=PROGRESS_EVERY)
    shown = ctx.con.execute("SELECT COUNT(*) FROM jobs WHERE active = 1 AND hidden_rule IS NULL").fetchone()[0]
    ctx.changed(f"Re-checked {total:,} jobs; {shown:,} open jobs are shown.")
    return {"jobs": total, "shown": shown}


def _rescore_example(world) -> dict:
    return {"setting": "scoring.w.fresh"}


@runner.action("find.rescore", area="find", label="Re-checking every job with your new points",
               why="You changed {setting}, which affects every job's score", priority=2, example=_rescore_example)
def rescore(ctx, setting: str = "your goal") -> dict:
    """Re-scores every job under the live goal and weights."""
    return _rescore(ctx)


def _screen_example(world) -> dict:
    row = world.con.execute("SELECT id, score FROM jobs WHERE status = 'new' ORDER BY score DESC LIMIT 1").fetchone()
    return {"job_id": row["id"], "score": row["score"]}


@runner.action("find.screen", area="find", label="Checking that a new match fits",
               why="A new match scored {score}; Hal checks it fits before showing it", priority=2,
               example=_screen_example)
def screen_job(ctx, job_id: str, score: int | None = None) -> dict:
    """The AI check on one job."""
    from jobhunt import screen

    row = ctx.con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("That job is not in Hal's list.")
    ctx.step(f"Reading the advert for {row['title']} at {row['company']}")
    eligible, why = screen.screen_one(ctx.con, row)
    ctx.changed(f"{'Kept' if eligible else 'Hid'} {row['title']} at {row['company']}: {why}")
    return {"eligible": eligible, "why": why}


def _prepare_example(world) -> dict:
    return {"n": 1, "target": 3}


@runner.action("find.prepare", area="documents", label="Preparing documents for your best matches",
               why="One of your best matches; Hal prepares documents ahead ({n} of {target})", priority=2,
               example=_prepare_example)
def prepare(ctx, n: int = 1, target: int | None = None) -> dict:
    """One unit of preparing ahead: the next screen or pack (L replenish.py)."""
    from jobhunt import replenish

    worked, message = replenish.step(ctx.con, report=ctx.step)
    return {"worked": worked, "message": message}


@data_task("find.rescore_all", after=FIND_MIGRATION, title="Gave every job its reasons under your goal")
def rescore_all(ctx) -> None:
    """Re-scores every job once after the update, so each has its reasons and kind."""
    _rescore(ctx)

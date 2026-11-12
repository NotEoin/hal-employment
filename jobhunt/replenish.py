"""Keeps documents ready for the best matches: screens the next job and drafts its pack.

The queue is the open jobs with status 'ready'. While it holds fewer than `scoring.prepare_ahead`, the best
shown job among the kinds the person chose (`hidden_rule IS NULL`, at or above `scoring.min_show`) is
screened by the AI when `scoring.screen` is on and, if it fits, gets a pack. Paid internships, placements
and the other kinds are prepared like any job when the goal includes them; today's code never queued a
graduate internship. Model work waits for the GPU, and nothing is drafted while the person has no checked
card and no CV (`pack.has_facts`): every pack would fail in turn.
"""
from . import db, gpu, pack, screen
from .core import settings

# A pack that failed is retried after this long, in case the cause was transient.
RETRY_AFTER = "-1 day"

CANDIDATES = """
SELECT j.* FROM jobs j
WHERE j.status IN ('new', 'shortlisted') AND j.active = 1 AND COALESCE(j.score, 0) >= ?
  AND j.hidden_rule IS NULL AND COALESCE(j.screen_state, '') != 'out'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.job_id = j.id AND e.kind = 'pack-error'
                  AND e.at >= datetime('now', ?))
  AND NOT EXISTS (SELECT 1 FROM jobs d WHERE d.id != j.id AND d.status NOT IN ('new', 'closed')
                  AND lower(d.company) = lower(j.company) AND lower(d.title) = lower(j.title))
ORDER BY j.status = 'shortlisted' DESC, j.score DESC, j.first_seen DESC
LIMIT 1
"""


def __getattr__(name):
    # old constants, read live
    if name == "TARGET":
        return int(settings.get("scoring.prepare_ahead"))
    if name == "MIN_SCORE":
        return float(settings.get("scoring.min_show"))
    raise AttributeError(name)


def _live(name: str):
    """An old constant: a value a test assigned to the module, else the live setting."""
    return globals()[name] if name in globals() else __getattr__(name)


def queued(con) -> int:
    """How many open jobs have their documents ready."""
    return con.execute("SELECT COUNT(*) FROM jobs WHERE status = 'ready' AND active = 1").fetchone()[0]


def _screened(job) -> bool:
    keys = job.keys() if hasattr(job, "keys") else ()
    if "screen_state" in keys and job["screen_state"] == "eligible":
        return True
    return "screened eligible" in (job["score_notes"] or "")


def step(con, report=lambda msg: None) -> tuple[bool, str]:
    """Does one unit of queue work: a screen or a pack.

    Args:
        con: Database connection.
        report: Called with a short description of the work as it starts.

    Returns:
        (worked, message): whether anything was done, and the queue's state.
    """
    target, min_score = _live("TARGET"), _live("MIN_SCORE")
    have = queued(con)
    if have >= target:
        return False, f"queue full, {have - target} more in reserve" if have > target else "queue full"
    job = con.execute(CANDIDATES, (min_score, RETRY_AFTER)).fetchone()
    if job is None:
        return False, f"{have} of {target} queued; no more jobs score {min_score:.0f} or above"
    if not pack.has_facts(con):
        return False, (f"{have} of {target} queued; nothing of yours to write from yet: check your record or add "
                       "your CV")
    reason = gpu.busy()
    if reason:
        return False, f"{have} of {target} queued; {reason}"

    if settings.get("scoring.screen") and not _screened(job) and job["status"] == "new":
        report(f"screening {job['company']}: {job['title']}")
        eligible, why = screen.screen_one(con, job)
        if not eligible:
            return True, f"screened out {job['company']}: {why}"

    report(f"drafting the pack for {job['company']}: {job['title']}")
    try:
        pack.build(con, job["id"])
    except Exception as e:  # noqa: BLE001 - a bad advert or model reply must not stop the queue
        db.log(con, job["id"], "pack-error", f"{type(e).__name__}: {e}")
        con.commit()
        return True, f"pack failed for {job['company']}: {e}"
    db.alert(con, "info", f"New in your queue: {job['company']}", job["title"], job["id"])
    con.commit()
    return True, f"queued {job['company']}: {job['title']}"

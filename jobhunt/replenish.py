"""Keeps the application queue full: screens the best unreviewed jobs and drafts their packs.

The queue is the open jobs with status 'ready'. While it holds fewer than
`queue_size` (config.toml, default 10), the next candidate by score is screened
by the local model and, if eligible, gets a pack. Model work waits for the GPU.
"""
from . import db, gpu, pack, screen
from .config import CONFIG

TARGET = int(CONFIG.get("queue_size", 10))
MIN_SCORE = float(CONFIG.get("queue_min_score", 45))
# A pack that failed is retried after this long, in case the cause was transient.
RETRY_AFTER = "-1 day"

CANDIDATES = """
SELECT j.* FROM jobs j
WHERE j.status IN ('new', 'shortlisted') AND j.active = 1 AND COALESCE(j.score, 0) >= ?
  AND COALESCE(j.score_notes, '') NOT LIKE '%graduate internship%'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.job_id = j.id AND e.kind = 'pack-error'
                  AND e.at >= datetime('now', ?))
  AND NOT EXISTS (SELECT 1 FROM jobs d WHERE d.id != j.id AND d.status NOT IN ('new', 'closed')
                  AND lower(d.company) = lower(j.company) AND lower(d.title) = lower(j.title))
ORDER BY j.status = 'shortlisted' DESC, j.score DESC, j.first_seen DESC
LIMIT 1
"""


def queued(con) -> int:
    return con.execute("SELECT COUNT(*) FROM jobs WHERE status = 'ready' AND active = 1").fetchone()[0]


def step(con, report=lambda msg: None) -> tuple[bool, str]:
    """Does one unit of queue work: a screen or a pack.

    Args:
        con: Database connection.
        report: Called with a short description of the work as it starts.

    Returns:
        (worked, message): whether anything was done, and the queue's state.
    """
    have = queued(con)
    if have >= TARGET:
        return False, f"queue full, {have - TARGET} more in reserve" if have > TARGET else "queue full"
    job = con.execute(CANDIDATES, (MIN_SCORE, RETRY_AFTER)).fetchone()
    if job is None:
        return False, f"{have} of {TARGET} queued; no more jobs score {MIN_SCORE:.0f} or above"
    reason = gpu.busy()
    if reason:
        return False, f"{have} of {TARGET} queued; {reason}"

    if "screened eligible" not in (job["score_notes"] or "") and job["status"] == "new":
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

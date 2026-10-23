"""The background worker that keeps the queue full and turns email into to-dos.

Preparing ahead is a search-side job, so it stops while the person has paused Hal, or while the owner's
Hal is switched off and the Hal add-on respects that. Reading the record folder is the schedule's
`record.sync` job, which calls `sync_record` every `schedule.record_every` minutes.
"""
import logging
import time

from .. import contacts, db, record, replenish, tasks

log = logging.getLogger(__name__)

WORKER_PAUSE = 60  # seconds between queue checks when there is nothing to do
JOB_ID = "find.prepare"


def _held() -> str | None:
    """Why preparing ahead is held back now, or None."""
    from . import scheduler

    if scheduler.paused_for(JOB_ID):
        return "Hal is paused"
    if scheduler.hal_switched_off():
        return "Hal is switched off"
    return None


def step_once(con) -> bool:
    """One round of the worker; True when it did something and should go again at once."""
    tasks.sync(con)
    held = _held()
    worked = False
    if held:
        log.debug("preparing ahead is held: %s", held)
    else:
        worked, msg = replenish.step(con, lambda m: log.debug("preparing ahead: %s", m))
        log.debug("preparing ahead: %s", msg)
    return contacts.step(con) or worked


def sync_record() -> dict:
    """Reads new or changed notes in the record folder; an unreadable folder is reported, not raised."""
    try:
        return record.sync()
    except record.RecordUnavailable as e:
        return {"error": str(e)}


def worker() -> None:
    """Keeps the queue full and the to-do list current, forever."""
    con = db.connect()
    while True:
        worked = False
        try:
            worked = step_once(con)
        except Exception:  # the worker must outlive any one failure
            log.exception("the worker's round failed")
        if not worked:
            time.sleep(WORKER_PAUSE)

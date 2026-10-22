"""The background worker that keeps the queue full and turns email into to-dos (`find.prepare`).

Moved from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface. Reading the
record folder moved to the schedule's `record.sync` job, which calls `sync_record` every
`schedule.record_every` minutes; `RECORD_SYNC` still holds the last result for Classic.

Preparing ahead is a search-side job, so it stops while the person has paused Hal (or while the owner's Hal
is switched off and the Hal add-on respects that), as well as with Classic's own pause.
"""
import logging
import time

from .. import contacts, db, record, replenish, tasks

log = logging.getLogger(__name__)

WORKER_PAUSE = 60  # seconds between queue checks when there is nothing to do
WORKER = {"activity": "starting", "detail": "", "at": db.now(), "paused": False}
RECORD_EVERY = 10 * 60  # seconds; the default of `schedule.record_every`, kept for the legacy interface
RECORD_SYNC: dict = {"at": 0.0, "last": None}
JOB_ID = "find.prepare"


def _held() -> str | None:
    """Why preparing ahead is held back now, or None."""
    from . import scheduler

    if WORKER["paused"]:
        return "queue filling is paused"
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
        WORKER.update(activity="paused", detail=held, at=db.now())
    else:
        worked, msg = replenish.step(con, lambda m: WORKER.update(activity="working", detail=m, at=db.now()))
        WORKER.update(activity="working" if worked else "idle", detail=msg, at=db.now())
    return contacts.step(con) or worked


def sync_record() -> dict:
    """Reads new or changed notes in the record folder; the result is kept for Classic's record page."""
    RECORD_SYNC["at"] = time.time()
    try:
        RECORD_SYNC["last"] = record.sync()
    except record.RecordUnavailable as e:
        RECORD_SYNC["last"] = {"error": str(e)}
    return RECORD_SYNC["last"]


def worker() -> None:
    """Keeps the queue full and the to-do list current, forever."""
    con = db.connect()
    while True:
        worked = False
        try:
            worked = step_once(con)
        except Exception as e:  # the worker must outlive any one failure
            log.exception("the worker's round failed")
            WORKER.update(activity="error", detail=str(e), at=db.now())
        if not worked:
            time.sleep(WORKER_PAUSE)

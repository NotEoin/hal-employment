"""The background worker that keeps the queue full and turns email into to-dos.

Preparing ahead is a search-side job, so it stops while the person has paused Hal, or while the owner's
Hal is switched off and the Hal add-on respects that. Reading the record folder is the schedule's
`record.sync` job, which calls `sync_record` every `schedule.record_every` minutes; that is also when files
put in the drop folder (`record.drop_folder`) are sorted: Hal proposes a place for each, for the person to accept
on Your record, and never reads them straight in (`sections.sorting.actions.queue_inbox`).

The worker keeps one connection for ever, so each round must leave it clean: Python's sqlite3 opens a
transaction before a write and keeps it when the statement fails, and the next round's read then joins it and
holds its snapshot. After one busy moment every write of every later round failed as "database is locked"
(SQLITE_BUSY_SNAPSHOT, which the busy timeout never retries) until Hal restarted (seen 2026-11-19). So the
connection is rolled back after every round, and replaced when even that fails.
"""
import logging
import sqlite3
import time

from jobhunt import db, record, tasks
from jobhunt.docs import contacts
from jobhunt.find import replenish

log = logging.getLogger(__name__)

WORKER_PAUSE = 60  # seconds between queue checks when there is nothing to do
JOB_ID = "find.prepare"


def _held() -> str | None:
    """Why preparing ahead is held back now, or None."""
    from jobhunt.core import scheduler

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
    """Reads new or changed notes in the record folder, and starts sorting the drop folder when it has new files.

    An unreadable folder is reported, not raised.
    """
    from jobhunt.sections.sorting import actions as sort_actions

    try:
        out = record.sync()
    except record.RecordUnavailable as e:
        return {"error": str(e)}
    out["sort_id"] = sort_actions.queue_inbox()
    return out


def _clean(con):
    """Ends any transaction a round left open and returns the connection; a fresh one when that fails."""
    try:
        con.rollback()
        return con
    except sqlite3.Error:
        log.exception("the worker could not roll back; it opens a new connection")
        try:
            con.close()
        except sqlite3.Error:
            log.debug("the old connection would not close either", exc_info=True)
        return db.connect()


def worker() -> None:
    """Keeps the queue full and the to-do list current, forever."""
    con = db.connect()
    while True:
        worked = False
        try:
            worked = step_once(con)
        except Exception:  # the worker must outlive any one failure
            log.exception("the worker's round failed")
        con = _clean(con)  # also after a good round, so no read snapshot outlives it
        if not worked:
            time.sleep(WORKER_PAUSE)

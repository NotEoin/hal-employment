"""The background worker that keeps the queue full, turns email into to-dos and re-reads the employment record.

Moved unchanged from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface.
"""
import time
import traceback

from .. import contacts, db, record, replenish, tasks

WORKER_PAUSE = 60  # seconds between queue checks when there is nothing to do
WORKER = {"activity": "starting", "detail": "", "at": db.now(), "paused": False}
RECORD_EVERY = 10 * 60  # seconds between looks for new or changed notes in the employment record
RECORD_SYNC: dict = {"at": 0.0, "last": None}


def worker() -> None:
    """Keeps the queue full and the to-do list current, forever."""
    con = db.connect()
    while True:
        worked = False
        try:
            tasks.sync(con)
            if WORKER["paused"]:
                WORKER.update(activity="paused", detail="queue filling is paused", at=db.now())
            else:
                worked, msg = replenish.step(
                    con, lambda m: WORKER.update(activity="working", detail=m, at=db.now()))
                WORKER.update(activity="working" if worked else "idle", detail=msg, at=db.now())
            worked = contacts.step(con) or worked
            if time.time() - RECORD_SYNC["at"] >= RECORD_EVERY:
                RECORD_SYNC["at"] = time.time()
                try:
                    RECORD_SYNC["last"] = record.sync()
                except record.RecordUnavailable as e:
                    RECORD_SYNC["last"] = {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - the worker must outlive any one failure
            traceback.print_exc()
            WORKER.update(activity="error", detail=str(e), at=db.now())
        if not worked:
            time.sleep(WORKER_PAUSE)

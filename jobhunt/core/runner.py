"""Background actions for one application at a time: start, stop, and report how each ended.

An action runs on a small thread pool and records its state in `ACTIONS`, which the web interface polls.
Moved unchanged from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface.
"""
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from .. import db

ACTIONS: dict[str, dict] = {}
# Most of an action's time is spent waiting on the browser thread, the model, or the applicant.
POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="action")


class Stopped(BaseException):
    """The action's application was discarded, marked applied or started again while the action ran.

    A BaseException, so the many `except Exception` blocks on the way (a failed
    search, a tab that moved on) let it through to `_carry_out`.
    """


# The action, by job, that this thread is carrying out. An action whose entry in ACTIONS has
# been replaced or removed is stopped at its next checkpoint.
_RUN = threading.local()


def _checkpoint(job_id: str) -> None:
    """Raises `Stopped` in an action whose application no longer wants it; a no-op in any other thread."""
    mine = getattr(_RUN, "actions", {}).get(job_id)
    if mine is not None and ACTIONS.get(job_id) is not mine:
        raise Stopped()


def stop_action(job_id: str) -> bool:
    """Lets go of the action running for a job at once; it stops itself at its next checkpoint.

    The browser or model call in hand runs to its end in the background, but
    nothing it returns is written. Returns True when an action was running.
    """
    current = ACTIONS.get(job_id)
    if not current or current["state"] != "running":
        return False
    ACTIONS.pop(job_id, None)
    return True


def run_action(job_id: str, name: str, fn, *args, replace: bool = False) -> dict:
    """Starts fn(con, *args) in the background.

    Args:
        replace: Stop an action already running for the job in favour of this one, rather than refuse.
    """
    current = ACTIONS.get(job_id)
    if current and current["state"] == "running":
        if not replace:
            raise RuntimeError(f"Hal is still busy with '{current['name']}' for this job")
        stop_action(job_id)
    ACTIONS[job_id] = {"name": name, "state": "running", "message": "", "at": db.now()}

    def wrapper():
        con = db.connect()
        try:
            _carry_out(con, job_id, fn, *args)
        finally:
            con.close()

    POOL.submit(wrapper)
    return ACTIONS[job_id]


def _carry_out(con, job_id: str, fn, *args) -> None:
    """Runs an action already recorded as running, and records how it ended.

    An action stopped part way leaves no trace: whatever stopped it has already
    said where the application stands.
    """
    mine = ACTIONS[job_id]
    if not hasattr(_RUN, "actions"):
        _RUN.actions = {}
    _RUN.actions[job_id] = mine
    try:
        msg = fn(con, *args) or "done"
        _checkpoint(job_id)
        mine.update(state="done", message=msg, at=db.now())
    except Stopped:
        mine.update(state="stopped", message="stopped", at=db.now())
    except Exception as e:  # noqa: BLE001 - shown to the user, who can retry
        if ACTIONS.get(job_id) is not mine:
            mine.update(state="stopped", message="stopped", at=db.now())
            return
        if not isinstance(e, RuntimeError):
            traceback.print_exc()
        mine.update(state="error", message=f"{e}", at=db.now())
        _note(con, job_id, "stuck", f"{e}")
    finally:
        _RUN.actions.pop(job_id, None)


def _say(job_id: str, text: str) -> None:
    """Shows what Hal is doing for a job, on its page and in the queue."""
    _checkpoint(job_id)
    if job_id in ACTIONS:
        ACTIONS[job_id]["message"] = text


def _note(con, job_id: str, state: str, message: str) -> None:
    """Records where an application stands, for its page: working, ready, needs, stuck or sent."""
    try:
        form = _app(con, job_id)["form"]
        if form is not None:
            form.update(state=state, message=message)
            _set_app(con, job_id, form=form)
    except Exception:  # noqa: BLE001 - the action's own message still reaches the page
        pass


from ..apply.queries import _app, _set_app  # noqa: E402 - imported last; queries imports this module

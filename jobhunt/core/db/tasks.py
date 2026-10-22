"""Data tasks: slow or file-system work that runs once, after a given migration.

Migrations only change the database. Work such as re-laying-out application folders or re-scoring every
job is a data task instead: `@data_task(name, after=N, title=...)` registers a function that the runner
starts as a persisted background action once migration N has applied. State lives in the
`data_tasks` table, so a task that failed is retried at the next start and shown in "Is Hal working?".

The decorator, the registry and the state helpers came first; the runner below registers each
task as an action kind of the same name (area system, priority 2) and runs the due ones one after another
in migration order, on one background thread, because a later task may rely on an earlier one. Tasks are
found in modules named `tasks.py`, `*_tasks.py`, `actions.py` or `*_actions.py`, and in the modules
listed outside that pattern (`TASK_MODULES`); the old to-do list `jobhunt.tasks` shares the name and is skipped.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jobhunt.core.db.migrate import LATEST, iso, user_version

log = logging.getLogger(__name__)

STATES = ("pending", "running", "done", "error")
DONE = "done"
WHY = "Hal is updating its records after an update"
NOT_TASK_MODULES = {"jobhunt.tasks"}    # the to-do list, older than data tasks
TASK_MODULES = ("jobhunt.core.answers_rescope",)   # `answers.rescope` lives beside the import it serves


@dataclass(frozen=True)
class DataTask:
    """One registered data task."""

    name: str        # "answers.rescope"
    after: int       # the migration it needs
    title: str       # plain words for Activity: "Sorted your saved answers by where they apply"
    fn: Callable[[Any], None]   # fn(ctx); ctx writes Activity rows and steps (the runner)


TASKS: dict[str, DataTask] = {}


def data_task(name: str, *, after: int, title: str) -> Callable:
    """Registers fn(ctx) to run once, as a persisted background action, after migration `after` has
    applied. State lives in the data_tasks table; a failed task is retried at the next start and shown
    in "Is Hal working?". Each task writes Activity rows through its ctx."""
    if not 1 <= after <= LATEST:
        raise ValueError(f"data task {name!r}: after={after} is not a migration (1 to {LATEST})")
    if not title.strip():
        raise ValueError(f"data task {name!r} needs a title")

    def register(fn: Callable[[Any], None]) -> Callable[[Any], None]:
        if name in TASKS and TASKS[name].fn is not fn:
            raise ValueError(f"data task {name!r} is registered twice")
        TASKS[name] = DataTask(name, after, title, fn)
        return fn

    return register


def state(con: sqlite3.Connection, name: str) -> str | None:
    """The stored state of a task, or None when it has never been started."""
    row = con.execute("SELECT state FROM data_tasks WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def due(con: sqlite3.Connection) -> list[DataTask]:
    """Registered tasks whose migration has applied and that are not done, in migration order."""
    version = user_version(con)
    return sorted((t for t in TASKS.values() if t.after <= version and state(con, t.name) != DONE),
                  key=lambda t: (t.after, t.name))


def mark(con: sqlite3.Connection, task: DataTask, new_state: str, detail: str | None = None) -> None:
    """Writes a task's state with its start or end time; the caller commits."""
    if new_state not in STATES:
        raise ValueError(f"unknown data task state {new_state!r}; one of {', '.join(STATES)}")
    now = iso()
    started = now if new_state == "running" else None
    ended = now if new_state in (DONE, "error") else None
    con.execute(
        """INSERT INTO data_tasks (name, after, state, started, ended, detail) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET state = excluded.state, detail = excluded.detail,
               started = coalesce(excluded.started, data_tasks.started),
               ended = CASE WHEN excluded.state = 'running' THEN NULL ELSE coalesce(excluded.ended, ended) END""",
        (task.name, task.after, new_state, started, ended, detail))


# ---------------------------------------------------------------------------------------------- runner

def load() -> list[DataTask]:
    """Imports every module that may register data tasks and makes each task an action kind."""
    import importlib
    import importlib.util

    from jobhunt.core import plugins

    extra = [m for m in TASK_MODULES if importlib.util.find_spec(m) is not None]
    for name in dict.fromkeys([*plugins.names("tasks"), *plugins.names("actions"), *extra]):
        if name in NOT_TASK_MODULES or name == __name__:
            continue
        try:
            importlib.import_module(name)
        except Exception as e:  # kept for the health check, as plugins.modules does
            plugins.FAILED[name] = f"{type(e).__name__}: {e}"
            log.exception("could not import %s", name)
    for task in TASKS.values():
        _as_kind(task)
    return list(TASKS.values())


def _as_kind(task: DataTask) -> None:
    from jobhunt.core import runner

    if task.name in runner.KINDS:
        return

    def run_task(ctx) -> str:
        ctx.step(task.title)
        _write_state(task, "running")
        try:
            task.fn(ctx)
        except BaseException as e:
            _write_state(task, "error", f"{type(e).__name__}: {e}")
            raise
        _write_state(task, DONE)
        return task.title

    runner.action(task.name, area="system", label=task.title, why=WHY, priority=2, exclusive="subject",
                  example=lambda world: {})(run_task)


def _write_state(task: DataTask, new_state: str, detail: str | None = None) -> None:
    """Writes a task's state on a connection of its own, so it holds even when the task's work rolls back."""
    from jobhunt import db

    con = db.connect()
    try:
        mark(con, task, new_state, detail)
        con.commit()
    finally:
        con.close()


def run_due(con: sqlite3.Connection | None = None) -> list[dict]:
    """Runs every due task now, in this thread, in migration order; returns each run as `runner.get` does.

    A task that fails is left in state error and tried again at the next call; the ones after it still run.
    """
    from jobhunt import db
    from jobhunt.core import runner

    load()
    own = con is None
    con = con or db.connect()
    try:
        due_now = due(con)
    finally:
        if own:
            con.close()
    out = []
    for task in due_now:
        try:
            out.append(runner.run(task.name, subject=f"_data:{task.name}"))
        except RuntimeError as e:
            log.warning("data task %s did not start: %s", task.name, e)
    return out


def run_now(name: str) -> dict:
    """Runs one task now in this thread, whether or not it ran before (the importers call it)."""
    from jobhunt.core import runner

    load()
    if name not in TASKS:
        raise LookupError(f"there is no data task {name!r}")
    return runner.run(name, subject=f"_data:{name}")


def start_due() -> threading.Thread:
    """Runs the due tasks on a background thread (server start); returns the thread."""
    thread = threading.Thread(target=_run_due_logged, name="data-tasks", daemon=True)
    thread.start()
    return thread


def _run_due_logged() -> None:
    try:
        for done in run_due():
            if done["state"] != "done":
                log.warning("data task %s ended %s: %s", done["kind"], done["state"], done["error"] or "")
    except Exception:  # logged; the tasks are tried again at the next start
        log.exception("the data tasks could not run")

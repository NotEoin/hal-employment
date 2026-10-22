"""Data tasks: slow or file-system work that runs once, after a given migration.

Migrations only change the database. Work such as re-laying-out application folders or re-scoring every
job is a data task instead: `@data_task(name, after=N, title=...)` registers a function that the runner
starts as a persisted background action once migration N has applied. State lives in the
`data_tasks` table, so a task that failed is retried at the next start and shown in "Is Hal working?".

This module holds the decorator, the registry and the state helpers; it never runs a task itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jobhunt.core.db.migrate import LATEST, iso, user_version

STATES = ("pending", "running", "done", "error")
DONE = "done"


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

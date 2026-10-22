"""A stand-in for the runner's `runner.action` contract, so find's action kinds can be tested without it.

`install()` adds `action` and `KINDS` to `jobhunt.core.runner` when the real ones are not there yet, and
`Ctx` records steps and changes in memory. Once the runner exists, `install()` does nothing and the real
runner is used.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

KINDS: dict[str, dict] = {}


class Stopped(BaseException):
    """Raised by `Ctx.checkpoint` after `stop()`, as the real runner does."""


def action(kind: str, **meta):
    """Registers fn(ctx, **params) as action kind `kind`, keeping its metadata."""
    def register(fn):
        KINDS[kind] = {"kind": kind, "fn": fn, **meta}
        return fn
    return register


@dataclass
class Ctx:
    """The parts of the runner's `Ctx` the find actions use."""

    con: sqlite3.Connection
    kind: str = "test"
    job_id: str | None = None
    priority: int = 2
    steps: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    stopped: bool = False

    def step(self, text: str, *, why: str | None = None, used: str | None = None, total: int | None = None) -> None:
        self.steps.append(text)

    def changed(self, words: str) -> None:
        self.changes.append(words)

    def checkpoint(self) -> None:
        if self.stopped:
            raise Stopped()


def install() -> None:
    """Puts the stand-in on `jobhunt.core.runner` when the real `action` has not merged yet."""
    from jobhunt.core import runner

    if not hasattr(runner, "action"):
        runner.action = action
        runner.KINDS = KINDS

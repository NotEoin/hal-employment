"""Documents written from the record as it is: wait for a record update, and write again when it moves.

A document write reads the record (the cards, the CV, the search index). `written(write, ctx)` makes the
write wait while a record update is queued or running (`runner.busy_with(work.RECORD)`), with the step
`work.RECORD_WAIT_WORDS` and the action's Stop working, then runs `write(fresh)` with the record's revision.
The write calls `fresh.check()` before it saves anything: when the revision moved while it wrote, that raises
`RecordMoved`, and the write starts again once the update is done, so it never saves documents written from a
record that has already changed.

Design decisions, each with its reason:

- At most `MAX_RESTARTS` restarts, then the last write saves whatever it read: a record that keeps changing
  (a long bulk read finishing source by source) would otherwise hold the documents back for ever. That last
  write keeps the revision it started from (`documents.written_from`), so its documents show out of date.
- An update in another process (a bulk read from the command line) counts too: the runner reads the actions
  rows of live processes. A wait looks again at least every `WAIT_POLL_S`, as an action ending in another
  process wakes nothing here.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from jobhunt import record
from jobhunt.core import runner, work

log = logging.getLogger(__name__)

MAX_RESTARTS = 2     # a pack is about 30 model calls, so a third full write is the most one record change costs
WAIT_POLL_S = 0.5    # how often a wait looks again for an update in another process
AGAIN_WORDS = "Your record changed while Hal wrote, so Hal writes again from it"
T = TypeVar("T")


class RecordMoved(Exception):
    """The record changed while a document was being written from it."""


class Fresh:
    """The record's revision a write started from; `check` says whether it still holds."""

    def __init__(self, revision: int, *, last: bool) -> None:
        self.revision, self.last = revision, last

    def check(self) -> None:
        """Raises `RecordMoved` when the record changed since the write started; the last try never raises."""
        if not self.last and record.revision() != self.revision:
            raise RecordMoved(f"the record moved from revision {self.revision}")


def record_busy() -> bool:
    """True while a record update is queued or running, here or in another live process."""
    return runner.busy_with(work.RECORD)


def settled(ctx: Any = None, *, then: str | None = None) -> int:
    """Waits while a record update is queued or running, then returns the record's revision.

    In an action (`ctx`) the wait is a step and Stop ends it (`ctx.checkpoint`); `then` is the step taken once
    the wait is over. Outside one it simply waits.
    """
    if record_busy():
        if ctx is not None:
            ctx.step(work.RECORD_WAIT_WORDS)
        while record_busy():
            if ctx is not None:
                ctx.checkpoint()
            runner.wait_for_an_end(WAIT_POLL_S)
        if ctx is not None and then:
            ctx.step(then)
    runner.flush()
    return record.revision()


def written(write: Callable[[Fresh], T], ctx: Any = None, *, then: str | None = None) -> tuple[T, int]:
    """Runs `write(fresh)` on a settled record, again after each change it saw; returns (its result, the
    revision it was written from)."""
    for attempt in range(MAX_RESTARTS):
        fresh = Fresh(settled(ctx, then=then), last=False)
        try:
            return write(fresh), fresh.revision
        except RecordMoved as e:
            log.info("a document write starts again (%d of %d): %s", attempt + 1, MAX_RESTARTS, e)
            if ctx is not None:
                ctx.step(AGAIN_WORDS)
    fresh = Fresh(settled(ctx, then=then), last=True)
    return write(fresh), fresh.revision

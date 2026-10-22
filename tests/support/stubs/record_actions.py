"""Stand-ins for the record's `record.sync` and `record.ask` action kinds, which applications start.

Classic's "Read it now" and "Ask your record" start these kinds. The contract assumed here:
`record.sync` takes no params and returns what `record.sync()` returns; `record.ask` takes `q` and returns
`{answer, sources}`. `installed(delay=...)` registers them for a `with` block, unless the real kinds are
already registered.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator

from jobhunt.core import runner

SYNCED = {"added": 1, "changed": 0, "removed": 0}


@contextlib.contextmanager
def installed(delay: float = 0.0) -> Iterator[None]:
    """Registers the two kinds, each waiting `delay` seconds as a slow record would, for the block."""
    runner.load_kinds()
    mine = [k for k in ("record.sync", "record.ask") if k not in runner.KINDS]

    def sync(ctx) -> dict:
        ctx.step("Reading the made-up record")
        time.sleep(delay)
        return dict(SYNCED)

    def ask(ctx, q: str) -> dict:
        ctx.step(f"Looking through the made-up record for: {q}")
        time.sleep(delay)
        return {"answer": "A made-up answer.", "sources": ["notes.md"]}

    if "record.sync" in mine:
        runner.action("record.sync", area="documents", label="Reading your record",
                      why="New or changed files in your record", example=lambda w: {})(sync)
    if "record.ask" in mine:
        runner.action("record.ask", area="documents", label="Asking your record", why="You asked: {q}",
                      example=lambda w: {"q": "Where did I volunteer?"})(ask)
    try:
        yield
    finally:
        for kind in mine:
            runner.forget(kind)

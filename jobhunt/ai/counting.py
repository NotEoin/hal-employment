"""Counts the AI calls made inside a block, so a caller can log how many one piece of work took.

A pack made about 55 calls before its drafts were told one paragraph per piece of work (2026-11-08), and the
count per pack is how the change is measured. The counter is a context variable, so it counts this thread's
calls only, and blocks nest.
"""
import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

__all__ = ["Calls", "count", "counted"]


@dataclass
class Calls:
    """How many AI calls a `counted` block has made so far."""

    n: int = 0


_COUNTING: contextvars.ContextVar[tuple[Calls, ...]] = contextvars.ContextVar("ai_counting", default=())


@contextmanager
def counted() -> Iterator[Calls]:
    """Counts the calls `ai.chat` and `ai.chat_json` make inside the block; an outer block counts an inner one's too."""
    calls = Calls()
    token = _COUNTING.set((*_COUNTING.get(), calls))
    try:
        yield calls
    finally:
        _COUNTING.reset(token)


def count() -> None:
    """Adds one call to every open `counted` block."""
    for calls in _COUNTING.get():
        calls.n += 1

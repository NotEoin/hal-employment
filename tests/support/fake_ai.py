"""The fake AI for tests: `FakeBackend` from `jobhunt.ai.fake`, and `fake_ai()` to put one in place.

Until the AI layer provides `jobhunt.ai.use`, `fake_ai()` only hands the backend over, because no code
calls the new interface yet; afterwards it installs the backend for the block. Every request the code makes
is in `backend.log`, so a test can check what reached the AI (gate G5).
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator

from jobhunt import ai
from jobhunt.ai.fake import FakeBackend

__all__ = ["FakeBackend", "fake_ai"]


@contextlib.contextmanager
def fake_ai(replies: dict | None = None, default: str = "{}") -> Iterator[FakeBackend]:
    """A `FakeBackend` with `replies`, installed while the block runs when the AI layer can take it."""
    backend = FakeBackend(replies, default)
    use = getattr(ai, "use", None)
    if use is None:
        yield backend
        return
    with use(backend):
        yield backend

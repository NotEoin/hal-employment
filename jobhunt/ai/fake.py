"""The fake AI backend for tests: canned replies by purpose or prompt, and a log of every request.

`FakeBackend(replies)` answers a request whose `purpose` is a key of `replies`, else the first key that is a
regular expression matching the system or user prompt (keys starting with `re:`), else `default`. A reply
may be a string, a function of the request, or an exception (instance or class) to raise, so a test can make
the AI unavailable or limited on demand. Accepted as a provider only with HAL_TEST_MODE=1.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable

from .base import ChatRequest, ChatResult, LLMUnavailable, Status

Reply = str | Callable[[ChatRequest], str] | BaseException | type
REGEX_PREFIX = "re:"


class FakeBackend:
    """A backend that never leaves the process."""

    name = "fake"
    online = False
    local_gpu = False

    def __init__(self, replies: dict[str, Reply] | None = None, default: Reply = "{}") -> None:
        self.replies = dict(replies or {})
        self.default = default
        self.log: list[ChatRequest] = []
        self.ready = True
        self._lock = threading.Lock()

    def _pick(self, req: ChatRequest) -> Reply:
        if req.purpose and req.purpose in self.replies:
            return self.replies[req.purpose]
        for key, reply in self.replies.items():
            if key.startswith(REGEX_PREFIX) and re.search(key[len(REGEX_PREFIX):], f"{req.system}\n{req.user}"):
                return reply
        return self.default

    def chat(self, req: ChatRequest, model: str) -> ChatResult:
        """Records the request and answers it from `replies`."""
        start = time.monotonic()
        with self._lock:
            self.log.append(req)
        reply = self._pick(req)
        if isinstance(reply, BaseException) or (isinstance(reply, type) and issubclass(reply, BaseException)):
            raise reply
        text = reply(req) if callable(reply) else reply
        return ChatResult(text=text, provider=self.name, model=model, ms=int((time.monotonic() - start) * 1000))

    def status(self) -> Status:
        """Ready unless a test set `ready = False`."""
        return Status(ready=True) if self.ready else Status(ready=False, why="The made-up AI is switched off.")

    def models(self) -> list[str]:
        """One made-up model."""
        return ["fake-model"]

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """A tiny stable vector per text, so search tests have something to rank."""
        if not self.ready:
            raise LLMUnavailable("The made-up AI is switched off.")
        return [[float(len(t)), float(sum(map(ord, t)) % 97)] for t in texts]

    def requests(self, purpose: str | None = None) -> list[ChatRequest]:
        """The requests seen so far, optionally only those for `purpose`."""
        with self._lock:
            return [r for r in self.log if purpose is None or r.purpose == purpose]

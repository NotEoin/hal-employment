"""Hal's AI: one way to ask, whichever backend the person chose.

`chat` runs, in order: the private-answer guard; the allowance (`budget.admit`); the GPU gate
for backends on this computer's card; the backend call, at most `ai.parallel` at once with lower
priority numbers served first; and one `ai_usage` row with sizes only. A failure raises `LLMUnavailable`
with a `kind` and plain words, and callers fall back to rules as they always have.

The backend comes from `ai.provider` (`provider.current`, which never changes by itself) and is rebuilt
when any setting changes. In a test run it is a made-up AI that is never ready, unless a test installs one
with `use(...)` or `HAL_TEST_REAL_AI` asks for a real model, so no test reaches a real model by accident.
Importing the package registers a start task that stores the suggested `ai.provider` once when nobody has
chosen one, so a person who skipped setup gets a fixed choice the first time Hal starts.
"""
from __future__ import annotations

import contextlib
import heapq
import itertools
import json
import logging
import os
import re
import threading
from collections.abc import Iterator

from ..core import plugins
from . import budget, gpu, guard
from .base import Backend, ChatRequest, ChatResult, Limited, LLMUnavailable, PrivateLeak, Role, Status, role_for
from .budget import at_priority, current_priority

__all__ = [
    "Backend",
    "ChatRequest",
    "ChatResult",
    "LLMUnavailable",
    "Limited",
    "PrivateLeak",
    "Role",
    "Status",
    "at_priority",
    "backend",
    "chat",
    "chat_json",
    "current_priority",
    "model_for",
    "ready",
    "use",
]

log = logging.getLogger(__name__)

TEST_ENV = "HAL_TEST_MODE"
REAL_AI_ENV = "HAL_TEST_REAL_AI"
OLD_ROLE_ENV = {"checker": "MAIN", "writer": "WRITER", "quick": "FAST"}   # JOBHUNT_<ROLE>_MODEL, as `llm.py` read it
FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.S)
SECRETS_MODULE = "jobhunt.core.settings.secrets"
NOT_IN_TESTS = "The made-up AI in the tests has no answer for this."

_override: list[Backend] = []
_override_lock = threading.Lock()
_built: dict = {}


def _test_backend() -> Backend:
    from .fake import FakeBackend

    if "test" not in _built:
        fake = FakeBackend(default=LLMUnavailable(NOT_IN_TESTS))
        fake.ready = False
        _built["test"] = fake
    return _built["test"]


def _settings():
    from ..core import settings

    return settings


def _build(name: str) -> Backend:
    settings = _settings()
    if name == "local":
        from .ollama import OllamaBackend

        return OllamaBackend(settings.get("ai.ollama_url"))
    if name == "openai_compat":
        from .openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(settings.get("ai.openai_url"), _openai_key())
    if name == "claude_cli":
        from .claude_cli import ClaudeCLIBackend

        return ClaudeCLIBackend()
    if name == "fake" and os.environ.get(TEST_ENV) == "1":
        return _test_backend()
    raise LLMUnavailable(f"Hal does not know the AI {name!r}. Choose one in Settings, Hal's brain.")


def _openai_key() -> str | None:
    import importlib

    try:
        secrets = importlib.import_module(SECRETS_MODULE)
    except ModuleNotFoundError as e:
        if e.name != SECRETS_MODULE:
            raise
        return None   # a build without the secrets store has no key; a local server rarely needs one
    return secrets.get("ai.openai_key", interactive=False)


def backend() -> Backend:
    """The backend in use: a test's `use(...)`, else the one `ai.provider` names, rebuilt after any setting change."""
    with _override_lock:
        if _override:
            return _override[-1]
    if os.environ.get(TEST_ENV) == "1" and not os.environ.get(REAL_AI_ENV):
        return _test_backend()
    from . import provider

    name = provider.current()
    key = (name, _settings().revision())
    if _built.get("key") != key:
        _built["backend"], _built["key"] = _build(name), key
    return _built["backend"]


@contextlib.contextmanager
def use(be: Backend) -> Iterator[Backend]:
    """Makes `be` the backend for every call while the block runs (tests)."""
    with _override_lock:
        _override.append(be)
    try:
        yield be
    finally:
        with _override_lock:
            _override.remove(be)


def model_for(be: Backend, role: str) -> str:
    """The model for `role`: the setting, else the backend's own choice for that role."""
    role = role_for(role)
    old = OLD_ROLE_ENV.get(role)
    chosen = os.environ.get(f"JOBHUNT_{old}_MODEL", "") if old else ""
    chosen = chosen or str(_settings().get(f"ai.model.{role}") or "")
    if chosen and (be.name != "claude_cli" or chosen in be.models()):
        return chosen
    pick = getattr(be, "default_model", None)
    if pick is not None:
        return pick(role)
    found = be.models()
    if not found:
        raise LLMUnavailable("There is no model to use yet.")
    return found[0]


class _Turns:
    """At most `limit` calls at once; a waiting call with a lower priority number goes first."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._running = 0
        self._queue: list[tuple[int, int]] = []
        self._seq = itertools.count()

    @contextlib.contextmanager
    def turn(self, priority: int, limit: int, timeout: float) -> Iterator[None]:
        me = (priority, next(self._seq))
        with self._cond:
            heapq.heappush(self._queue, me)
            ok = self._cond.wait_for(lambda: self._running < limit and self._queue[0] == me, timeout=timeout)
            if not ok:
                self._queue.remove(me)
                heapq.heapify(self._queue)
                self._cond.notify_all()
                raise LLMUnavailable("Hal's AI was busy with other work for too long. Try again.", kind="timeout")
            heapq.heappop(self._queue)
            self._running += 1
            self._cond.notify_all()
        try:
            yield
        finally:
            with self._cond:
                self._running -= 1
                self._cond.notify_all()


_turns = _Turns()


def _record(outcome, req: ChatRequest, be: Backend, model: str, chars_out: int) -> None:
    try:
        budget.record(outcome, priority=req.priority, purpose=req.purpose, chars_in=len(req.system) + len(req.user),
                      chars_out=chars_out, provider=be.name, model=model, role=req.role, job_id=req.job_id)
    except Exception:
        log.exception("could not record an AI call")


def chat(system: str, user: str, *, role: str = "checker", json_out: bool = False, timeout: float | None = None,
         ctx: int | None = None, purpose: str = "", priority: int | None = None, job_id: str | None = None) -> str:
    """Asks the AI and returns its reply; raises `LLMUnavailable` when it cannot answer now."""
    settings = _settings()
    req = ChatRequest(system=system, user=user, role=role_for(role), json_out=json_out,
                      timeout=float(timeout or settings.get("ai.timeout")), ctx=ctx, purpose=purpose,
                      priority=current_priority() if priority is None else priority, job_id=job_id)
    guard.check(req)
    be = backend()
    budget.admit(req.priority, purpose, be)
    if be.local_gpu and gpu.gate_on():
        gpu.wait(req.priority)
    model = model_for(be, req.role)
    with _turns.turn(req.priority, int(settings.get("ai.parallel")), req.timeout):
        try:
            result = be.chat(req, model)
        except LLMUnavailable as e:
            _record(e, req, be, model, 0)
            raise
    _record(result, req, be, model, len(result.text))
    return result.text


def parse_json(text: str) -> dict:
    """The JSON object in a reply, with any code fence around it; {} when there is none."""
    body = text.strip()
    fenced = FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        out = json.loads(body)
    except ValueError:
        found = re.search(r"\{.*\}", body, re.S)
        if not found:
            return {}
        try:
            out = json.loads(found.group(0))
        except ValueError:
            return {}
    return out if isinstance(out, dict) else {}


def chat_json(system: str, user: str, **kw) -> dict:
    """Asks for JSON and returns the object; a reply that is not JSON gives {} rather than an error."""
    return parse_json(chat(system, user, json_out=True, **kw))


def ready(priority: int = budget.BACKGROUND, purpose: str = "") -> str | None:
    """None when a call at `priority` could run now, else why not, in plain words."""
    be = backend()
    try:
        budget.admit(priority, purpose, be)
    except LLMUnavailable as e:
        return str(e)
    if be.local_gpu and gpu.gate_on():
        found = gpu.holders()
        if found:
            return f"Waiting for the graphics card: {', '.join(n for n, _ in found)} is using it"
    status = be.status()
    return None if status.ready else (status.why or "Hal's AI is not available.")


@plugins.on_start
def choose_ai_once() -> None:
    """Stores the suggested `ai.provider` once when nobody has chosen one."""
    from . import provider

    log.info("Hal's AI: %s", provider.current())

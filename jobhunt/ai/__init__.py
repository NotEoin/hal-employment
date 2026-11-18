"""Hal's AI: one way to ask, whichever backend the person chose.

`chat` runs, in order: the private-answer guard; the allowance (`budget.admit`); the GPU gate
for backends on this computer's card; the backend call, at most `at_once` at once (the slots the backend says its
model serves, else `ai.parallel`) with lower work classes served first (`core.work`: on screen, record updates,
document writes, the rest), first come first served within a class; and one `ai_usage` row with sizes only.

A call's class is the one entered around it (the runner enters its action's class), else its explicit
`priority` mapped by `work.for_priority`. The allowance and the GPU claim still go by the priority alone
(`budget.current_priority`, 2 inside actions as before): the class only orders the queue, so moving a document
write behind a record update never lets it past the Claude daily cap or the keep-free hours.

A failure raises `LLMUnavailable` with a `kind` and plain words, and callers fall back to rules as they always
have.

`embed` gives vectors of meaning from the AI on this computer only (Ollama, Hal's gateway): a backend that sends
text elsewhere (`online`) is never asked, as the record's search by meaning always kept the person's words
here. Each batch takes a turn in the same queue as `chat`, in the caller's work class, so a document write's
vectors wait behind the person's own requests like its chats do; the record's index and search opt out, as
before. It skips the allowance and the GPU gate, as the record's vectors always did: an embedding model is
small and costs no allowance.

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
import math
import os
import re
import threading
from collections.abc import Iterator

from jobhunt.ai import budget, gpu, guard
from jobhunt.ai.base import (
    Backend,
    ChatRequest,
    ChatResult,
    Limited,
    LLMUnavailable,
    PrivateLeak,
    Role,
    Status,
    role_for,
)
from jobhunt.ai.budget import at_priority, current_priority
from jobhunt.ai.counting import Calls, count, counted
from jobhunt.core import plugins, work

__all__ = [
    "Backend",
    "Calls",
    "ChatRequest",
    "ChatResult",
    "LONG_TIMEOUT_S",
    "LLMUnavailable",
    "Limited",
    "PrivateLeak",
    "Role",
    "Status",
    "at_priority",
    "available",
    "backend",
    "chat",
    "chat_json",
    "cosine",
    "counted",
    "current_priority",
    "embed",
    "embed_model",
    "model_for",
    "ready",
    "undash",
    "use",
]

log = logging.getLogger(__name__)

TEST_ENV = "HAL_TEST_MODE"
REAL_AI_ENV = "HAL_TEST_REAL_AI"
OLD_ROLE_ENV = {"checker": "MAIN", "writer": "WRITER", "quick": "FAST"}   # JOBHUNT_<ROLE>_MODEL, as `llm.py` read it
FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.S)
SECRETS_MODULE = "jobhunt.core.settings.secrets"
LONG_TIMEOUT_S = 180   # seconds; what the callers that moved from the old `llm.chat` have always waited
NOT_IN_TESTS = "The made-up AI in the tests has no answer for this."
EMBED_MODEL_DEFAULT = "nomic-embed-text"   # the embedding model Ollama's docs suggest; `ai.model.embed` overrides
EMBED_BATCH = 32                           # texts per request, as the record's index always sent them
ONLINE_EMBED = "Vectors of meaning come only from the AI on this computer."

_override: list[Backend] = []
_override_lock = threading.Lock()
_built: dict = {}


def _test_backend() -> Backend:
    from jobhunt.ai.fake import FakeBackend

    if "test" not in _built:
        fake = FakeBackend(default=LLMUnavailable(NOT_IN_TESTS))
        fake.ready = False
        _built["test"] = fake
    return _built["test"]


def _settings():
    from jobhunt.core import settings

    return settings


def _build(name: str) -> Backend:
    settings = _settings()
    if name == "local":
        from jobhunt.ai.ollama import OllamaBackend

        return OllamaBackend(settings.get("ai.ollama_url"))
    if name == "openai_compat":
        from jobhunt.ai.openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(settings.get("ai.openai_url"), _openai_key())
    if name == "hal":
        from jobhunt.ai.hal_gateway import GatewayBackend

        return GatewayBackend(settings.get("ai.hal_url"), settings.get("ai.hal_token_file"),
                              settings.get("ai.hal_batch_token_file"))
    if name == "claude_cli":
        from jobhunt.ai.claude_cli import ClaudeCLIBackend

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
    from jobhunt.ai import provider

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
        raise LLMUnavailable("There is no AI to use yet.")
    return found[0]


def at_once(role: str = "checker") -> int:
    """How many calls for `role` go to the AI at once: the slots the backend says its model serves (Hal's gateway
    reports its writer's `parallel`), else `ai.parallel`. More calls than slots only queue at the model, and the
    wait counts against each call's timeout: four calls on Hal's two-slot writer timed out after 300 s
    (2026-11-12)."""
    chosen = max(1, int(_settings().get("ai.parallel")))
    be = backend()
    slots = getattr(be, "slots", None)
    if slots is None:
        return chosen
    try:
        return int(slots(model_for(be, role))) or chosen
    except LLMUnavailable:
        return chosen


class _Turns:
    """At most `limit` calls at once; a waiting call of a lower work class goes first, then the one that came
    first."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._running = 0
        self._queue: list[tuple[int, int]] = []
        self._seq = itertools.count()

    @contextlib.contextmanager
    def turn(self, cls: int, limit: int, timeout: float) -> Iterator[None]:
        me = (cls, next(self._seq))
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
         ctx: int | None = None, purpose: str = "", priority: int | None = None, job_id: str | None = None,
         temperature: float | None = None) -> str:
    """Asks the AI and returns its reply; raises `LLMUnavailable` when it cannot answer now. `temperature`
    overrides the backend's own for this request."""
    count()
    settings = _settings()
    req = ChatRequest(system=system, user=user, role=role_for(role), json_out=json_out,
                      timeout=float(timeout or settings.get("ai.timeout")), ctx=ctx, purpose=purpose,
                      priority=current_priority() if priority is None else priority, job_id=job_id,
                      temperature=temperature)
    guard.check(req)
    be = backend()
    budget.admit(req.priority, purpose, be)
    if be.local_gpu and gpu.gate_on():
        gpu.wait(req.priority)
    model = model_for(be, req.role)
    cls = work.of_call(req.priority)
    with _turns.turn(cls, at_once(req.role), req.timeout), \
            (gpu.claim(req.priority) if be.local_gpu else contextlib.nullcontext()):
        try:
            result = be.chat(req, model)
        except LLMUnavailable as e:
            _record(e, req, be, model, 0)
            raise
    _record(result, req, be, model, len(result.text))
    return result.text


def embed_model() -> str:
    """The embedding model: `ai.model.embed`, else `EMBED_MODEL_DEFAULT`."""
    return str(_settings().get("ai.model.embed") or EMBED_MODEL_DEFAULT)


def embed(texts: list[str], *, model: str | None = None, timeout: float | None = None,
          queued: bool = True) -> list[list[float]]:
    """One vector per text from the AI on this computer, in `EMBED_BATCH`es, each queued like a chat.

    `queued` False skips the queue, for the record's own index and search, which never waited behind a chat
    and would make a search on screen wait for a long draft on a one-slot model. `LLMUnavailable` when the
    backend sends text elsewhere (`online`), the model is missing or does not answer, or the queue keeps the
    call waiting longer than `timeout` (default `ai.timeout`).
    """
    if not texts:
        return []
    be = backend()
    if getattr(be, "online", True):
        raise LLMUnavailable(ONLINE_EMBED)
    model = model or embed_model()
    wait = float(timeout or _settings().get("ai.timeout"))
    cls = work.of_call(current_priority())
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        with _turns.turn(cls, at_once(), wait) if queued else contextlib.nullcontext():
            out += be.embed(texts[i:i + EMBED_BATCH], model)
    if len(out) != len(texts):
        raise LLMUnavailable("The AI on this computer sent the wrong number of vectors.")
    return out


def cosine(a: list[float], b: list[float]) -> float:
    """How alike two vectors point, from -1 to 1; 0 when either is all zeros."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


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


def undash(text: str) -> str:
    """Rewrites the long dashes models favour: a hyphen in number ranges, a comma elsewhere."""
    text = re.sub(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)", "-", text)
    return re.sub(r"\s*[\u2013\u2014]\s*", ", ", text)


def available() -> bool:
    """True when the chosen AI can answer now."""
    try:
        return backend().status().ready
    except LLMUnavailable:
        return False


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
    from jobhunt.ai import provider

    log.info("Hal's AI: %s", provider.current())

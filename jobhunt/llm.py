"""The old way to ask the AI, kept so no caller changes: a shim over `jobhunt.ai`.

`chat(..., role="main"|"writer"|"fast")` maps the old role names onto the new ones and goes through
`jobhunt.ai.chat`, so every old call site gets the private-answer guard, the allowance and the GPU gate.
Callers move to `jobhunt.ai.chat` with a `purpose` as their owners touch them. `LLMUnavailable` is the new
one, so `except llm.LLMUnavailable` catches every kind. `undash` and `available` stay here.

`HAL_ROOT` (Hal's own folder, for its retrieval library) is read from the `addon.hal_root` setting when used;
it has no built-in default, because the folder is the owner's and other installs have none.

`counted()` counts the calls made through `chat` and `chat_json` inside a block, so a caller can log how many
one piece of work took: a pack made about 55 calls before its drafts were told one paragraph per piece of work
(2026-11-08), and the count per pack is how the change is measured. It is a context variable, so it counts
this thread's calls only, and blocks nest.
"""
import contextvars
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import ai
from .ai.base import LLMUnavailable

__all__ = ["Calls", "LLMUnavailable", "available", "chat", "chat_json", "counted", "undash"]

OLD_TIMEOUT_S = 180       # what the old chat() waited when a caller named no timeout


def __getattr__(name):  # old constant, read live (PEP 562)
    if name == "HAL_ROOT":
        from .core import settings

        root = settings.get("addon.hal_root") or ""
        return Path(root).expanduser() if root else Path("hal-not-configured")
    raise AttributeError(name)


@dataclass
class Calls:
    """How many AI calls a `counted` block has made so far."""

    n: int = 0


_COUNTING: contextvars.ContextVar[tuple[Calls, ...]] = contextvars.ContextVar("llm_counting", default=())


@contextmanager
def counted() -> Iterator[Calls]:
    """Counts the calls `chat` and `chat_json` make inside the block; an outer block counts an inner one's too."""
    calls = Calls()
    token = _COUNTING.set((*_COUNTING.get(), calls))
    try:
        yield calls
    finally:
        _COUNTING.reset(token)


def _count() -> None:
    for calls in _COUNTING.get():
        calls.n += 1


def chat(system: str, user: str, *, role: str = "main", json_out: bool = False, timeout: float = OLD_TIMEOUT_S,
         ctx: int | None = None, temperature: float | None = None) -> str:
    """Asks the AI; `ctx` widens a local model's context window, in tokens, for long material, and
    `temperature` replaces the backend's own for this request."""
    _count()
    return ai.chat(system, user, role=role, json_out=json_out, timeout=timeout, ctx=ctx, temperature=temperature)


def chat_json(system: str, user: str, **kw) -> dict:
    """Asks for JSON and returns the object, {} when the reply holds none."""
    _count()
    return ai.chat_json(system, user, **kw)


def undash(text: str) -> str:
    """Rewrites the long dashes models favour: a hyphen in number ranges, a comma elsewhere."""
    text = re.sub(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)", "-", text)
    return re.sub(r"\s*[\u2013\u2014]\s*", ", ", text)


def available() -> bool:
    """True when the chosen AI can answer now."""
    try:
        return ai.backend().status().ready
    except LLMUnavailable:
        return False

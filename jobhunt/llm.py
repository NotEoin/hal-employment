"""The old way to ask the AI, kept so no caller has to change: a shim over `jobhunt.ai`.

`chat(..., role="main"|"writer"|"fast")` maps the old role names onto the new ones and goes through
`jobhunt.ai.chat`, so every old call site gets the private-answer guard, the allowance and the GPU gate.
Callers move to `jobhunt.ai.chat` with a `purpose` as their owners touch them. `LLMUnavailable` is the new
one, so `except llm.LLMUnavailable` catches every kind. `undash` and `available` stay here.

`HAL_ROOT` (Hal's own folder, for its retrieval library) is read from the `addon.hal_root` setting when used;
it has no built-in default, because the folder is the owner's and other installs have none.
"""
import re
from pathlib import Path

from . import ai
from .ai.base import LLMUnavailable

__all__ = ["LLMUnavailable", "available", "chat", "chat_json", "undash"]

OLD_TIMEOUT_S = 180       # what the old chat() waited when a caller named no timeout


def __getattr__(name):  # old constant, read live (PEP 562)
    if name == "HAL_ROOT":
        from .core import settings

        root = settings.get("addon.hal_root") or ""
        return Path(root).expanduser() if root else Path("hal-not-configured")
    raise AttributeError(name)


def chat(system: str, user: str, *, role: str = "main", json_out: bool = False, timeout: float = OLD_TIMEOUT_S,
         ctx: int | None = None) -> str:
    """Asks the AI; `ctx` widens a local model's context window, in tokens, for long material."""
    return ai.chat(system, user, role=role, json_out=json_out, timeout=timeout, ctx=ctx)


def chat_json(system: str, user: str, **kw) -> dict:
    """Asks for JSON and returns the object, {} when the reply holds none."""
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

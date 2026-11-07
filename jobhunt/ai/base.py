"""The AI interface every backend implements: one request shape, one result shape, plain-words status.

Backends are "local" (Ollama), "openai_compat", "claude_cli" and, in tests only, "fake". Callers use
`jobhunt.ai.chat`, which runs the private-answer guard, the allowance, the GPU gate and the backend in that
order; nothing here sends anything anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

Role = Literal["writer", "checker", "quick", "embed"]
ROLES: tuple[str, ...] = ("writer", "checker", "quick", "embed")
OLD_ROLES = {"writer": "writer", "main": "checker", "fast": "quick"}   # the role names `llm.py` always used
UNAVAILABLE_KINDS = ("signed_out", "limit", "free_plan", "timeout", "cap", "keep_free", "gpu_busy", "local_only",
                     "not_installed", "unavailable")


def role_for(name: str) -> str:
    """The role for a new or old role name ("main" -> "checker"); ValueError for anything else."""
    role = OLD_ROLES.get(name, name)
    if role not in ROLES:
        raise ValueError(f"unknown AI role {name!r}; one of {', '.join(ROLES)}")
    return role


@dataclass(frozen=True)
class ChatRequest:
    """One prompt, with what it is for."""

    system: str
    user: str
    role: Role = "checker"
    json_out: bool = False
    timeout: float | None = None        # default: the ai.timeout setting
    ctx: int | None = None              # Ollama num_ctx; ignored elsewhere
    purpose: str = ""                   # the prompt id, e.g. "letter"
    priority: int | None = None         # default: the current action's priority, else 2
    job_id: str | None = None
    temperature: float | None = None    # default: the backend's own (low for JSON); the Claude CLI takes none


@dataclass(frozen=True)
class ChatResult:
    """A reply and where it came from."""

    text: str
    provider: str
    model: str
    ms: int


@dataclass(frozen=True)
class Status:
    """Whether a backend can answer now, in plain words, with the health fix to offer when not."""

    ready: bool
    why: str = ""
    fix: str = ""
    limited_until: datetime | None = None
    detail: dict = field(default_factory=dict)


class Backend(Protocol):
    """What every AI backend provides."""

    name: str
    online: bool            # sends text off this computer
    local_gpu: bool         # the GPU gate applies

    def chat(self, req: ChatRequest, model: str) -> ChatResult:
        """Answers one request with `model`."""
        ...

    def status(self) -> Status:
        """Whether it can answer now."""
        ...

    def models(self) -> list[str]:
        """The models it offers."""
        ...

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Vectors for `texts` (local backends only; others raise `LLMUnavailable`)."""
        ...


class LLMUnavailable(Exception):
    """The AI cannot answer now; callers fall back to rules. `kind` says why, from `UNAVAILABLE_KINDS`."""

    kind: str = "unavailable"

    def __init__(self, message: str = "", *, kind: str | None = None) -> None:
        super().__init__(message)
        if kind is not None:
            if kind not in UNAVAILABLE_KINDS:
                raise ValueError(f"unknown unavailable kind {kind!r}")
            self.kind = kind


class Limited(LLMUnavailable):
    """The plan's limit is reached until `until`."""

    kind = "limit"

    def __init__(self, message: str = "", *, until: datetime | None = None) -> None:
        super().__init__(message)
        self.until = until


class PrivateLeak(Exception):
    """A prompt held a saved private answer; raised by the guard and never caught by callers."""

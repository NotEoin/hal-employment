"""Small helpers the catalogue files share: choice lists and defaults that depend on other settings.

Each default function takes `get` (a function from key to live value) and returns the default, or None when
it cannot decide, in which case the setting's plain `default` is used (registry.default_of).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Get = Callable[[str], Any]
LOCAL_PROVIDERS = ("local", "openai_compat")


def choices(*pairs: tuple[Any, str]) -> tuple[tuple[Any, str], ...]:
    """A choice list of (stored value, words on screen) pairs."""
    return tuple(pairs)


def same(*values: Any, unit: str = "") -> tuple[tuple[Any, str], ...]:
    """Choices whose words are the value itself, with an optional unit: 7 -> "7 days"."""
    return tuple((v, f"{v} {unit}".strip()) for v in values)


def safe(get: Get, key: str, fallback: Any = None) -> Any:
    """`get(key)`, or `fallback` when the key is not readable yet (unregistered, or no store in this process)."""
    try:
        return get(key)
    except (KeyError, LookupError, RuntimeError):
        return fallback


def uses_local_ai(get: Get) -> bool:
    """True when Hal's AI runs on this computer (or another local server)."""
    return safe(get, "ai.provider") in LOCAL_PROVIDERS

"""The old GPU check, kept so no caller has to change: a shim over `jobhunt.ai.gpu`.

`busy()` says why the AI cannot run now, or None: another program on the graphics card (only for AI on this
computer, and only while `ai.gpu_gate` is on), or the chosen AI not being ready.
"""
from .ai.gpu import OWN, holders
from .ai.gpu import busy as _busy

__all__ = ["OWN", "busy", "holders"]


def busy() -> str | None:
    """Why the AI cannot run now, or None when it can."""
    return _busy()

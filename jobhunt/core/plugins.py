"""Discovery of area code by module name, so no package edits a shared registry file.

`modules("routes")` imports every module under `jobhunt/` named `routes.py` or ending in `_routes.py`, in
dotted-name order; importing a module registers what it defines (its decorators run). A module that fails
to import is logged and kept in `FAILED` for the "Is Hal working?" check, never skipped silently.
`on_start` collects work the server runs once at start (runner recovery, data tasks, the helper copy).
"""
from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

KINDS = ("routes", "settings", "actions", "health", "home", "search", "schedule", "undo", "tasks")
PACKAGE = "jobhunt"
SKIP_DIRS = {"static", "__pycache__", "prompts", "templates"}   # data folders, never code to import
# Code found another way: migrations run in order through `core.db.migrate`, and commands through
# `cli.register_all`. Without this `0003_settings.py` and `cli/settings.py` would load as settings.
SKIP_PACKAGES = (("core", "db", "migrations"), ("cli",))

log = logging.getLogger(__name__)
FAILED: dict[str, str] = {}             # dotted module name -> the error, for the health check
_lock = threading.Lock()
_start: list[tuple[str, Callable[[], None]]] = []


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def names(kind: str) -> list[str]:
    """The dotted names of the modules of `kind`, in order, without importing them."""
    if kind not in KINDS:
        raise ValueError(f"unknown plugin kind {kind!r}; one of {', '.join(KINDS)}")
    root = _root()
    found = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if any(rel.parts[:len(pkg)] == pkg for pkg in SKIP_PACKAGES):
            continue
        if path.stem == kind or path.stem.endswith(f"_{kind}"):
            found.append(".".join((PACKAGE, *rel.with_suffix("").parts)))
    return sorted(found)


def modules(kind: str) -> list[ModuleType]:
    """Imports and returns every module of `kind`; failures are logged and kept in `FAILED`."""
    out = []
    for name in names(kind):
        try:
            out.append(importlib.import_module(name))
        except Exception as e:
            with _lock:
                FAILED[name] = f"{type(e).__name__}: {e}"
            log.exception("could not import %s", name)
        else:
            with _lock:
                FAILED.pop(name, None)
    return out


def on_start(fn: Callable[[], None]) -> Callable[[], None]:
    """Decorator: runs `fn()` once when the server starts, after the routes are loaded."""
    with _lock:
        _start.append((f"{fn.__module__}.{fn.__qualname__}", fn))
    return fn


def run_start_tasks() -> list[str]:
    """Runs every start task in registration order; returns the names of those that failed (logged)."""
    failed = []
    with _lock:
        tasks = list(_start)
    for name, fn in tasks:
        try:
            fn()
        except Exception:
            log.exception("start task %s failed", name)
            failed.append(name)
    return failed

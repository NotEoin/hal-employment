"""Live settings: read where a value is used, never at import.

`get` answers the stored value or the registered default; `set` parses, validates, stores and records the
change. The registry (`core.settings.registry`) declares every key; an unregistered key is a
`KeyError`, so a typo fails loudly. This is the minimal version over `store`; goal versions,
rescore counts, secrets and private answers come later on the same signatures.
"""
from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import store

_loaded = False
_load_lock = threading.Lock()
_subscribers: list[tuple[str, Callable[[str, Any, Any], None]]] = []
SETTINGS_AREA = "settings"


@dataclass
class SetResult:
    """What one change did, for the page that made it."""

    key: str
    value: Any
    display: str
    restart: bool
    rescore_count: int | None
    goal_version: int | None
    activity_id: int | None


def _registry():
    """The registry module with every catalogue and area settings file loaded."""
    global _loaded
    from . import registry

    with _load_lock:
        if not _loaded:
            registry.load_all()
            _loaded = True
    return registry


def setting(key: str):
    """The registered `Setting` for `key`; KeyError when no code registered it."""
    reg = _registry()
    if key not in reg.REGISTRY:
        raise KeyError(f"no setting {key!r} is registered")
    return reg.REGISTRY[key]


def _default(item) -> Any:
    if item.default_fn is not None:
        return item.default_fn(get)
    return item.default


def get(key: str) -> Any:
    """The live value of `key`, or its default."""
    item = setting(key)
    vals = store.values()
    return vals[key] if key in vals else _default(item)


def get_many(prefix: str) -> dict[str, Any]:
    """Every registered key under `prefix` with its live value."""
    reg = _registry()
    return {k: get(k) for k in sorted(reg.REGISTRY) if k.startswith(prefix)}


def changed() -> dict[str, Any]:
    """The keys whose stored value differs from the default."""
    reg = _registry()
    return {k: v for k, v in store.values().items() if k in reg.REGISTRY}


def revision() -> int:
    """Rises on every write by any process."""
    return store.revision()


def _display(item, value: Any) -> str:
    if item.show is not None:
        return item.show(value)
    return "" if value is None else str(value)


def _check(item, value: Any) -> Any:
    reg = _registry()
    if item.parse is not None:
        value = item.parse(value)
    if item.validate is not None:
        problem = item.validate(value)
        if problem:
            raise reg.SettingError(item.key, problem)
    if item.choices and item.kind in ("choice", "seg") and value not in {c[0] for c in item.choices}:
        raise reg.SettingError(item.key, "Choose one of the options shown.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if item.min is not None and value < item.min:
            raise reg.SettingError(item.key, f"Use {item.min:g} or more.")
        if item.max is not None and value > item.max:
            raise reg.SettingError(item.key, f"Use {item.max:g} or less.")
    return value


def _activity(con: sqlite3.Connection, key: str, old: Any, label: str, at: str, session_id: str | None) -> int | None:
    """An Activity row with Undo, once the activity table exists (migration 0004)."""
    try:
        cur = con.execute(
            """INSERT INTO activity (at, area, kind, icon, tone, text, why, undo, session_id)
               VALUES (?, ?, 'setting.changed', 'settings', 'info', ?, ?, ?, ?)""",
            (at, SETTINGS_AREA, f"Changed {label}", "You changed this setting.",
             json.dumps({"type": "setting", "key": key, "value": old}), session_id))
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return None
        raise
    con.commit()
    return cur.lastrowid


def _notify(key: str, old: Any, new: Any) -> None:
    for prefix, fn in list(_subscribers):
        if key.startswith(prefix):
            fn(key, old, new)


def _write(key: str, value: Any, *, by: str, session_id: str | None, con: sqlite3.Connection) -> SetResult:
    from .. import clock, events

    item = setting(key)
    value = _check(item, value)
    at = clock.iso()
    old = store.write(con, key, value, default=_default(item), by=by, kind=item.kind, session_id=session_id, at=at)
    activity_id = _activity(con, key, old, item.label, at, session_id)
    events.publish("setting", keys=[key])
    _notify(key, old, value)
    return SetResult(key=key, value=value, display=_display(item, value), restart=item.restart, rescore_count=None,
                     goal_version=None, activity_id=activity_id)


def _con() -> sqlite3.Connection:
    from ... import db

    return db.connect()


def set(key: str, value: Any, *, by: str = "you", session_id: str | None = None) -> SetResult:
    """Parses, checks and stores one value; raises `SettingError` with words for the person."""
    con = _con()
    try:
        return _write(key, value, by=by, session_id=session_id, con=con)
    finally:
        con.close()


def set_many(values: dict[str, Any], *, by: str = "you") -> list[SetResult]:
    """Stores several values, all or nothing: every value is checked before any is written."""
    for key, value in values.items():
        _check(setting(key), value)
    con = _con()
    try:
        return [_write(k, v, by=by, session_id=None, con=con) for k, v in values.items()]
    finally:
        con.close()


def reset(key: str, *, by: str = "you") -> SetResult:
    """Puts `key` back to its default."""
    return set(key, _default(setting(key)), by=by)


def cached(*prefixes: str):
    """Memoises a function until a setting under one of `prefixes` changes in any process."""
    def wrap(fn):
        memo: dict = {}

        @functools.wraps(fn)
        def inner(*args):
            rev = store.revision()
            if memo.get("rev") != rev:
                memo.clear()
                memo["rev"], memo["values"] = rev, {}
            if args not in memo["values"]:
                memo["values"][args] = fn(*args)
            return memo["values"][args]

        inner.cache_prefixes = prefixes
        return inner
    return wrap


def schema(*, developer: bool, remote: bool, platform: str | None = None) -> list[dict]:
    """The Settings page's groups and rows for this session, in page order."""
    from .. import paths

    reg = _registry()
    platform = platform or paths.platform_name()
    out = []
    for gid, name, icon in reg.GROUPS:
        items = []
        for item in sorted((s for s in reg.REGISTRY.values() if (s.group or s.key.split(".")[0]) == gid),
                           key=lambda s: (s.order, s.key)):
            if item.dev_only and not developer:
                continue
            if item.platforms and platform not in item.platforms:
                continue
            if not reg.visible(item, get, developer=developer, platform=platform):
                continue
            disabled = "Change this on the computer Hal runs on." if remote and item.remote == "local_only" else ""
            items.append({"key": item.key, "kind": item.kind, "label": item.label, "help": item.help,
                          "choices": [list(c) for c in item.choices], "min": item.min, "max": item.max,
                          "unit": item.unit, "restart": item.restart, "rescore": item.rescore,
                          "advanced": item.advanced, "visible_if": item.visible_if, "to": item.to,
                          "action": item.action, "disabled_why": disabled})
        out.append({"id": gid, "name": name, "icon": icon, "items": items})
    return out


def on_change(prefix: str, fn: Callable[[str, Any, Any], None]) -> None:
    """Calls `fn(key, old, new)` after any change in this process to a key under `prefix`."""
    _subscribers.append((prefix, fn))

"""Live settings: read where a value is used, never at import.

`get` answers the stored value or the registered default; `set` parses, checks and stores one change with
its history, Activity row (with Undo) and, for a goal setting, a goal version, all in one transaction. The
registry (`core.settings.registry`) declares every key; an unregistered key is a `KeyError`, so a typo
fails loudly. Secrets (`secrets.py`) and private answers (`private.py`) are kept elsewhere and never pass
through `set`.

Design notes:
- Nothing about the goal, the scoring or the CV changes without the person's yes: `set(..., by="hal")` on
  a `goal.*`, `scoring.*` or `cvdocs.*` key raises `PermissionError` (gate G7 checks the code statically too).
- Subscribers (`on_change`) run after the change is committed. One that fails is logged and the others
  still run, because the change has already happened and the person was told so.
- `cached` memoises on the stored values under its prefixes, so an unrelated change does not throw away
  work such as compiled goal patterns.
"""
from __future__ import annotations

import dataclasses
import functools
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import store

log = logging.getLogger(__name__)

_loaded = False
_load_lock = threading.Lock()
_subscribers: list[tuple[str, Callable[[str, Any, Any], None]]] = []
SETTINGS_AREA = store.AREA
BY = ("you", "setup", "import", "learned", "hal")
# Only the person decides these; Hal may suggest, never write.
PERSON_ONLY = ("goal.", "scoring.", "cvdocs.")
GOAL_PREFIXES = ("goal.", "scoring.")       # what a goal version holds (migration 0006)
OPEN_STATUSES = ("new", "shortlisted", "ready")   # the jobs a re-check covers
REMOTE_DISABLED = "Change this on the computer Hal runs on."
WHY = {
    "you": "You changed this setting.",
    "setup": "You chose this in the setup guide.",
    "import": "Copied from your old settings when your data moved.",
    "learned": "You said yes to one of Hal's suggestions.",
    "hal": "Hal changed this itself.",
}


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

    def as_dict(self) -> dict:
        """The shape the settings API answers."""
        return {"key": self.key, "value": self.value, "display": self.display, "restart": self.restart,
                "rescore_count": self.rescore_count, "goal_version": self.goal_version,
                "activity_id": self.activity_id}


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


def _default(item, get_fn: Callable[[str], Any] | None = None) -> Any:
    return _registry().default_of(item, get_fn or get)


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
    return {k: v for k, v in store.values().items() if k in reg.REGISTRY and v != _default(reg.REGISTRY[k])}


def revision() -> int:
    """Rises on every write by any process."""
    return store.revision()


def display(key: str, value: Any = ...) -> str:
    """The words for `key`'s value (its live value when none is given)."""
    from . import parse

    item = setting(key)
    return parse.describe(item, get(key) if value is ... else value)


def check(key: str, value: Any) -> Any:
    """What `set` would store for `value`; raises `SettingError` with words, and writes nothing."""
    from . import parse

    item = setting(key)
    reg = _registry()
    if not item.stored:
        raise reg.SettingError(key, "This row has no value to change here.")
    return parse.check(item, value)


def _local_get(con: sqlite3.Connection) -> Callable[[str], Any]:
    """A `get` over `con`'s own view, so a default inside a transaction sees the value just written."""
    stored = store.read_all(con)
    reg = _registry()

    def local(key: str) -> Any:
        if key in stored:
            return stored[key]
        return reg.default_of(setting(key), local)
    return local


def _goal_data(con: sqlite3.Connection) -> dict:
    """Every goal and scoring value as it now is, for a goal version."""
    reg = _registry()
    local = _local_get(con)
    return {k: local(k) for k in sorted(reg.REGISTRY)
            if k.startswith(GOAL_PREFIXES) and reg.REGISTRY[k].stored}


def _rescore_count(con: sqlite3.Connection) -> int | None:
    marks = ", ".join("?" * len(OPEN_STATUSES))
    try:
        row = con.execute(f"SELECT COUNT(*) FROM jobs WHERE active = 1 AND status IN ({marks})",
                          OPEN_STATUSES).fetchone()
    except sqlite3.OperationalError as e:
        log.warning("could not count the jobs a re-check would cover: %s", e)
        return None
    return int(row[0])


def _notify(key: str, old: Any, new: Any) -> None:
    for prefix, fn in list(_subscribers):
        if not key.startswith(prefix):
            continue
        try:
            fn(key, old, new)
        except Exception:  # the change is committed; one subscriber must not stop the rest
            log.exception("a subscriber to %s failed after %s changed", prefix, key)


def _guard_by(key: str, by: str) -> None:
    if by not in BY:
        raise ValueError(f"by must be one of {BY}, not {by!r}")
    if by == "hal" and key.startswith(PERSON_ONLY):
        raise PermissionError(f"Hal may suggest a change to {key}, never make it: the person decides.")


def _write(con: sqlite3.Connection, key: str, value: Any, *, by: str, session_id: str | None) -> tuple:
    """Stores one checked value inside the caller's transaction; returns what `SetResult` needs."""
    from .. import clock

    item = setting(key)
    default = _default(item)
    words = display(key, value)
    at = clock.iso()

    def activity(old: Any) -> store.Activity:
        return store.Activity(text=f"{item.label}: {words}" if words else f"{item.label}: cleared", why=WHY[by],
                              undo={"type": "setting", "key": key, "value": old}, changed={"setting": key})

    goal = (f"{item.label} changed to {words or 'nothing'}", _goal_data) if item.goal else None
    written = store.change_in(con, key, value, default=default, by=by, kind=item.kind, session_id=session_id,
                              at=at, activity=activity, goal=goal)
    return item, written, words


def _result(con: sqlite3.Connection, key: str, value: Any, item, written: store.Written, words: str) -> SetResult:
    return SetResult(key=key, value=value, display=words, restart=item.restart,
                     rescore_count=_rescore_count(con) if item.rescore else None,
                     goal_version=written.goal_version, activity_id=written.activity_id)


def _published(results: list[tuple[str, Any, Any]]) -> None:
    from .. import events

    events.publish("setting", keys=[k for k, _, _ in results])
    for key, old, new in results:
        _notify(key, old, new)


def _con() -> sqlite3.Connection:
    from ... import db

    return db.connect()


def set(key: str, value: Any, *, by: str = "you", session_id: str | None = None) -> SetResult:
    """Parses, checks and stores one value; raises `SettingError` with words for the person."""
    _guard_by(key, by)
    value = check(key, value)
    con = _con()
    try:
        with store.transaction(con):
            item, written, words = _write(con, key, value, by=by, session_id=session_id)
        result = _result(con, key, value, item, written, words)
    finally:
        con.close()
    _published([(key, written.old, value)])
    return result


def set_many(values: dict[str, Any], *, by: str = "you", session_id: str | None = None) -> list[SetResult]:
    """Stores several values in one transaction, all or nothing: every value is checked before any is written."""
    for key in values:
        _guard_by(key, by)
    checked = {key: check(key, value) for key, value in values.items()}
    con = _con()
    try:
        done = []
        with store.transaction(con):
            for key, value in checked.items():
                done.append((key, value, *_write(con, key, value, by=by, session_id=session_id)))
        results = [_result(con, key, value, item, written, words) for key, value, item, written, words in done]
    finally:
        con.close()
    _published([(key, written.old, value) for key, value, _, written, _ in done])
    return results


def reset(key: str, *, by: str = "you", session_id: str | None = None) -> SetResult:
    """Puts `key` back to its default."""
    return set(key, _default(setting(key)), by=by, session_id=session_id)


def undo(payload: dict, *, session_id: str | None = None) -> SetResult:
    """Applies an Activity row's Undo (`{"type": "setting", "key", "value"}`): the old value, as a new change."""
    if payload.get("type") != "setting" or "key" not in payload:
        raise ValueError("not a setting's undo")
    return set(payload["key"], payload.get("value"), by="you", session_id=session_id)


def history(key: str | None = None, limit: int = 50) -> list[dict]:
    """The newest changes first, for one key or all."""
    con = _con()
    try:
        return store.history(con, key, limit)
    finally:
        con.close()


def cached(*prefixes: str):
    """Memoises a function until a setting under one of `prefixes` changes in any process."""
    def wrap(fn):
        memo: dict = {}
        lock = threading.Lock()

        def snapshot() -> str:
            vals = store.values()
            under = {k: v for k, v in vals.items() if not prefixes or k.startswith(prefixes)}
            return json.dumps(under, sort_keys=True, default=str)

        @functools.wraps(fn)
        def inner(*args):
            snap = snapshot()
            with lock:
                if memo.get("snap") != snap:
                    memo.clear()
                    memo["snap"], memo["values"] = snap, {}
                if args in memo["values"]:
                    return memo["values"][args]
            value = fn(*args)
            with lock:
                if memo.get("snap") == snap:
                    memo["values"][args] = value
            return value

        inner.cache_prefixes = prefixes
        return inner
    return wrap


def schema(*, developer: bool, remote: bool, platform: str | None = None) -> list[dict]:
    """The Settings page's groups and rows for this session, in page order; empty groups are left out."""
    from .. import paths

    reg = _registry()
    platform = platform or paths.platform_name()
    out = []
    for gid, name, icon in reg.GROUPS:
        items = []
        for item in reg.in_group(gid):
            # visible_if is sent with the row and evaluated by the page, so a row appears as its condition changes
            if not reg.visible(dataclasses.replace(item, visible_if=""), get, developer=developer, platform=platform):
                continue
            disabled = REMOTE_DISABLED if remote and item.remote == "local_only" else ""
            items.append({"key": item.key, "kind": item.kind, "label": item.label, "help": item.help,
                          "choices": [list(c) for c in item.choices], "min": item.min, "max": item.max,
                          "unit": item.unit, "restart": item.restart, "rescore": item.rescore,
                          "advanced": item.advanced, "visible_if": item.visible_if, "to": item.to,
                          "action": item.action, "disabled_why": disabled})
        if items:
            out.append({"id": gid, "name": name, "icon": icon, "items": items})
    return out


def on_change(prefix: str, fn: Callable[[str, Any, Any], None]) -> Callable[[], None]:
    """Calls `fn(key, old, new)` after any change in this process to a key under `prefix`; returns an unsubscribe."""
    entry = (prefix, fn)
    _subscribers.append(entry)

    def unsubscribe() -> None:
        if entry in _subscribers:
            _subscribers.remove(entry)
    return unsubscribe

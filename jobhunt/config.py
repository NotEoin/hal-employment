"""The old configuration names, answered from live settings, plus the board list and private answers.

`CONFIG.get(old_key, default)` maps an old `config.toml` key onto its setting through
`core.settings.legacy_map`, so modules not yet converted keep working while their owners move them to
`settings.get`. `CONFIG.get("applicant", {})` is a view of the `you.*` values the person has set, under the
old names; `CONFIG.get("search", {})` (the stick's layout) a view of `goal.*`. `boards()` and `private()` keep
their signatures.

Design notes:
- The checkout's `config.toml` is read only in the legacy layout (the owner's data still in the checkout's
  `data/`, before the import moves it), so a temporary, new or imported home never sees the owner's
  values. A setting the person has changed wins over the file.
- An old key answers its setting's live value when the setting is stored or its default is a real value.
  When the default is empty ("", None, []) the caller's own default answers, as before, because old callers
  pass a working fallback there (a folder, a URL) that an empty setting must not replace.
- The carer answer is never in the applicant view: it lives in the private store.
- `CONFIG` is read-only. Tests may patch it (`mock.patch.dict`): a value set under a test answers in this
  process only, before the live settings, and is never stored.
"""
from __future__ import annotations

import logging
import os
import threading
import tomllib
from collections.abc import Iterator, Mapping
from typing import Any

from .core import paths

log = logging.getLogger(__name__)

ROOT = paths.code_dir()
CONFIG_PATH = ROOT / "config.toml"   # read only in the legacy layout
BOARDS_PATH = ROOT / "boards.toml"
EMPTY = (None, "", [], {}, ())
_MISSING = object()

_file_lock = threading.Lock()
_file_cache: tuple[float, dict] | None = None


def __getattr__(name):  # old path constant, read live (PEP 562)
    if name == "PRIVATE_PATH":
        return paths.private_path()
    raise AttributeError(name)


def _legacy_layout() -> bool:
    try:
        return paths.is_legacy_layout()
    except paths.RealDataInTests:
        return False


def legacy_file() -> dict:
    """The checkout's `config.toml`, only while the owner's data still lives in the checkout; else {}."""
    global _file_cache
    if not _legacy_layout() or not CONFIG_PATH.is_file():
        return {}
    mtime = CONFIG_PATH.stat().st_mtime
    with _file_lock:
        if _file_cache is not None and _file_cache[0] == mtime:
            return _file_cache[1]
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        _file_cache = (mtime, data)
        return data


def _from_file(old: str) -> Any:
    """`old` ("alert_score", "applicant.city") in the legacy file, or _MISSING."""
    node: Any = legacy_file()
    for part in old.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _settings():
    from .core import settings

    return settings


def _reverse(entry, value: Any) -> Any:
    """A setting's value in the old file's terms where the words changed (`helper` -> `extension`), else _MISSING."""
    for old, new in entry.values:
        if type(new) is type(value) and new == value:
            return old
    return _MISSING


def _old_words(entry, value: Any) -> Any:
    """A table value in the old file's terms (`prefer_not` -> "Prefer not to say"), or _MISSING.

    A yes-or-no setting reads "Yes" or "No", as the old forms wrote it. A choice the old code has no words for
    is left out, so the old caller's own default answers.
    """
    old = _reverse(entry, value)
    if old is not _MISSING:
        return old
    kind = _settings().setting(entry.setting).kind
    if kind == "bool":
        return "Yes" if value else "No"
    if kind in ("choice", "seg"):
        return _MISSING
    return value


def _secret(name: str) -> Any:
    from .core.settings import secrets

    try:
        return secrets.get(name, interactive=False)
    except secrets.SecretsLocked:
        log.info("%s is waiting for the keyring to be unlocked", name)
        return None


def _live(entry, default: Any) -> Any:
    """The answer for one mapped key: a changed setting, else the legacy file, else the default in effect."""
    from .core.settings import secrets

    if entry.old == "signed_in_apply":
        return []    # Hal never presses a signed-in Apply
    in_file = _from_file(entry.old)
    if entry.setting in secrets.NAMES:
        return in_file if in_file is not _MISSING else (_secret(entry.setting) or default)
    if entry.store != "settings" or entry.setting is None:
        return in_file if in_file is not _MISSING else default
    settings = _settings()
    value = settings.store.stored(entry.setting)
    if value is None:
        if in_file is not _MISSING:
            return in_file
        value = settings.get(entry.setting)
        if value in EMPTY and default is not None:
            return default
    old = _reverse(entry, value)
    return value if old is _MISSING else old


def _section(name: str) -> dict:
    """A table such as `applicant`: the keys the person set, under their old names (never the carer answer)."""
    from .core.settings import legacy_map

    settings = _settings()
    out: dict[str, Any] = {}
    in_file = legacy_file().get(name, {})
    if isinstance(in_file, dict):
        out.update({k: v for k, v in in_file.items() if not (name == "applicant" and k == "carer")})
    for entry in legacy_map.LEGACY_KEYS:
        section, _, key = entry.old.partition(".")
        if section != name or not key or entry.store != "settings" or entry.setting is None:
            continue
        stored = settings.store.stored(entry.setting)
        if stored is None:
            continue
        words = _old_words(entry, stored)
        if words is not _MISSING:
            out[key] = words
    return out


class LegacyConfig(Mapping):
    """`CONFIG`: the old key names over live settings, read-only outside tests."""

    def __init__(self) -> None:
        self._overlay: dict[str, Any] = {}

    def _sections(self) -> set[str]:
        from .core.settings import legacy_map

        return {k.old.split(".", 1)[0] for k in legacy_map.LEGACY_KEYS if "." in k.old and ":" not in k.old}

    def get(self, key: str, default: Any = None) -> Any:
        """The value of an old key, or `default` when nothing answers it."""
        from .core.settings import legacy_map

        if key in self._overlay:
            return self._overlay[key]
        if key in self._sections():
            view = _section(key)
            return view if view or default is None else default
        entry = legacy_map.BY_OLD.get(key)
        if entry is None:
            value = _from_file(key)
            return default if value is _MISSING else value
        value = _live(entry, default)
        return default if value is None else value

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __iter__(self) -> Iterator[str]:
        from .core.settings import legacy_map

        seen = dict.fromkeys(self._overlay)
        seen.update(dict.fromkeys(legacy_file()))
        for entry in legacy_map.LEGACY_KEYS:
            if "." not in entry.old and ":" not in entry.old and entry.setting and entry.store == "settings":
                if _settings().store.stored(entry.setting) is not None:
                    seen[entry.old] = None
        return iter(seen)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def _writable(self) -> None:
        if os.environ.get(paths.ENV_TEST) != "1" and not paths._under_test_runner():
            raise TypeError("CONFIG is read-only: change a setting with core.settings.set")

    def __setitem__(self, key: str, value: Any) -> None:
        self._writable()
        self._overlay[key] = value

    def __delitem__(self, key: str) -> None:
        self._writable()
        del self._overlay[key]

    def update(self, other=(), **kw: Any) -> None:
        """Test overlay only (`mock.patch.dict`)."""
        self._writable()
        self._overlay.update(other, **kw)

    def clear(self) -> None:
        """Test overlay only (`mock.patch.dict`)."""
        self._writable()
        self._overlay.clear()

    def copy(self) -> dict[str, Any]:
        """The test overlay, which is all `mock.patch.dict` needs to put back."""
        return dict(self._overlay)


CONFIG = LegacyConfig()


def boards_path():
    """The board list's file: the import's copy in the data folder, else the checkout's (or a test's) file.

    The import copies the checkout's `boards.toml` to `<data>/import/boards.toml`, and the copy
    in the checkout is deleted once the import has run, so the imported copy wins once it exists.
    """
    if BOARDS_PATH == ROOT / "boards.toml" and not paths.is_legacy_layout():
        imported = paths.data_dir() / "import" / "boards.toml"
        if imported.exists():
            return imported
    return BOARDS_PATH


def boards() -> list[dict]:
    """The board list (until the sources registry replaces it); [] when there is none."""
    path = boards_path()
    if not path.exists():
        return []
    return tomllib.loads(path.read_text()).get("board", [])


def private() -> dict:
    """The [applicant] answers in the data folder's private.toml, or {} when there is none."""
    from .core.settings import private as store

    path = globals().get("PRIVATE_PATH")
    if path is not None and path != paths.private_path():
        # a test pointed the old constant elsewhere
        return tomllib.loads(path.read_text()).get("applicant", {}) if path.exists() else {}
    return store.read_table()

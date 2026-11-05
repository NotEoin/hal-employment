"""Passwords and keys: the email app password, job-site keys, the phone alert code, a GitHub token.

The first choice is the system keyring (`keyring`, service "Hal Employment", user name = the secret's
name): Windows Credential Manager, or the desktop's Secret Service on Linux. When no keyring works (a
headless session), values go to `config/secrets.json`: mode 0600 on Linux, each value protected with DPAPI
for the current user on Windows. A value is read back after every write, so "saved" is never a guess.

Design notes:
- Background work calls `get(name)` with `interactive=False`. On Linux the collection's lock is checked
  first and `SecretsLocked` is raised instead of unlocking, so no password prompt ever pops up behind the
  person; the page offers "Unlock your keyring" and Retry, which calls `interactive=True`.
- Keyring calls run in a worker thread with a timeout, because a D-Bus call to a stuck keyring daemon can
  block for ever.
- The keyring is shared by every Hal on this login, so it is used only for the real install: with a home
  override (`HAL_EMPLOYMENT_HOME`, a worktree, the importer's dry run) or under tests, secrets go to that
  home's own file, and a worktree can never overwrite the owner's saved password. Tests hand a fake
  backend to `use_keyring()`.
- Values are never logged, returned by an API, exported or put in Activity. Only `status()` and
  `backend()` describe them.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import paths

log = logging.getLogger(__name__)

SERVICE = "Hal Employment"
NAMES = ("email.app_password", "sites.adzuna_key", "sites.reed_key", "notify.ntfy_topic", "ai.openai_key",
         "record.github_token")
FILE_NAME = "secrets.json"
FILE_MODE = 0o600
KEYRING_TIMEOUT_S = 10          # a healthy keyring answers in well under a second
UNLOCK_TIMEOUT_S = 120          # the person types their keyring password in this time
MAX_SECRET_CHARS = 4096         # longer than any key or password a site gives out
WINDOWS_STORE = "Windows Credential Manager"
DESKTOP_STORE = "your desktop keyring"
FILE_STORE = "a protected file"
LOCKED_WORDS = "Waiting for you to unlock your keyring"

_override_lock = threading.Lock()
_override: list[Any] = []       # a test's keyring backend, or None for "no keyring"
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hal-keyring")


class SecretsLocked(RuntimeError):
    """The Linux keyring is locked; the page shows "Unlock your keyring" and Retry."""

    def __init__(self, name: str = "") -> None:
        super().__init__(f"{LOCKED_WORDS}." if not name else f"{LOCKED_WORDS} to use {name}.")
        self.name = name


class KeyringUnavailable(RuntimeError):
    """The keyring did not answer in time, or failed; the file store is used instead where it can be."""


def _check_name(name: str) -> None:
    if name not in NAMES:
        raise KeyError(f"no secret is called {name!r}")


def _separate_home() -> bool:
    """True for a test run or a home override, which must never reach the shared system keyring."""
    if os.environ.get(paths.ENV_TEST) == "1" or paths.home_override() is not None:
        return True
    try:
        paths.data_dir()
    except paths.RealDataInTests:
        return True
    return False


@contextmanager
def use_keyring(backend: Any) -> Iterator[None]:
    """Uses `backend` (an object with `get_password`, `set_password`, `delete_password`) or None for no keyring."""
    with _override_lock:
        _override.append(backend)
    try:
        yield
    finally:
        with _override_lock:
            _override.pop()


def _call(fn: Callable[[], Any], timeout: float = KEYRING_TIMEOUT_S) -> Any:
    """Runs a keyring call with a timeout; any failure becomes `KeyringUnavailable` (without the value)."""
    future = _pool.submit(fn)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise KeyringUnavailable("the keyring did not answer in time") from None
    except SecretsLocked:
        raise
    except Exception as e:  # keyring backends raise many types; the caller falls back to the file
        raise KeyringUnavailable(f"the keyring failed: {type(e).__name__}") from None


def _system_keyring() -> Any | None:
    """The system keyring backend when one works here, else None."""
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        kr = _call(keyring.get_keyring)
    except KeyringUnavailable as e:
        log.warning("no keyring: %s", e)
        return None
    if isinstance(kr, fail.Keyring) or getattr(kr, "priority", 1) <= 0:
        return None
    if type(kr).__name__ == "ChainerBackend" and not getattr(kr, "backends", None):
        return None
    return kr


def _keyring() -> Any | None:
    with _override_lock:
        if _override:
            return _override[-1]
    if _separate_home():
        return None
    return _system_keyring()


def _is_secret_service(kr: Any) -> bool:
    parts = [kr, *getattr(kr, "backends", ())]
    return any("SecretService" in type(p).__module__ for p in parts)


def _collection_locked(unlock: bool) -> bool:
    """The Secret Service default collection's lock; with `unlock`, asks the person to unlock it first."""
    import contextlib

    import secretstorage

    with contextlib.closing(secretstorage.dbus_init()) as conn:
        collection = secretstorage.get_default_collection(conn)
        if collection.is_locked() and unlock:
            collection.unlock()
        return collection.is_locked()


def _locked(kr: Any, *, unlock: bool = False) -> bool:
    """True when the keyring is locked (after an unlock attempt when `unlock`)."""
    if hasattr(kr, "is_locked"):
        if unlock and kr.is_locked() and hasattr(kr, "unlock"):
            _call(kr.unlock, UNLOCK_TIMEOUT_S)
        return bool(_call(kr.is_locked))
    if not _is_secret_service(kr):
        return False
    return bool(_call(lambda: _collection_locked(unlock), UNLOCK_TIMEOUT_S if unlock else KEYRING_TIMEOUT_S))


# --- the protected file --------------------------------------------------------------------------------

def file_path() -> Path:
    """`config/secrets.json`, used only when no keyring works."""
    return paths.config_dir() / FILE_NAME


def _read_file() -> dict[str, dict]:
    path = file_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{path} cannot be read ({type(e).__name__}); move it aside and save the keys again") from e
    return data if isinstance(data, dict) else {}


def _write_file(data: dict[str, dict]) -> None:
    """Writes the file atomically, created with mode 0600 so no other user can ever read it."""
    path = file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".secrets-", dir=path.parent)
    try:
        os.chmod(tmp, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.chmod(path, FILE_MODE)


def _seal(value: str) -> dict:
    if sys.platform == "win32":
        return {"dpapi": base64.b64encode(_dpapi(value.encode("utf-8"), protect=True)).decode("ascii")}
    return {"v": value}


def _open(entry: dict) -> str | None:
    if "dpapi" in entry:
        return _dpapi(base64.b64decode(entry["dpapi"]), protect=False).decode("utf-8")
    value = entry.get("v")
    return value if isinstance(value, str) else None


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData for the current user (Windows only)."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]  # Windows only
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows only
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    ui_forbidden = 0x1   # CRYPTPROTECT_UI_FORBIDDEN: never show a dialog
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, ui_forbidden, ctypes.byref(blob_out))
    if not ok:
        raise OSError("Windows could not protect or read a saved key (DPAPI)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _file_get(name: str) -> str | None:
    entry = _read_file().get(name)
    return _open(entry) if isinstance(entry, dict) else None


def _file_put(name: str, value: str) -> None:
    data = _read_file()
    data[name] = _seal(value)
    _write_file(data)


def _file_delete(name: str) -> None:
    data = _read_file()
    if name in data:
        del data[name]
        _write_file(data)


def _record(name: str, verb: str) -> None:
    """History and Activity naming the key's Settings row, never its value."""
    from . import _registry, store

    row = next((s for s in _registry().REGISTRY.values() if s.kind == "secret" and s.secret_name == name), None)
    store.record_saved(row.key if row else name, text=f"{verb}: {row.label if row else name}",
                       why="You changed a key or password in Settings.")


# --- the contract --------------------------------------------------------------------------

def put(name: str, value: str) -> str:
    """Saves a secret and reads it back; returns "keyring" or "file". The person is acting, so it may unlock."""
    _check_name(name)
    value = (value or "").strip()
    if not value:
        raise ValueError("Type the key or password first.")
    if len(value) > MAX_SECRET_CHARS:
        raise ValueError("That is too long to be a key or password.")
    kr = _keyring()
    if kr is not None:
        try:
            if _locked(kr, unlock=True):
                raise SecretsLocked(name)
            _call(lambda: kr.set_password(SERVICE, name, value))
            if _call(lambda: kr.get_password(SERVICE, name)) == value:
                _file_delete(name)
                _record(name, "Saved")
                return "keyring"
            log.warning("the keyring did not keep %s; saving it in the protected file", name)
        except KeyringUnavailable as e:
            log.warning("could not save %s in the keyring (%s); saving it in the protected file", name, e)
    _file_put(name, value)
    if _file_get(name) != value:
        raise RuntimeError(f"Hal could not save {name}: the saved copy did not read back.")
    _record(name, "Saved")
    return "file"


def get(name: str, *, interactive: bool = False) -> str | None:
    """The secret, or None when it is not saved; raises `SecretsLocked` from the background."""
    _check_name(name)
    kr = _keyring()
    if kr is not None:
        try:
            if _locked(kr, unlock=interactive):
                in_file = _file_get(name)
                if in_file is not None:
                    return in_file
                raise SecretsLocked(name)
            value = _call(lambda: kr.get_password(SERVICE, name))
            if value is not None:
                return value
        except KeyringUnavailable as e:
            log.warning("could not read %s from the keyring (%s); trying the protected file", name, e)
    return _file_get(name)


def delete(name: str) -> None:
    """Forgets a secret everywhere Hal could have kept it."""
    _check_name(name)
    kr = _keyring()
    if kr is not None:
        try:
            if not _locked(kr, unlock=True) and _call(lambda: kr.get_password(SERVICE, name)) is not None:
                _call(lambda: kr.delete_password(SERVICE, name))
        except KeyringUnavailable as e:
            log.warning("could not remove %s from the keyring (%s)", name, e)
    _file_delete(name)
    _record(name, "Forgot")


def status(name: str) -> str:
    """"saved", "not saved" or "locked"; never the value, and never a prompt."""
    try:
        return "saved" if get(name, interactive=False) else "not saved"
    except SecretsLocked:
        return "locked"


def statuses() -> dict[str, str]:
    """`status()` of every secret."""
    return {name: status(name) for name in NAMES}


def backend() -> dict:
    """Where secrets go on this computer, and whether that store is locked now."""
    kr = _keyring()
    if kr is None:
        return {"name": FILE_STORE, "locked": False}
    try:
        locked = _locked(kr)
    except KeyringUnavailable:
        return {"name": FILE_STORE, "locked": False}
    name = WINDOWS_STORE if sys.platform == "win32" else DESKTOP_STORE
    return {"name": name, "locked": locked}


def retry() -> bool:
    """Asks the person to unlock the keyring (Retry on the page); True when it is still locked."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        return _locked(kr, unlock=True)
    except KeyringUnavailable:
        return True

"""Every folder Hal Employment reads or writes, resolved when it is asked for.

Nothing here is an import-time constant: tests, the importer and worktrees point everything elsewhere with
`HAL_EMPLOYMENT_HOME` or `set_override()`, and the owner's install keeps its old in-checkout data folder
until he moves it (the legacy layout). Standard library only, so the command line starts under any
Python 3.11 or newer before the venv trampoline has run.

The test guard: with `HAL_TEST_MODE=1`, or under the unittest runner, and no override, every data path
raises `RealDataInTests`, so a test that forgets its temporary home fails instead of reading real data.
`code_dir()` is exempt, because it is the code, never data.

The worktree guard: a linked git worktree (`.git` is a file) is a second working copy of the code, so with no
override it refuses folders under the account's real home and raises `RealDataFromWorktree`, test mode or
not. A server started in a worktree once created the real data folder that way (2026-10-22).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ENV_HOME = "HAL_EMPLOYMENT_HOME"
ENV_TEST = "HAL_TEST_MODE"
APP_DIR = "hal-employment"            # Linux folder names
WIN_DIR = "Hal Employment"            # Windows folder names, and the visible Documents folder on both
RECORD_DIR = "Employment Record"
HELPER_DIR = "Browser helper"
MARKER = ".hal-data"
IMPORTED_TAG = ".imported-"           # the importer's locked old copies, never used again
PATHS_FILE = "paths.toml"
XDG_USER_DIR_TIMEOUT_S = 5            # xdg-user-dir is a tiny shell script; it never needs longer
PRIVATE_MODE = 0o700

_lock = threading.Lock()
_overrides: list[Path] = []


class RealDataInTests(RuntimeError):
    """A test asked for a data folder without a temporary home, so it would have reached real data."""


class RealDataFromWorktree(RealDataInTests):
    """A build worktree asked for a data folder without a temporary home, so it would have reached real data."""


def _is_windows() -> bool:
    return os.name == "nt"


def platform_name() -> str:
    """"windows", "macos" or "linux", the names settings and the platform layer use."""
    if _is_windows():
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _under_test_runner() -> bool:
    """True when the running program is the unittest runner, which never runs tests/__init__.py."""
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    name = getattr(spec, "name", "") or ""
    if name.startswith("unittest"):
        return True
    return "unittest" in sys.modules and not name.startswith("jobhunt")


def home_override() -> Path | None:
    """The folder that holds everything instead of the platform folders, or None."""
    with _lock:
        if _overrides:
            return _overrides[-1]
    raw = os.environ.get(ENV_HOME, "").strip()
    return Path(raw).expanduser().absolute() if raw else None


def _guarded() -> Path | None:
    """The override; raises when there is none and this is a test run or a build worktree."""
    home = home_override()
    if home is None and (os.environ.get(ENV_TEST) == "1" or _under_test_runner()):
        raise RealDataInTests(
            f"A test asked for Hal's folders without a temporary home. Set {ENV_HOME} or use paths.set_override().")
    if home is None and _in_linked_worktree() and _reaches_account_home():
        raise RealDataFromWorktree(
            f"This copy of the code is a git worktree, which never uses your real folders. Set {ENV_HOME} to a "
            f"folder inside {code_dir()}.")
    return home


def _in_linked_worktree() -> bool:
    """True when the code is a linked git worktree, whose `.git` is a file pointing at the main repo."""
    return (code_dir() / ".git").is_file()


def _account_home() -> Path | None:
    """The account's own home from the password database, which `HOME` cannot redirect; None on Windows."""
    if _is_windows():
        return None
    import pwd

    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except KeyError:
        return None


def _reaches_account_home() -> bool:
    """True when the platform folders would land in the account's real home, outside the code folder."""
    real = _account_home()
    if real is None:
        return False
    code = code_dir()
    bases = [Path.home()] + [Path(raw) for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")
                             if (raw := os.environ.get(name, "").strip())]
    return any(b.resolve().is_relative_to(real) and not b.resolve().is_relative_to(code) for b in bases)


@contextmanager
def set_override(home: Path) -> Iterator[Path]:
    """Points every folder under `home` until the block ends (tests, the importer's dry run)."""
    home = Path(home).expanduser().absolute()
    with _lock:
        _overrides.append(home)
    try:
        yield home
    finally:
        with _lock:
            _overrides.remove(home)


def code_dir() -> Path:
    """The checkout or install folder: the git clone with `.venv` and `extension/`."""
    return Path(__file__).resolve().parents[2]


def _env_dir(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw and Path(raw).is_absolute() else fallback


def _appdata() -> Path:
    return _env_dir("APPDATA", Path.home() / "AppData" / "Roaming")


def _localappdata() -> Path:
    return _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local")


def _windows_documents() -> Path:
    """The Documents known folder, following OneDrive redirection (SHGetKnownFolderPath)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}, from KnownFolders.h
    folder = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    out = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]  # windll exists only on Windows
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out)) != 0:
            return Path.home() / "Documents"
        return Path(out.value or (Path.home() / "Documents"))
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]


def _linux_documents() -> Path:
    """`xdg-user-dir DOCUMENTS`, else ~/Documents."""
    try:
        done = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True,
                              timeout=XDG_USER_DIR_TIMEOUT_S, check=False)
        found = done.stdout.strip()
        # xdg-user-dir answers $HOME itself when DOCUMENTS is not set up, which is no place for our folders
        if done.returncode == 0 and found and Path(found) != Path.home():
            return Path(found)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path.home() / "Documents"


def _user_documents() -> Path:
    return _windows_documents() if _is_windows() else _linux_documents()


def config_dir() -> Path:
    """Settings files that are not in the database: the local API key, install id, optional paths.toml."""
    home = _guarded()
    if home is not None:
        return home / "config"
    if _is_windows():
        return _appdata() / WIN_DIR / "config"
    return _env_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP_DIR


def _default_data_dir() -> Path:
    if _is_windows():
        return _appdata() / WIN_DIR / "data"
    return _env_dir("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP_DIR


def _legacy_data_dir() -> Path:
    return code_dir() / "data"


def _refuse_imported(path: Path) -> Path:
    if IMPORTED_TAG in path.name:
        raise RuntimeError(f"{path} is an old copy kept after moving your data; Hal never uses it again.")
    return path


def _configured_data_dir() -> Path | None:
    """`data_dir` from config/paths.toml, for a person who keeps the data elsewhere."""
    file = config_dir() / PATHS_FILE
    if not file.is_file():
        return None
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8")).get("data_dir")
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"{file} cannot be read: {e}") from e
    return Path(raw).expanduser() if raw else None


def is_developer_checkout() -> bool:
    """True when the code is a git checkout the person made, not a folder a bootstrap installed."""
    code = code_dir()
    if not (code / ".git").exists():
        return False
    if _is_windows():
        installed = _localappdata() / "Programs" / WIN_DIR / "app"
    else:
        installed = Path.home() / ".local" / "opt" / APP_DIR / "app"
    return code != installed


def is_legacy_layout() -> bool:
    """True while the owner's data still lives in the checkout's data/ folder."""
    if _guarded() is not None or _configured_data_dir() is not None:
        return False
    if (_default_data_dir() / MARKER).exists():
        return False
    return (_legacy_data_dir() / "jobs.db").exists() and is_developer_checkout()


def data_dir() -> Path:
    """The data folder, in the resolution order above."""
    home = _guarded()
    if home is not None:
        return home / "data"
    configured = _configured_data_dir()
    if configured is not None:
        return _refuse_imported(configured)
    if is_legacy_layout():
        return _legacy_data_dir()
    return _default_data_dir()


def cache_dir() -> Path:
    """Page images, print folders and fetched pages: anything Hal can make again."""
    home = _guarded()
    if home is not None:
        return home / "cache"
    if _is_windows():
        return _localappdata() / WIN_DIR / "cache"
    return _env_dir("XDG_CACHE_HOME", Path.home() / ".cache") / APP_DIR


def documents_dir() -> Path:
    """Documents/Hal Employment: the folder a person opens to find their applications."""
    home = _guarded()
    if home is not None:
        return home / "documents"
    return _user_documents() / WIN_DIR


def applications_dir() -> Path:
    """Where application folders go: the `cvdocs.folder` setting when the person changed it."""
    from .settings import store

    chosen = store.stored("cvdocs.folder")
    return Path(chosen).expanduser() if chosen else documents_dir() / "Applications"


def helper_dir() -> Path:
    """The visible copy of the browser helper, outside hidden and OneDrive-synced folders."""
    home = _guarded()
    if home is not None or not _is_windows():
        return documents_dir() / HELPER_DIR
    return Path(os.environ.get("USERPROFILE") or Path.home()) / WIN_DIR / HELPER_DIR


def record_default() -> Path:
    """The default record folder, before the person picks one."""
    home = _guarded()
    if home is not None:
        return home / "record"
    return _user_documents() / RECORD_DIR


def db_path() -> Path:
    """The one SQLite database."""
    return data_dir() / "jobs.db"


def private_path() -> Path:
    """The private answers file (never read here)."""
    return data_dir() / "private.toml"


def browser_profile() -> Path:
    """Hal's own browser profile for window mode."""
    return data_dir() / "browser-profile"


def shots_dir() -> Path:
    """Form screenshots."""
    return data_dir() / "apply"


def cv_dir() -> Path:
    """CV files and working copies."""
    return data_dir() / "cv"


def backups_dir() -> Path:
    """Database snapshots."""
    return data_dir() / "backups"


def logs_dir() -> Path:
    """Server and child-process logs."""
    return data_dir() / "logs"


def state_dir() -> Path:
    """Lock files, the pid file and update state."""
    return data_dir() / "state"


def legacy_packs_dir() -> Path:
    """The old application packs: the checkout's packs/ in the legacy layout, else data/legacy-packs."""
    if is_legacy_layout():
        return code_dir() / "packs"
    return data_dir() / "legacy-packs"


def claude_work_dir() -> Path:
    """An empty folder for the Claude Code command line to run in."""
    return data_dir() / "claude-work"


def _write_marker(data: Path) -> None:
    marker = data / MARKER
    if marker.exists():
        return
    from .. import __version__

    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker.write_text(json.dumps({"created": created, "kind": "fresh", "source": None, "code_version": __version__,
                                  "counts": {}}) + "\n", encoding="utf-8")


def ensure() -> None:
    """Makes every folder with the right modes and marks a fresh data folder. Safe to call again."""
    private = (config_dir(), data_dir())
    for folder in (*private, cache_dir(), state_dir(), logs_dir(), backups_dir(), cv_dir(), claude_work_dir()):
        folder.mkdir(parents=True, exist_ok=True)
    if not _is_windows():
        for folder in private:
            folder.chmod(PRIVATE_MODE)
    if not is_legacy_layout():
        _write_marker(data_dir())

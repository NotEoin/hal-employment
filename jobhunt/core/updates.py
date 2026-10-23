"""Hal's own updater: check, fetch, verify, merge, sync, migrate, restart, and go back.

`scripts/updater.py` runs this under uv's managed Python, outside `.venv`, so `uv sync` can replace `.venv`
while it runs; the server imports it only to read the install record and the last result. It therefore uses
the standard library and `core.paths` only (a test holds it to that).

Design decisions, each with its reason:

- Everything outside this process (git, uv, the migration, stopping and starting the server, asking it
  which commit it runs) goes through one `World` object, so the tests drive the real steps against local
  bare repositories with a fake uv and a fake server.
- Only the owner's commits reach friends: every commit in `HEAD..origin/main` is checked with
  `git verify-commit` against the `allowed_signers` of the commit already installed, so a push cannot bring
  its own key. While that file holds no key (the owner has not set up signing yet), updates install
  unchecked and `signing()` says so in plain words; from the first commit that adds a key, every later
  commit must verify.
- A failed commit is held (`state/update-status.json` `held`, mirrored into the `updates.held_commit`
  setting by the app), so the daily check does not offer it again.
- Rollback uses `git reset --keep`, which never discards files the person added.
- The result is written to `state/update-status.json` with `recorded: false`; the server reads it at its
  next start and writes the Activity row, because the updater has no settings or Activity code of its own.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger(__name__)

INSTALL_FILE = "install.json"          # state/: written by `jobhunt install` for a user install
STATUS_FILE = "update-status.json"     # state/: the last check or update, shown by the app
RECORD_FILE = "update.json"            # state/: {from, to, backup}, for rollback and "Go back"
CHECK_FILE = "update-check.json"       # state/: the last check, for Settings and Home
LOCK_FILE = "update.lock"              # state/: one updater at a time
WAITING_FILE = "update-waiting"        # state/: start the update when the form being filled is done
PID_FILE = "server.pid"                # state/: written by the server at start
SIGNERS = "scripts/install/allowed_signers"
REMOTE, BRANCH = "origin", "main"
BIND = "127.0.0.1"
DEFAULT_PORT = 8737
GIT_TIMEOUT_S = 60
FETCH_TIMEOUT_S = 120
SYNC_TIMEOUT_S = 15 * 60
MIGRATE_TIMEOUT_S = 10 * 60
STOP_WAIT_S = 30
HEALTH_WAIT_S = 90
HTTP_TIMEOUT_S = 5
POLL_S = 0.5
WHATS_NEW_MAX = 20
SHORT = 7
PYTHON = "3.13"
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)

# Words on screen; OWNER is filled from scripts/install/repo.toml.
DEVELOPER = "You run Hal from your own folder. Update it with git."
CHANGED_BY_HAND = "Hal's files were changed by hand, so it cannot update by itself. Ask the person who set Hal up."
FILLING = "Hal is filling in a form. It will update when it has finished."
BUSY = "Hal is already updating."
NO_NETWORK = "Hal could not reach GitHub. Check your internet connection."
SIGN_IN_AGAIN = "Hal cannot reach its updates on GitHub. Sign in to GitHub again."
NO_ACCESS = "Your GitHub account no longer has access to Hal's files. Ask {owner} to add you again."
UP_TO_DATE = "Hal is up to date."
NOT_OWNER = "This update was not made by the person who set Hal up, so Hal did not install it. Ask them."
NEEDS_OWNER = "This update needs the person who set Hal up."
WENT_BACK = "The update did not work, so Hal went back to the version you had. Nothing was lost."
UPDATED = "Updated Hal from {old} to {new}"
WENT_BACK_BY_HAND = "Went back to Hal {version}"
NOTHING_TO_GO_BACK_TO = "There is no earlier version to go back to."
UNCHECKED = "Updates are not yet checked for the owner's signature: they have not set up signing."
CHECKED = "Every update is checked: it must be signed by the person who set Hal up."
GO_BACK_WARNING = "Changes you made since the update, such as new applications, will be lost."


class UpdateError(Exception):
    """A stop with plain words for the person and the technical details for Details."""

    def __init__(self, words: str, details: str = "", *, state: str = "failed") -> None:
        super().__init__(words)
        self.words, self.details, self.state = words, details, state


# ---------------------------------------------------------------------------------------------- install record

@dataclass
class Install:
    """What the bootstrap recorded: a user install, or a developer checkout when nothing was."""

    kind: str = "developer"
    code: str = ""
    tools: str = ""
    git: str = "git"
    uv: str = ""
    python_dir: str = ""
    uv_cache: str = ""
    extras: list[str] = field(default_factory=list)
    installed: str = ""

    @property
    def developer(self) -> bool:
        """True for any checkout the bootstrap did not make, such as the owner's."""
        return self.kind != "user"

    @property
    def code_dir(self) -> Path:
        """The clone the updater works on."""
        return Path(self.code) if self.code else paths.code_dir()

    def uv_env(self) -> dict[str, str]:
        """The uv folders the bootstrap used, so uv never downloads Python a second time."""
        out = {}
        if self.python_dir:
            out["UV_PYTHON_INSTALL_DIR"] = self.python_dir
        if self.uv_cache:
            out["UV_CACHE_DIR"] = self.uv_cache
        return out


def install_file() -> Path:
    """`state/install.json`."""
    return paths.state_dir() / INSTALL_FILE


def read_install() -> Install:
    """The install record; a developer checkout when there is none."""
    file = install_file()
    if not file.is_file():
        return Install(code=str(paths.code_dir()))
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UpdateError("Hal's install record cannot be read. Run the install file again.", str(e)) from e
    known = {k: v for k, v in raw.items() if k in Install.__dataclass_fields__}
    return Install(**known)


def write_install(inst: Install) -> Path:
    """Writes the install record atomically."""
    file = install_file()
    _write_json(file, asdict(inst))
    return file


# ---------------------------------------------------------------------------------------------- small files

def _write_json(file: Path, data: dict) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, file)


def _read_json(file: Path) -> dict:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status() -> dict:
    """The last check or update, as the app shows it; empty before the first."""
    return _read_json(paths.state_dir() / STATUS_FILE)


def write_status(data: dict) -> None:
    """Replaces the status file; the held commit survives every write."""
    old = status()
    data = {**data}
    data.setdefault("held", old.get("held", ""))
    data.setdefault("at", utc_now())
    _write_json(paths.state_dir() / STATUS_FILE, data)


def mark_recorded() -> dict:
    """Marks the last result as shown in Activity and returns it."""
    data = status()
    if data:
        data["recorded"] = True
        _write_json(paths.state_dir() / STATUS_FILE, data)
    return data


def last_check() -> dict:
    """The last check's result (`check()`'s reply plus `at`); empty before the first."""
    return _read_json(paths.state_dir() / CHECK_FILE)


def write_check(result: dict) -> None:
    """Keeps a check's result for Settings and Home."""
    _write_json(paths.state_dir() / CHECK_FILE, {**result, "at": result.get("at") or utc_now()})


def record() -> dict:
    """{from, to, backup, done} of the last update, for "Go back"."""
    return _read_json(paths.state_dir() / RECORD_FILE)


def utc_now() -> str:
    """Now as `2026-10-23T09:00:00Z`."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def waiting() -> bool:
    """True when an update waits for a form to be finished."""
    return (paths.state_dir() / WAITING_FILE).exists()


def set_waiting(on: bool) -> None:
    """Sets or clears the "update when the form is done" flag."""
    flag = paths.state_dir() / WAITING_FILE
    if on:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(utc_now() + "\n", encoding="utf-8")
    else:
        with contextlib.suppress(FileNotFoundError):
            flag.unlink()


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """`state/update.lock`, taken without waiting; `UpdateError(BUSY)` when another updater holds it."""
    file = paths.state_dir() / LOCK_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(file, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise UpdateError(BUSY, str(e), state="refused") from e
        yield
    finally:
        handle.close()


def repo_owner() -> str:
    """The GitHub account that owns Hal's files, from `scripts/install/repo.toml`, for the words."""
    import tomllib

    file = paths.code_dir() / "scripts" / "install" / "repo.toml"
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "the person who set Hal up"
    return str(data.get("owner_name") or str(data.get("slug", "")).split("/")[0] or "the person who set Hal up")


# ---------------------------------------------------------------------------------------------- the outside world

class World:
    """Everything the updater asks of the computer; tests subclass it."""

    def __init__(self, inst: Install, *, interactive: bool = False, say: Callable[[str], None] | None = None,
                 log_file: Path | None = None) -> None:
        self.inst, self.interactive = inst, interactive
        self._say = say
        self.log_file = log_file

    # reporting
    def say(self, line: str) -> None:
        """One progress line: the console (when run there) and the update log."""
        log.info("update: %s", line)
        if self._say:
            self._say(line)
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{utc_now()} {line}\n")

    # commands
    def env(self) -> dict[str, str]:
        """The environment for git and uv; background runs never prompt for a sign-in."""
        env = {**os.environ, **self.inst.uv_env()}
        env.pop("HAL_EMPLOYMENT_NO_TRAMPOLINE", None)
        if not self.interactive:
            env["GIT_TERMINAL_PROMPT"] = "0"
            env["GCM_INTERACTIVE"] = "never"
        return env

    def run(self, args: list[str], *, timeout: float, check: bool = True) -> subprocess.CompletedProcess:
        """Runs a command in the clone with a timeout; `UpdateError` with its output when it fails."""
        flags = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW: no console flashes up
        try:
            done = subprocess.run(args, cwd=str(self.inst.code_dir), env=self.env(), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
                                  stdin=subprocess.DEVNULL, creationflags=flags)
        except subprocess.TimeoutExpired as e:
            raise UpdateError("Hal's update took too long and was stopped.", f"{args[0]}: {e}") from e
        except OSError as e:
            raise UpdateError("Hal could not run one of its tools.", f"{args[0]}: {e}") from e
        if check and done.returncode != 0:
            raise UpdateError("Hal's update stopped.", f"{' '.join(args[:3])}: {done.stderr.strip()[-2000:]}")
        return done

    def git(self, *args: str, timeout: float = GIT_TIMEOUT_S, check: bool = True) -> subprocess.CompletedProcess:
        """Runs the install's own git (the bundled one on Windows)."""
        return self.run([self.inst.git or "git", *args], timeout=timeout, check=check)

    def sync(self) -> None:
        """`uv sync --frozen --no-dev` with the install's extras."""
        uv = self.inst.uv or shutil.which("uv") or "uv"
        extras = [a for e in self.inst.extras for a in ("--extra", e)]
        self.run([uv, "sync", "--frozen", "--no-dev", *extras], timeout=SYNC_TIMEOUT_S)

    def venv_python(self, windowless: bool = False) -> str:
        """The clone's `.venv` Python (`pythonw.exe` on Windows for a server with no console)."""
        code = self.inst.code_dir
        if os.name == "nt":
            return str(code / ".venv" / "Scripts" / ("pythonw.exe" if windowless else "python.exe"))
        return str(code / ".venv" / "bin" / "python")

    def migrate(self) -> None:
        """`python -m jobhunt migrate`, which backs up again first."""
        self.run([self.venv_python(), "-X", "utf8", "-m", "jobhunt", "migrate"], timeout=MIGRATE_TIMEOUT_S)

    # the server
    def port(self) -> int:
        """The port Hal listens on: `HAL_EMPLOYMENT_PORT`, else the stored `advanced.port`, else 8737."""
        raw = os.environ.get("HAL_EMPLOYMENT_PORT", "").strip()
        if raw.isdigit():
            return int(raw)
        value = _setting("advanced.port")
        return int(value) if isinstance(value, int) or (isinstance(value, str) and value.isdigit()) else DEFAULT_PORT

    def _request(self, method: str, path: str) -> tuple[int, dict]:
        key_file = paths.config_dir() / "local-api-key"
        headers = {"Host": f"{BIND}:{self.port()}", "X-Hal": "1"}
        if key_file.is_file():
            headers["Authorization"] = "Bearer " + key_file.read_text(encoding="utf-8").strip()
        con = http.client.HTTPConnection(BIND, self.port(), timeout=HTTP_TIMEOUT_S)
        try:
            con.request(method, path, body=b"{}" if method == "POST" else None,
                        headers={**headers, **({"Content-Type": "application/json"} if method == "POST" else {})})
            reply = con.getresponse()
            body = reply.read()
            try:
                return reply.status, json.loads(body or b"{}")
            except ValueError:
                return reply.status, {}
        finally:
            con.close()

    def apply_running(self) -> bool:
        """True while an `apply.*` action (Hal filling in a form) is queued or running."""
        db = paths.data_dir() / "jobs.db"
        if not db.is_file():
            return False
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT count(*) FROM actions WHERE kind LIKE 'apply.%' "
                              "AND state IN ('queued', 'running')").fetchone()
            return bool(row and row[0])
        except sqlite3.Error:
            return False
        finally:
            con.close()

    def stop_server(self) -> None:
        """Asks Hal to stop, waits for its process, else ends it."""
        try:
            code, body = self._request("POST", "/api/v2/system/shutdown")
        except OSError:
            self.say("Hal was not running.")
            return
        if code == 409:
            raise UpdateError(FILLING, json.dumps(body), state="refused")
        pid = _read_pid()
        deadline = time.monotonic() + STOP_WAIT_S
        while pid and _alive(pid) and time.monotonic() < deadline:
            time.sleep(POLL_S)
        if pid and _alive(pid):
            _end(pid)

    def start_server(self) -> None:
        """Starts Hal the way it was started: its user unit on Linux, else a detached server."""
        unit = Path.home() / ".config" / "systemd" / "user" / "hal-employment-app.service"
        if os.name != "nt" and unit.is_file() and shutil.which("systemctl"):
            self.run(["systemctl", "--user", "restart", unit.name], timeout=GIT_TIMEOUT_S)
            return
        args = [self.venv_python(windowless=True), "-X", "utf8", "-m", "jobhunt", "serve", "--background"]
        if os.name == "nt":
            flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED_PROCESS, NEW_PROCESS_GROUP, BREAKAWAY
            subprocess.Popen(args, cwd=str(self.inst.code_dir), env=self.env(), creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(args, cwd=str(self.inst.code_dir), env=self.env(), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def running_commit(self) -> str | None:
        """The commit the running server reports, or None while it does not answer."""
        try:
            code, body = self._request("GET", "/api/v2/version")
        except OSError:
            return None
        return str(body.get("commit") or "") or None if code == 200 else None

    def wait_for(self, sha: str, timeout: float = HEALTH_WAIT_S) -> None:
        """Waits until the server reports `sha`."""
        deadline = time.monotonic() + timeout
        seen = None
        while time.monotonic() < deadline:
            seen = self.running_commit()
            if seen and (sha.startswith(seen) or seen.startswith(sha)):
                return
            time.sleep(POLL_S)
        raise UpdateError("Hal did not start after the update.", f"wanted {sha[:SHORT]}, saw {seen}")


def _setting(key: str) -> Any:
    """A stored setting read straight from the database (the updater has no settings code)."""
    db = paths.data_dir() / "jobs.db"
    if not db.is_file():
        return None
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError):
        return None
    finally:
        con.close()


def _read_pid() -> int | None:
    try:
        return int((paths.state_dir() / PID_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    if os.name == "nt":
        done = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                              timeout=HTTP_TIMEOUT_S, check=False)
        return str(pid) in done.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _end(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=HTTP_TIMEOUT_S,
                       check=False)
        return
    import signal

    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)


# ---------------------------------------------------------------------------------------------- steps

def held_commit() -> str:
    """The commit a failed update held back, from the status file or the `updates.held_commit` setting."""
    return str(status().get("held") or _setting("updates.held_commit") or "")


def refusal(world: World) -> UpdateError | None:
    """Why an update must not run now, or None."""
    if world.inst.developer:
        return UpdateError(DEVELOPER, state="developer")
    dirty = world.git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        return UpdateError(CHANGED_BY_HAND, dirty[:2000], state="refused")
    if world.apply_running():
        return UpdateError(FILLING, state="waiting")
    return None


def fetch(world: World) -> None:
    """`git fetch --prune origin main` (120 s), with each failure in plain words."""
    done = world.git("fetch", "--prune", REMOTE, BRANCH, timeout=FETCH_TIMEOUT_S, check=False)
    if done.returncode == 0:
        return
    err = done.stderr.lower()
    if any(w in err for w in ("403", "401", "authentication failed", "could not read username",
                              "terminal prompts disabled", "invalid credentials")):
        raise UpdateError(SIGN_IN_AGAIN, done.stderr.strip(), state="sign_in")
    if "not found" in err or "404" in err:
        raise UpdateError(NO_ACCESS.format(owner=repo_owner()), done.stderr.strip())
    raise UpdateError(NO_NETWORK, done.stderr.strip())


def _rev(world: World, name: str) -> str:
    return world.git("rev-parse", name).stdout.strip()


def version_at(world: World, rev: str) -> str:
    """Hal's version (`jobhunt/__init__.py`) at a commit."""
    done = world.git("show", f"{rev}:jobhunt/__init__.py", check=False)
    m = VERSION_RE.search(done.stdout or "")
    return m.group(1) if m else rev[:SHORT]


def whats_new(world: World, old: str, new: str) -> list[str]:
    """The commit subjects from `old` to `new`, newest first; the repo writes them in plain words."""
    done = world.git("log", "--format=%s", f"{old}..{new}", check=False)
    return [s for s in done.stdout.splitlines() if s.strip()][:WHATS_NEW_MAX]


def signers_text(world: World, rev: str = "HEAD") -> str:
    """`allowed_signers` as the installed commit has it; empty when the file is missing."""
    done = world.git("show", f"{rev}:{SIGNERS}", check=False)
    return done.stdout if done.returncode == 0 else ""


def has_keys(text: str) -> bool:
    """True when an allowed-signers text holds at least one key line (not only comments)."""
    return any(line.strip() and not line.strip().startswith("#") for line in text.splitlines())


def signing(world: World | None = None) -> dict:
    """{checked, words}: whether updates are checked for the owner's signature on this install."""
    if world is None:
        text = _file_text(paths.code_dir() / Path(SIGNERS))
    else:
        text = signers_text(world)
    on = has_keys(text)
    return {"checked": on, "words": CHECKED if on else UNCHECKED}


def _file_text(file: Path) -> str:
    try:
        return file.read_text(encoding="utf-8")
    except OSError:
        return ""


def _ssh_keygen(world: World) -> list[str]:
    """`-c gpg.ssh.program=...` for the bundled Git's ssh-keygen on Windows, where PATH may lack one."""
    if not world.inst.tools:
        return []
    exe = Path(world.inst.tools) / "git" / "usr" / "bin" / "ssh-keygen.exe"
    return ["-c", f"gpg.ssh.program={exe}"] if exe.is_file() else []


def _signers_for(world: World, sha: str, new: set[str]) -> str:
    """The `allowed_signers` that `sha` is checked against.

    Its first parent's copy when that parent is itself a new commit (checked before it, so its file is
    trusted), else the installed commit's copy. A push therefore cannot bring its own key, and once a key is
    committed every later commit must verify, even within one update.
    """
    parent = world.git("rev-parse", "--verify", "--quiet", f"{sha}^1", check=False).stdout.strip()
    return signers_text(world, parent if parent in new else "HEAD")


def verify(world: World, commits: list[str]) -> bool:
    """Checks the SSH signature of every new commit (`rev-list` order, newest first), oldest first.

    Returns True when every commit was checked, False when some installed unchecked because the file they
    are checked against holds no key yet (the owner has not set up signing); raises `UpdateError(NOT_OWNER)`
    naming the first commit that fails.
    """
    new, files, every = set(commits), {}, True
    folder = Path(tempfile.mkdtemp(prefix="hal-signers-", dir=str(paths.state_dir())))
    try:
        for sha in reversed(commits):
            text = _signers_for(world, sha, new)
            if not has_keys(text):
                every = False
                continue
            if text not in files:
                files[text] = folder / f"allowed_signers-{len(files)}"
                files[text].write_text(text, encoding="utf-8", newline="\n")
            done = world.git("-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={files[text].as_posix()}",
                             *_ssh_keygen(world), "verify-commit", sha, check=False)
            if done.returncode != 0:
                raise UpdateError(NOT_OWNER, f"{sha[:SHORT]}: {done.stderr.strip()[-500:]}", state="not_owner")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not every:
        world.say("Some commits were not checked for a signature: the owner has not set up signing yet.")
    return every


def backup_db(sha: str) -> Path | None:
    """`backups/jobs-<UTC>Z-before-update-<sha7>.db` through SQLite's backup API."""
    db = paths.data_dir() / "jobs.db"
    if not db.is_file():
        return None
    folder = paths.backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"jobs-{_stamp()}Z-before-update-{sha[:SHORT]}.db"
    src = sqlite3.connect(db, timeout=30)
    dest = sqlite3.connect(out)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return out


def restore_db(backup: Path) -> None:
    """Puts a backup back over `jobs.db` (the server is stopped)."""
    db = paths.data_dir() / "jobs.db"
    src = sqlite3.connect(backup)
    dest = sqlite3.connect(db, timeout=30)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()


def check(world: World) -> dict:
    """Steps 1 to 3: whether an update is waiting, what is new, and whether its commits are signed."""
    stop = refusal(world)
    if stop is not None and stop.state == "developer":
        raise stop
    fetch(world)
    head, new = _rev(world, "HEAD"), _rev(world, f"{REMOTE}/{BRANCH}")
    commits = world.git("rev-list", f"HEAD..{REMOTE}/{BRANCH}").stdout.split()
    held = held_commit()
    out = {"commit": head, "newest": new, "behind": len(commits), "held": held, "signing": signing(world)}
    if not commits or new == held:
        out.update(available=False, words=UP_TO_DATE, whats_new=[])
        return out
    verify(world, commits)
    out.update(available=True, whats_new=whats_new(world, head, new), version=version_at(world, new),
               words=f"Hal {version_at(world, new)} is ready.")
    return out


def check_and_keep(world: World) -> dict:
    """`check()`, with its result (or its failure in words) kept for Settings and Home."""
    try:
        result = check(world)
    except UpdateError as e:
        write_check({"available": False, "state": e.state, "words": e.words, "details": e.details})
        raise
    write_check({**result, "state": "ok"})
    return result


def update(world: World) -> dict:
    """The whole update; returns the status it wrote. Raises nothing: failures are in the status."""
    try:
        with _locked():
            return _update(world)
    except UpdateError as e:
        result = {"state": e.state, "words": e.words, "details": e.details, "recorded": False}
        write_status(result)
        world.say(e.words)
        return status()


def _update(world: World) -> dict:
    stop = refusal(world)
    if stop is not None:
        if stop.state == "waiting":
            set_waiting(True)
        raise stop
    set_waiting(False)
    world.say("Looking for a new version")
    fetch(world)
    head, new = _rev(world, "HEAD"), _rev(world, f"{REMOTE}/{BRANCH}")
    commits = world.git("rev-list", f"HEAD..{REMOTE}/{BRANCH}").stdout.split()
    if not commits or new == held_commit():
        write_status({"state": "up_to_date", "words": UP_TO_DATE, "commit": head, "recorded": True})
        world.say(UP_TO_DATE)
        return status()
    if world.git("merge-base", "--is-ancestor", "HEAD", f"{REMOTE}/{BRANCH}", check=False).returncode != 0:
        raise UpdateError(NEEDS_OWNER, f"{head[:SHORT]} is not behind {new[:SHORT]}", state="refused")
    checked = verify(world, commits)
    old_version, new_version = version_at(world, head), version_at(world, new)
    news = whats_new(world, head, new)
    _write_json(paths.state_dir() / RECORD_FILE, {"from": head, "to": new, "at": utc_now(), "done": False})
    write_status({"state": "running", "words": "Hal is updating.", "from": head, "to": new, "recorded": True})
    world.say("Stopping Hal")
    world.stop_server()
    backup = backup_db(head)
    _write_json(paths.state_dir() / RECORD_FILE, {"from": head, "to": new, "at": utc_now(), "done": False,
                                                  "backup": str(backup) if backup else ""})
    migrated = False
    try:
        world.say("Getting the new version")
        world.git("merge", "--ff-only", f"{REMOTE}/{BRANCH}")
        world.say("Setting up the new version")
        world.sync()
        world.say("Hal is updating its records")
        migrated = True
        world.migrate()
        world.say("Starting Hal")
        world.start_server()
        world.wait_for(new)
    except UpdateError as e:
        world.say(f"The update failed: {e.details or e.words}")
        _roll_back(world, head, backup if migrated else None)
        write_status({"state": "failed", "words": WENT_BACK, "details": e.details or e.words, "from": head,
                      "to": new, "held": new, "recorded": False})
        return status()
    _write_json(paths.state_dir() / RECORD_FILE, {"from": head, "to": new, "at": utc_now(), "done": True,
                                                  "backup": str(backup) if backup else "",
                                                  "from_version": old_version, "to_version": new_version})
    words = UPDATED.format(old=old_version, new=new_version)
    write_status({"state": "updated", "words": words, "from": head, "to": new, "from_version": old_version,
                  "to_version": new_version, "whats_new": news, "signed": checked, "recorded": False,
                  "held": ""})
    world.say(words)
    return status()


def _roll_back(world: World, to: str, backup: Path | None) -> None:
    """Spec 11.4 step 11: the old commit, its packages, the backup when a migration ran, the old server."""
    world.say("Going back to the version you had")
    world.git("reset", "--keep", to)
    try:
        world.sync()
    except UpdateError as e:
        world.say(f"Setting up the old version again failed: {e.details}")
    if backup is not None:
        restore_db(backup)
    try:
        world.start_server()
        world.wait_for(to)
    except UpdateError as e:
        world.say(f"Hal did not start again by itself: {e.details}")


def go_back(world: World) -> dict:
    """"Go back to the previous version": the pre-update backup, the previous commit, sync, start."""
    try:
        with _locked():
            rec = record()
            if world.inst.developer:
                raise UpdateError(DEVELOPER, state="developer")
            if not rec.get("done") or not rec.get("from"):
                raise UpdateError(NOTHING_TO_GO_BACK_TO, state="refused")
            world.say("Stopping Hal")
            world.stop_server()
            backup = Path(rec["backup"]) if rec.get("backup") else None
            if backup is not None and backup.is_file():
                restore_db(backup)
            world.git("reset", "--keep", rec["from"])
            world.sync()
            world.start_server()
            world.wait_for(rec["from"])
            version = rec.get("from_version") or version_at(world, rec["from"])
            _write_json(paths.state_dir() / RECORD_FILE, {**rec, "done": False, "went_back": utc_now()})
            write_status({"state": "went_back", "words": WENT_BACK_BY_HAND.format(version=version),
                          "from": rec["to"], "to": rec["from"], "held": rec["to"], "recorded": False})
    except UpdateError as e:
        write_status({"state": e.state, "words": e.words, "details": e.details, "recorded": False})
    world.say(status().get("words", ""))
    return status()


def can_go_back() -> bool:
    """True after an update that went well, until "Go back" is used."""
    return bool(record().get("done"))


def log_path() -> Path:
    """`logs/update-<UTC>.log` for this run."""
    return paths.logs_dir() / f"update-{_stamp()}Z.log"


def main(argv: list[str] | None = None) -> int:
    """The updater's command line (`scripts/updater.py`): update, `--check` or `--rollback`."""
    import argparse

    p = argparse.ArgumentParser(prog="updater", description="Update Hal Employment, or go back.")
    p.add_argument("--check", action="store_true", help="only look for a new version")
    p.add_argument("--rollback", action="store_true", help="go back to the version before the last update")
    p.add_argument("--background", action="store_true", help="never ask for a GitHub sign-in")
    args = p.parse_args(argv)
    try:
        inst = read_install()
    except UpdateError as e:
        print(e.words)
        return 1
    world = World(inst, interactive=not args.background, say=print, log_file=log_path())
    if args.check:
        try:
            result = check_and_keep(world)
        except UpdateError as e:
            print(e.words)
            return 1
        print(result["words"])
        return 0
    result = go_back(world) if args.rollback else update(world)
    return 0 if result.get("state") in ("updated", "up_to_date", "went_back") else 1


if __name__ == "__main__":     # pragma: no cover - run through scripts/updater.py
    sys.exit(main())

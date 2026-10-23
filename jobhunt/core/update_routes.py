"""The updates routes: Settings, Updates; Update Hal; Go back; versions; shutdown for the updater.

Design decisions, each with its reason:

- Update Hal and Go back start `scripts/updater.py` as its own detached process (on Linux its own transient
  unit, `hal-employment-update`), because the updater stops this server: a child left in the server's
  cgroup would be killed with it, possibly between `git merge` and `uv sync`.
- The updater runs under uv's managed Python with `--no-project`, outside `.venv`, so `uv sync` can replace
  `.venv` while it runs. `systemd-run` does not pass the environment on, so the uv folders from the install
  record go in front of the command through `env`.
- At start the server makes the local key (the updater sends it to stop the server), writes
  `state/server.pid` (the updater waits for that process to end) and turns the
  updater's last result into an Activity row, because the updater has no Activity code of its own.
- An update that waits for a form to be finished is started again by a small watcher thread here, once no
  `apply.*` action is left.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from .. import __version__, db
from ..web import auth
from ..web.router import BadRequest, Conflict, Request, route
from . import activity, paths, plugins, runner, updates

log = logging.getLogger(__name__)

UPDATE_UNIT = "hal-employment-update"
EXIT_AFTER_S = 0.5            # the reply leaves before the process ends
WAIT_POLL_S = 30              # how often a waiting update looks whether the form is done
GIT_TIMEOUT_S = 10
PROTOCOL = 1                  # the browser helper protocol
STARTED = "Hal is updating. This page comes back in about a minute."
QUIT_WORDS = "Hal stopped. Open it again from its shortcut."

_exit = os._exit              # tests replace it
_watcher: list[threading.Thread] = []


def _apply_running(con) -> bool:
    row = con.execute("SELECT count(*) FROM actions WHERE kind LIKE 'apply.%' AND state IN ('queued', 'running')"
                      ).fetchone()
    return bool(row and row[0])


def _commit(inst: updates.Install) -> str | None:
    """The full commit of the code, read with the install's own git."""
    try:
        done = subprocess.run([inst.git or "git", "-C", str(paths.code_dir()), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def helper_wanted() -> str | None:
    """The browser helper version this app wants, from `extension/manifest.json`."""
    try:
        return json.loads((paths.code_dir() / "extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return None


def updater_argv(inst: updates.Install, *flags: str, system: str | None = None) -> list[str]:
    """The command that runs the updater outside `.venv`."""
    system = system or ("windows" if os.name == "nt" else "linux")
    code = inst.code_dir
    uv = inst.uv or "uv"
    argv = [uv, "run", "--no-project", "--python", updates.PYTHON, str(code / "scripts" / "updater.py"), *flags]
    env = [f"{k}={v}" for k, v in sorted(inst.uv_env().items())]
    if system != "windows" and env:
        argv = ["env", *env, *argv]
    return argv


def start_updater(*flags: str) -> int:
    """Spawns the updater detached, in its own unit on Linux; returns its pid."""
    from . import platform

    inst = updates.read_install()
    os.environ.update(inst.uv_env())    # Windows passes this process's environment to the child
    return platform.current().spawn_detached(updater_argv(inst, *flags), unit=UPDATE_UNIT)


def _blocked_by(con, inst: updates.Install) -> str | None:
    if inst.developer:
        return updates.DEVELOPER
    if _apply_running(con):
        return updates.FILLING
    return None


@route("GET", "/api/v2/updates")
def overview(req: Request) -> dict:
    """The rows of Settings, Updates."""
    inst = updates.read_install()
    check = updates.last_check()
    last = updates.status()
    held = updates.held_commit()
    available = bool(check.get("available")) and check.get("newest") != held
    return {
        "version": __version__, "commit": _commit(inst), "developer": inst.developer,
        "checked_at": check.get("at"), "available": available, "behind": check.get("behind", 0) if available else 0,
        "new_version": check.get("version") if available else None,
        "whats_new": check.get("whats_new", []) if available else last.get("whats_new", []),
        "check_words": check.get("words"), "check_state": check.get("state"),
        "last_result": {k: last.get(k) for k in ("state", "words", "details", "at", "from_version", "to_version")}
        if last else None,
        "can_rollback": updates.can_go_back() and not inst.developer, "blocked_by": _blocked_by(req.con, inst),
        "held": held or None, "waiting": updates.waiting(), "signing": updates.signing(),
        "go_back_warning": updates.GO_BACK_WARNING, "helper_wanted": helper_wanted(),
    }


@route("POST", "/api/v2/updates/check")
def check_now(req: Request) -> dict:
    """"Check now": a person's own press in a local session may open GitHub's sign-in window."""
    runner.flush()
    return {"action_id": runner.start("updates.check", params={"interactive": not req.remote},
                                      why="You pressed Check now", priority=0)}


@route("POST", "/api/v2/updates/apply")
def apply(req: Request) -> dict:
    """"Update Hal": refused in plain words when unsafe, else the updater starts on its own."""
    inst = updates.read_install()
    if inst.developer:
        raise Conflict(updates.DEVELOPER)
    if _apply_running(req.con):
        updates.set_waiting(True)
        watch_waiting()
        raise Conflict(updates.FILLING)
    activity.record("settings", "updates.apply", "Started updating Hal", "You pressed Update Hal",
                    remote=req.remote, session_id=req.session.id if req.session else None)
    start_updater(*([] if not req.remote else ["--background"]))
    return {"started": True, "words": STARTED}


@route("POST", "/api/v2/updates/rollback")
def rollback(req: Request) -> dict:
    """"Go back to the previous version", after the person confirmed the warning."""
    body = req.body if isinstance(req.body, dict) else {}
    if body.get("confirm") is not True:
        raise BadRequest(updates.GO_BACK_WARNING, field="confirm")
    inst = updates.read_install()
    if inst.developer:
        raise Conflict(updates.DEVELOPER)
    if not updates.can_go_back():
        raise Conflict(updates.NOTHING_TO_GO_BACK_TO)
    activity.record("settings", "updates.rollback", "Started going back to the previous version",
                    "You pressed Go back to the previous version", remote=req.remote)
    start_updater("--rollback", "--background")
    return {"started": True, "words": STARTED}


@route("GET", "/api/v2/updates/auth")
def auth_state(req: Request) -> dict:
    """GitHub sign-in state, as far as the last fetch showed it."""
    check = updates.last_check()
    how = "credential_manager" if os.name == "nt" else "gh"
    if not check:
        return {"signed_in": None, "account": None, "how": how}
    return {"signed_in": check.get("state") != "sign_in", "account": None, "how": how}


@route("POST", "/api/v2/updates/sign-in", local_only=True)
def sign_in(req: Request) -> dict:
    """"Sign in to GitHub again" (on the computer Hal runs on only)."""
    runner.flush()
    return {"action_id": runner.start("updates.sign_in", why="You pressed Sign in again", priority=0)}


def usb_install_folder() -> Path:
    """Where the stick's Hal installed itself on this computer (U setup.ps1:24)."""
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "HalEmployment"


@route("POST", "/api/v2/setup/import-usb", local_only=True)
def import_usb(req: Request) -> dict:
    """"We found an older Hal on this computer": copies it in as an action."""
    body = req.body if isinstance(req.body, dict) else {}
    folder = Path(str(body.get("path") or usb_install_folder())).expanduser()
    if not (folder / "data" / "jobs.db").is_file():
        raise BadRequest("There is no older Hal in that folder.", field="path")
    runner.flush()
    return {"action_id": runner.start("setup.import_usb", params={"path": str(folder)},
                                      why="You pressed Copy my old Hal", priority=0)}


@route("GET", "/api/v2/version", auth="session_or_local")
def version(req: Request) -> dict:
    """{app, commit, helper_wanted, protocol}; the updater checks `commit` after a restart, with the local key."""
    return {"app": __version__, "commit": _commit(updates.read_install()), "helper_wanted": helper_wanted(),
            "protocol": PROTOCOL}


def _exit_soon(code: int = 0) -> None:
    def later() -> None:
        runner.flush()
        _exit(code)

    timer = threading.Timer(EXIT_AFTER_S, later)
    timer.daemon = True
    timer.start()


@route("POST", "/api/v2/system/shutdown", auth="local")
def shutdown(req: Request) -> dict:
    """Used by the updater: ends the server after replying; running actions are marked interrupted at the next
    start. 409 while Hal fills in a form."""
    if _apply_running(req.con):
        raise Conflict(updates.FILLING)
    activity.record("system", "system.shutdown", "Stopped Hal to update it", "Hal's updater asked it to stop")
    _exit_soon()
    return {"ok": True}


@route("POST", "/api/v2/system/quit", auth="session_or_local", local_only=True)
def quit_hal(req: Request) -> dict:
    """Settings, Advanced, "Quit Hal": ends the server after replying."""
    activity.record("system", "system.quit", "Quit Hal", "You pressed Quit Hal")
    _exit_soon()
    return {"ok": True, "words": QUIT_WORDS}


# ---------------------------------------------------------------------------------------------- at start

def write_pid() -> Path:
    """`state/server.pid`, which the updater waits on."""
    file = paths.state_dir() / updates.PID_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return file


def absorb_result() -> int | None:
    """Turns the updater's last unrecorded result into an Activity row and holds a failed commit."""
    last = updates.status()
    if not last or last.get("recorded", True):
        return None
    state = last.get("state")
    if state in ("updated", "failed", "went_back") or last.get("words"):
        news = last.get("whats_new") or []
        detail = "\n".join(["What's new:", *news]) if news else (last.get("details") or None)
        tone = "good" if state in ("updated", "went_back") else "warn"
        row = activity.record("settings", f"updates.{state or 'result'}", last.get("words") or "Hal's update",
                              "Hal's updater ran" if state != "updated" else "You pressed Update Hal",
                              detail=detail, tone=tone)
    else:
        row = None
    if last.get("held"):
        try:
            from . import settings

            if settings.get("updates.held_commit") != last["held"]:
                settings.set("updates.held_commit", last["held"], by="hal")
        except (KeyError, ValueError) as e:
            log.warning("could not hold the failed update: %s", e)
    updates.mark_recorded()
    return row


def _watch_waiting() -> None:
    """Starts a waiting update once Hal has finished filling in forms."""
    stop = threading.Event()
    while not stop.wait(WAIT_POLL_S):
        if not updates.waiting():
            continue
        con = db.connect()
        try:
            busy = _apply_running(con)
        finally:
            con.close()
        if not busy:
            updates.set_waiting(False)
            log.info("the form is done; starting the update that waited for it")
            start_updater("--background")
            return


def watch_waiting() -> None:
    """Starts the watcher thread once per process."""
    if _watcher and _watcher[0].is_alive():
        return
    _watcher.clear()
    t = threading.Thread(target=_watch_waiting, name="update-wait", daemon=True)
    _watcher.append(t)
    t.start()


@plugins.on_start
def at_start() -> None:
    """Makes the local key, writes the pid, records the last update's result, and watches for a waiting update.

    The key is made here because nothing else makes it on a new install, and without it the updater's
    shutdown request is refused (Windows 11 test VM, 2026-10-23).
    """
    auth.local_api_key()
    write_pid()
    absorb_result()
    if updates.waiting():
        watch_waiting()

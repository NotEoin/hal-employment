"""Makes sure a Hal Employment server answers before a link is made for another computer.

Order: a server that already answers is used as it is; else the platform's own service is started (Hal's
`hal-employment.service` on the owner's desktop, `hal-employment-app.service` for a user install); else, or
when that unit did not start, Hal runs a server of its own as the transient unit `TRANSIENT_UNIT`.

Design decisions, each with its reason:

- `systemctl --user start` succeeds silently when a unit's start condition is unmet: Hal's own unit has
  `ConditionPathExists=!~/.local/state/hal/off` and stays inactive while Hal is switched off (checked
  2026-10-22). So the unit's state is read back with `is-active` instead of trusting the exit code.
- The transient server carries no Hal condition. When Hal's own unit starts later, it finds the server
  lock held and exits 0 with "Hal Employment is already running", so `Restart=on-failure` never loops.
- Every outside command has a timeout, and the runner is a parameter, so tests use fakes.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable

from ..core import platform
from ..web import app

log = logging.getLogger(__name__)

START_WAIT_S = 20.0          # open-url waits up to 20 s for /healthz
POLL_S = 0.25
TOOL_TIMEOUT_S = 10          # systemctl answers in well under a second unless something is wrong
TRANSIENT_UNIT = "hal-employment-remote"
UNITS = {"hal-unit": platform.HAL_UNIT, "app-unit": platform.APP_UNIT}
STARTING = ("active", "activating", "reloading")


class NotStarted(RuntimeError):
    """Hal could not be started; the message says why in plain words."""


def serve_argv(port: int) -> list[str]:
    """The command line of a server started for another computer."""
    return [sys.executable, "-m", "jobhunt", "serve", "--background", "--port", str(port)]


def _systemctl(run: Callable[..., subprocess.CompletedProcess], *args: str) -> subprocess.CompletedProcess | None:
    try:
        return run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=TOOL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("systemctl --user %s did not answer: %s", " ".join(args), e)
        return None


def _unit_started(run, unit: str) -> bool:
    """Starts `unit` and says whether it is running or on its way, rather than trusting start's exit code."""
    done = _systemctl(run, "start", unit)
    if done is None or done.returncode != 0:
        return False
    state = _systemctl(run, "is-active", unit)
    word = (state.stdout.strip() if state else "") or "unknown"
    if word not in STARTING:
        log.info("%s did not start (%s); Hal starts a server of its own", unit, word)
        return False
    return True


def _wait(port: int, wait_s: float) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if app.answering(port):
            return True
        time.sleep(POLL_S)
    return app.answering(port)


def ensure_running(port: int, *, plat=None, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                   wait_s: float | None = None) -> str:
    """Makes sure Hal answers on `port`; returns "running", "service" or "own server", else raises NotStarted."""
    wait_s = START_WAIT_S if wait_s is None else wait_s
    if app.answering(port):
        return "running"
    plat = plat or platform.current()
    unit = UNITS.get(plat.service()) if plat.name == "linux" else None
    if unit and _unit_started(run, unit) and _wait(port, wait_s):
        return "service"
    try:
        plat.spawn_detached(serve_argv(port), unit=TRANSIENT_UNIT)
    except OSError as e:
        raise NotStarted(f"Hal Employment could not be started on this computer: {e}") from e
    if _wait(port, wait_s):
        return "own server"
    raise NotStarted("Hal Employment did not start on this computer. "
                     "See logs/server.log in Hal's data folder.")

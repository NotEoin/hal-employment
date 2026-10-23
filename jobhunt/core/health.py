"""The "Is Hal working?" contract and the core's own checks: schedule, disk space, data folder, port.

Each area lists `CHECKS` in a `health.py` or `*_health.py` module. `run_all(con)` runs every check, and a
check that raises becomes a "bad" row naming it, so one broken area never hides the others. The status
line asks `problem_count(con)` on every reply, so the count is kept for `CACHE_S`.

The schedule check shows the owner's old timers as fine when the Hal add-on is on ("Searches, email
checks and the summary run from your own timers"), and as a warning with "Let Hal run them instead" for
anyone else.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import clock, paths, plugins

log = logging.getLogger(__name__)

CACHE_S = 60
LOW_DISK_BYTES = 2 * 1024 ** 3      # below this, backups and application folders may fail to write
BAD_DISK_BYTES = 500 * 1024 ** 2
GB = 1024 ** 3
LEVELS = ("ok", "warn", "bad")


@dataclass(frozen=True)
class Fix:
    """A button on a check: an action kind to start, or a route to open."""

    label: str
    action: str | None = None
    route: str | None = None


@dataclass(frozen=True)
class CheckResult:
    """What one check found."""

    ok: bool
    level: str            # "ok" | "warn" | "bad"
    text: str
    fix: Fix | None = None


@dataclass(frozen=True)
class Check:
    """One row of "Is Hal working?"."""

    id: str
    name: str
    run: Callable[[], CheckResult] = field(repr=False)


def ok(text: str) -> CheckResult:
    """A check that passed."""
    return CheckResult(True, "ok", text)


def warn(text: str, fix: Fix | None = None) -> CheckResult:
    """A check that needs a look."""
    return CheckResult(False, "warn", text, fix)


def bad(text: str, fix: Fix | None = None) -> CheckResult:
    """A check that failed."""
    return CheckResult(False, "bad", text, fix)


# ---------------------------------------------------------------------------------------------- the core's checks

def _clock_words(iso_text: str) -> str:
    at = clock.local(clock.parse(iso_text))
    today = clock.local_day()
    return f"{at:%H:%M}" if at.date() == today else f"{at:%A} at {at:%H:%M}"


def schedule_check() -> CheckResult:
    """The next search, and whether the old timers run jobs instead."""
    from . import scheduler, settings

    timers = scheduler.timers_in_charge()
    if timers:
        if settings.get("addon.enabled"):
            return ok("Searches, email checks and the summary run from your own timers.")
        return warn("Your old timers still run searches.",
                    Fix("Let Hal run them instead", action="/schedule/retire-timers"))
    if scheduler.paused():
        return warn("Hal is paused. Email checks carry on.", Fix("Carry on", action="/status/pause"))
    runs = [r for r in scheduler.next_runs() if r["id"] == "find.discover"]
    if not runs:
        return ok("No job searches are planned.")
    tail = " Missed runs catch up." if settings.get("schedule.catch_up") else ""
    return ok(f"Next job search at {_clock_words(runs[0]['at'])}.{tail}")


def disk_check() -> CheckResult:
    """Free space where Hal keeps its records."""
    folder = paths.data_dir()
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    free = shutil.disk_usage(folder).free
    words = f"{free / GB:.1f} GB free where Hal keeps your records."
    if free < BAD_DISK_BYTES:
        return bad(words + " Hal cannot make backups. Free some space.")
    if free < LOW_DISK_BYTES:
        return warn(words + " Space is getting low.")
    return ok(words)


def data_folder_check() -> CheckResult:
    """Where the records live; the old layout inside the code folder is a warning."""
    if paths.home_override() is None and paths.is_legacy_layout():
        return warn("Hal still reads the data folder inside its code. Run: python -m jobhunt import --dry-run",
                    Fix("Move your data", route="settings-advanced"))
    return ok(f"Your records are in {paths.data_dir()}.")


def port_check() -> CheckResult:
    """Hal answers on its own address."""
    from ..web import app, auth

    port = auth.server_port()
    if app.answering(port):
        return ok(f"Hal answers on this computer, port {port}.")
    return bad(f"Nothing answers on port {port}. Another program may be using it.",
               Fix("Restart Hal", action="/system/restart"))


def in_own_home() -> bool:
    """True for a test or worktree home, which never asks the computer about its login items."""
    return paths.home_override() is not None


def login_check() -> CheckResult:
    """Whether Hal starts when the computer starts, as `schedule.start_at_login` asks."""
    from . import platform, settings

    if in_own_home():
        return ok("This Hal runs in its own home folder, so it never starts by itself.")
    plat = platform.current()
    if plat.service() == "hal-unit":
        return ok("Managed by your Hal setup.")
    state = plat.start_at_login_state()
    if state is None:
        return ok("This computer cannot start Hal by itself. Open it from its shortcut.")
    if bool(settings.get("schedule.start_at_login")) and not state:
        return warn("Hal is set to start when the computer starts, but it is not set up to.",
                    Fix("Switch it on again", route="settings-schedule"))
    return ok("Hal starts when the computer starts." if state else "Hal starts only when you open it.")


CHECKS = [
    Check("schedule", "Schedule", schedule_check),
    Check("login", "Start at login", login_check),
    Check("disk", "Disk space", disk_check),
    Check("data", "Your data folder", data_folder_check),
    Check("port", "Hal's address", port_check),
]


# ---------------------------------------------------------------------------------------------- running them

def run_all(con: sqlite3.Connection | None = None) -> list[dict]:
    """Every area's checks as rows: {id, name, ok, level, text, fix}."""
    rows = []
    for module in plugins.modules("health"):
        for check in getattr(module, "CHECKS", ()):
            try:
                result = check.run()
            except Exception as e:  # a broken check is itself a finding, never a blank page
                log.exception("health check %s failed", check.id)
                result = bad(f"This check could not run: {e}")
            fix = None
            if result.fix is not None:
                fix = {"label": result.fix.label, "action": result.fix.action, "route": result.fix.route}
            rows.append({"id": check.id, "name": check.name, "ok": result.ok, "level": result.level,
                         "text": result.text, "fix": fix})
    for name, error in sorted(plugins.FAILED.items()):
        rows.append({"id": f"import:{name}", "name": "A part of Hal", "ok": False, "level": "bad",
                     "text": f"{name} could not load: {error}", "fix": None})
    return rows


_cache: dict = {"at": 0.0, "count": 0}
_cache_lock = threading.Lock()


def problem_count(con: sqlite3.Connection | None = None) -> int:
    """How many checks are not ok, for the status line; worked out at most once a minute."""
    with _cache_lock:
        if time.monotonic() - _cache["at"] < CACHE_S and _cache["at"]:
            return _cache["count"]
    count = sum(1 for row in run_all(con) if not row["ok"])
    with _cache_lock:
        _cache.update(at=time.monotonic(), count=count)
    return count

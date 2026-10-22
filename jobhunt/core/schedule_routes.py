"""Routes for the schedule, pause, restart and Home, and the start of the schedule with the server.

`POST /status/pause` without `paused` turns pause over, so a "Carry on" button can post `{}`. Restart
answers first and restarts a moment later, so the window gets its reply. `GET /home` lives here until
a Home route of its own is needed; the reply itself is `core.home.compose`. A change to
`schedule.start_at_login` is applied at once through the platform layer, except in a home override (tests
and worktrees), which must never write the person's real login items.
"""
from __future__ import annotations

import logging
import threading

from ..web.router import BadRequest, Request, route
from . import activity, health, home, plugins, runner, scheduler

log = logging.getLogger(__name__)

RESTART_AFTER_S = 0.5     # long enough for the reply to leave before the server goes
LOGIN_KEY = "schedule.start_at_login"
ADDON_PREFIX = "addon."


@plugins.on_start
def start_schedule() -> None:
    """Starts the in-app schedule with the server; applies start at login and the /jobs command as they change."""
    from . import settings

    scheduler.start()
    settings.on_change(LOGIN_KEY, apply_start_at_login)
    settings.on_change(ADDON_PREFIX, install_jobs_command)
    install_jobs_command()


def install_jobs_command(*_changed) -> None:
    """Renders the owner's `/jobs` command again whenever an add-on setting changes; never for a test home."""
    from ..addons import hal

    if health.in_own_home():
        return
    try:
        written = hal.install_jobs_command()
    except OSError as e:
        log.warning("could not write the /jobs command: %s", e)
        return
    if written is not None:
        log.info("wrote the /jobs command to %s", written)


def apply_start_at_login(key: str, old, new) -> str | None:
    """Switches start at login through the platform layer; never for a test or worktree home."""
    from . import paths, platform

    if paths.home_override() is not None:
        log.info("start at login left alone: this Hal runs in its own home folder")
        return None
    words = platform.current().start_at_login(bool(new))
    log.info("start at login: %s", words)
    return words


@route("POST", "/api/v2/status/pause")
def pause(req: Request) -> dict:
    """Pause or carry on: searches and the daily summary stop; email checks carry on."""
    body = req.body if isinstance(req.body, dict) else {}
    wanted = body.get("paused")
    if wanted is not None and not isinstance(wanted, bool):
        raise BadRequest("paused must be true or false.", field="paused")
    on = (not scheduler.paused(req.con)) if wanted is None else wanted
    return {"paused": scheduler.set_paused(on)}


@route("POST", "/api/v2/system/restart", auth="session_or_local")
def restart(req: Request) -> dict:
    """Restarts Hal after replying."""
    from . import platform

    activity.record("system", "system.restart", "Restarted Hal", "You pressed Restart")
    timer = threading.Timer(RESTART_AFTER_S, platform.current().restart_server)
    timer.daemon = True
    timer.start()
    return {"ok": True}


@route("GET", "/api/v2/schedule")
def schedule(req: Request) -> dict:
    """Every scheduled job with its last and next run, and the old timers that run jobs instead."""
    return {"jobs": scheduler.overview(), "paused": scheduler.paused(req.con),
            "timers": scheduler.timers_in_charge()}


@route("POST", "/api/v2/schedule/{job_id}/run")
def run_now(req: Request) -> dict:
    """Runs one scheduled job now; the action id comes back at once."""
    runner.flush()
    return {"action_id": scheduler.run_now(req.params["job_id"])}


@route("POST", "/api/v2/schedule/retire-timers", local_only=True)
def retire_timers(req: Request) -> dict:
    """"Let Hal run them instead": retires the old timers so Hal's own schedule runs their jobs."""
    from . import platform

    retired = platform.retire_legacy_timers()
    scheduler.check_timers()
    if retired:
        activity.record("system", "schedule.timers", f"Retired your old timers ({len(retired)})",
                        "You pressed Let Hal run them instead")
    return {"retired": retired}


@route("GET", "/api/v2/home")
def get_home(req: Request) -> dict:
    """Home: what needs you, new matches and the rest."""
    runner.flush()
    return home.compose(req.con)

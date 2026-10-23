"""The daily update check: `updates.check` at 09:00 while `updates.auto_check` is on.

The check only fetches and compares (steps 1 to 3 of an update) and never prompts for a sign-in; installing
waits for the person to press Update Hal. The action kind is `core/update_actions.py`'s, so the schedule
runs it with its steps and Activity row.
"""
from __future__ import annotations

from datetime import datetime

from .builtin_schedule import daily
from .scheduler import Job

CHECK_AT = "09:00"


def check_after(after: datetime) -> datetime | None:
    """09:00 local time after `after`, or None while daily checks are off."""
    from . import settings

    if not settings.get("updates.auto_check"):
        return None
    return daily([CHECK_AT], after)


def check_in_background() -> None:
    """Never used while `updates.check` is registered; the schedule runs that kind instead."""
    from . import runner

    runner.run("updates.check", why="Daily update check")


JOBS = [
    Job("updates.check", "settings", "Check for updates", check_after, check_in_background, catch_up=False,
        while_paused=True, why="Daily update check at {time}"),
]

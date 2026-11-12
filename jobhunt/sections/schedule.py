"""The projects refresh on Hal's schedule: `record.projects_refresh` every `schedule.projects_every` hours.

The job's id is the action kind (`sections.actions.REFRESH`), so the schedule runs that action with its steps
and Activity row, as `learn/schedule.py` does. A missed run is not caught up: the next one checks the same
projects, and a refresh that ran late would only repeat it.
"""
from __future__ import annotations

from datetime import datetime

from ..core.builtin_schedule import every
from ..core.scheduler import Job
from .actions import REFRESH

MINUTES_AN_HOUR = 60
WHY = "Hal checks your projects regularly for changes"


def run_after(after: datetime) -> datetime | None:
    """The next refresh, `schedule.projects_every` hours after `after`; None when it is off (0)."""
    from ..core import settings

    hours = int(settings.get("schedule.projects_every") or 0)
    return every(hours * MINUTES_AN_HOUR, after) if hours > 0 else None


def run_in_background() -> None:
    """Never used while `record.projects_refresh` is registered; the schedule runs that kind instead."""
    from ..core import runner

    runner.run(REFRESH, why=WHY)


JOBS = [
    Job(REFRESH, "documents", "Check your projects for changes", run_after, run_in_background, catch_up=False,
        while_paused=True, why=WHY),
]

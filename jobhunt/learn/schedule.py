"""The nightly look at your choices: `learn.patterns` at 03:30.

Between nights, a run also starts 20 s after a job event (`learn/actions.py`'s listener). The night run
catches what the listener cannot see, such as a suggestion whose week has passed and should be withdrawn.
A missed night is not caught up: the next event or night does the same work. The action kind is
`learn/actions.py`'s, so the schedule runs it with its steps and Activity row.
"""
from __future__ import annotations

from datetime import datetime

from ..core.builtin_schedule import daily
from ..core.scheduler import Job

RUN_AT = "03:30"   # after the evening search, before the morning summary
WHY = "Nightly look at your choices"


def run_after(after: datetime) -> datetime | None:
    """03:30 local time after `after`, or None while learning is off for every area."""
    from . import patterns

    if not any(patterns.enabled(area) for area in set(patterns.SOURCES.values())):
        return None
    return daily([RUN_AT], after)


def run_in_background() -> None:
    """Never used while `learn.patterns` is registered; the schedule runs that kind instead."""
    from ..core import runner

    runner.run("learn.patterns", subject="learn", why=WHY)


JOBS = [
    Job("learn.patterns", "learning", "Look for patterns in your choices", run_after, run_in_background,
        catch_up=False, why=WHY),
]

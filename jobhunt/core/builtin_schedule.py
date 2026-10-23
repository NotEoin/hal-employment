"""The jobs Hal runs on its own schedule: searches, email, the summary, the record, backups, tidying.

Times are the person's local clock times; each `next_after` reads live settings, so a change applies at the
next tick. `goal.mode` shapes searching: "Getting ready" keeps only the first search time and "Just
watching" searches on Mondays only. The update check is in `core/update_schedule.py`.

The three command-line jobs name the owner's old systemd timers, so they never run twice while those timers
are still enabled.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from .. import db
from . import clock
from .scheduler import Job

log = logging.getLogger(__name__)

DAYS_AHEAD = 8               # a weekly slot is always found within this many days
MONDAY = 0
BACKUP_AT = "03:00"
TIDY_AT = "03:10"


def _get(key: str):
    from . import settings

    return settings.get(key)


def _hm(text: str) -> tuple[int, int]:
    hour, minute = str(text).strip().split(":")
    return int(hour), int(minute)


def daily(times: Iterable[str], after: datetime, *, days: Callable[[datetime], bool] = lambda d: True) -> datetime:
    """The first of `times` (local "HH:MM") strictly after `after`, on a day `days` allows; in UTC."""
    # each slot is a naive local time made aware on its own day, so a clock change is honoured
    local = clock.local(after).replace(tzinfo=None)
    slots = sorted(_hm(t) for t in times)
    for offset in range(DAYS_AHEAD):
        day = local + timedelta(days=offset)
        if not days(day):
            continue
        for hour, minute in slots:
            at = clock.from_local(day.replace(hour=hour, minute=minute, second=0, microsecond=0))
            if at > after:
                return at.astimezone(UTC)
    raise ValueError(f"no slot in {DAYS_AHEAD} days for {list(times)}")


def _inside(hm: tuple[int, int], start: tuple[int, int], end: tuple[int, int]) -> bool:
    if start <= end:
        return start <= hm < end
    return hm >= start or hm < end      # hours that run past midnight


def every(minutes: int, after: datetime, hours: Iterable[str] | None = None) -> datetime:
    """`after` plus `minutes`, moved to the next opening of `hours` (local "HH:MM" pair) when outside them."""
    nxt = after + timedelta(minutes=int(minutes))
    if not hours:
        return nxt
    start, end = (_hm(h) for h in hours)
    local = clock.local(nxt)
    if _inside((local.hour, local.minute), start, end):
        return nxt
    return daily([f"{start[0]:02d}:{start[1]:02d}"], after)


def search_after(after: datetime) -> datetime | None:
    """The next search, shaped by how hard the person is looking."""
    times = list(_get("schedule.search_times") or [])
    if not times:
        return None
    mode = _get("goal.mode")
    if mode == "getting_ready":
        times = sorted(times)[:1]
    if mode == "watching":
        return daily(times, after, days=lambda d: d.weekday() == MONDAY)
    return daily(times, after)


def mail_after(after: datetime) -> datetime:
    """The next email check: every `schedule.mail_every` minutes within `schedule.mail_hours`."""
    return every(int(_get("schedule.mail_every")), after, _get("schedule.mail_hours"))


def summary_after(after: datetime) -> datetime | None:
    """The daily summary at `notify.daily_summary`, or never when it is off."""
    at = _get("notify.daily_summary")
    return None if not at or at == "off" else daily([at], after)


def record_after(after: datetime) -> datetime:
    """The next read of the record folder."""
    return every(int(_get("schedule.record_every")), after)


def sync_record() -> dict:
    """Reads new or changed files in the record folder."""
    from .worker import sync_record as sync

    return sync()


def backup() -> str:
    """Today's checked copy of Hal's records."""
    from . import backup as backups

    con = db.connect()
    try:
        return str(backups.snapshot(con, "daily"))
    finally:
        con.close()


def tidy() -> dict:
    """Old backups past their keep rule, and sign-in links and windows that have expired."""
    from . import backup as backups
    from . import paths

    con = db.connect()
    try:
        deleted = backups.prune(paths.backups_dir())
        now = clock.iso()
        tokens = con.execute("DELETE FROM open_tokens WHERE expires < ?", (now,)).rowcount
        sessions = con.execute("DELETE FROM sessions WHERE expires < ?", (now,)).rowcount
        con.commit()
    finally:
        con.close()
    return {"backups": len(deleted), "links": tokens, "windows": sessions}


JOBS = [
    Job("find.discover", "find", "Search for jobs", search_after, ("discover", "--quiet"),
        legacy_timer="jobhunt-discover.timer", why="Scheduled search at {time}"),
    Job("mail.check", "email", "Check email", mail_after, ("mail", "-q"), while_paused=True,
        legacy_timer="jobhunt-mail.timer", why="Email check, every {n} minutes"),
    Job("notify.digest", "email", "Send the daily summary", summary_after, ("digest",),
        legacy_timer="jobhunt-digest.timer", why="Daily summary at {time}"),
    Job("record.sync", "documents", "Read your record folder", record_after, sync_record, catch_up=False,
        while_paused=True, why="Hal reads your record folder regularly for new or changed files"),
    Job("system.backup", "system", "Back up your records", lambda after: daily([BACKUP_AT], after), backup,
        while_paused=True, why="Daily backup at {time}"),
    Job("system.tidy", "system", "Tidy old copies and sign-ins", lambda after: daily([TIDY_AT], after), tidy,
        while_paused=True, why="Daily tidy at {time}"),
]

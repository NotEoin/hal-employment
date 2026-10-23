"""The one clock: UTC now, its ISO form with a `Z`, and the local day.

Tests fix the time with `HAL_EMPLOYMENT_FAKE_NOW=2026-10-22T09:00:00Z`, read on every call so a
test can change it between steps. New rows store `iso()`; old rows with `+00:00` compare the same on their
first 19 characters.

Local time is this computer's time zone, or the IANA zone in `HAL_EMPLOYMENT_TZ` when that is set. Tests set
it instead of `TZ`, because `time.tzset()` exists only on Unix and Windows reads `TZ` once at start-up.

`uk_zone()` is UK time whatever the computer's zone. Windows has no zone database unless the `tzdata`
package is installed, which it is not today, so there it follows the UK's own rule instead (summer time from
01:00 UTC on the last Sunday of March to 01:00 UTC on the last Sunday of October, the rule since 1996).
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_FAKE_NOW = "HAL_EMPLOYMENT_FAKE_NOW"
ENV_TZ = "HAL_EMPLOYMENT_TZ"
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
UK_ZONE = "Europe/London"
SUMMER = timedelta(hours=1)          # British Summer Time is UTC+1
CHANGE_HOUR_UTC = 1                  # the clocks change at 01:00 UTC, in March and in October
MARCH, OCTOBER, SUNDAY = 3, 10, 6


def now_utc() -> datetime:
    """Now in UTC, to the second, or the fixed test time when one is set."""
    fake = os.environ.get(ENV_FAKE_NOW, "").strip()
    if fake:
        try:
            at = datetime.fromisoformat(fake)
        except ValueError as e:
            raise ValueError(f"{ENV_FAKE_NOW}={fake!r} is not an ISO 8601 time") from e
        return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).replace(microsecond=0)
    return datetime.now(UTC).replace(microsecond=0)


def iso(at: datetime | None = None) -> str:
    """`at` (default now) as `2026-10-22T09:00:00Z`; a naive time is taken as UTC."""
    at = now_utc() if at is None else at
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).strftime(ISO_FORMAT)


def parse(text: str) -> datetime:
    """A stored time, `Z` or `+00:00` form, as an aware UTC datetime."""
    at = datetime.fromisoformat(text.strip())
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC)


def local_zone() -> tzinfo | None:
    """The zone named by `HAL_EMPLOYMENT_TZ`, or None for this computer's own; read on every call.

    `UTC` needs no time zone database; any other name does (Windows has none unless `tzdata` is installed).
    """
    name = os.environ.get(ENV_TZ, "").strip()
    if not name:
        return None
    return UTC if name.upper() == "UTC" else ZoneInfo(name)


def local(at: datetime | None = None) -> datetime:
    """`at` (default now) in the person's local time; a naive time is taken as UTC."""
    at = now_utc() if at is None else at
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(local_zone())


def from_local(naive: datetime) -> datetime:
    """A naive local wall-clock time as an aware time, with that day's own offset (so clock changes count)."""
    zone = local_zone()
    return naive.replace(tzinfo=zone) if zone is not None else naive.astimezone()


def local_day(at: datetime | None = None) -> date:
    """The person's calendar day for `at` (default now), in local time."""
    return local(at).date()


def _last_sunday_utc(year: int, month: int) -> datetime:
    """01:00 on the last Sunday of `month`, as a naive UTC time."""
    last = datetime(year, month, 31 if month in (MARCH, OCTOBER) else 30, CHANGE_HOUR_UTC)
    return last - timedelta(days=(last.weekday() - SUNDAY) % 7)


class _UkRule(tzinfo):
    """UK time from the UK's rule, for a computer with no zone database (Windows without tzdata)."""

    def _summer_utc(self, naive_utc: datetime) -> bool:
        y = naive_utc.year
        return _last_sunday_utc(y, MARCH) <= naive_utc < _last_sunday_utc(y, OCTOBER)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        wall = dt.replace(tzinfo=None)
        start = _last_sunday_utc(wall.year, MARCH)                 # 01:00 GMT jumps to 02:00 BST
        end = _last_sunday_utc(wall.year, OCTOBER) + SUMMER        # 02:00 BST falls back to 01:00 GMT
        if start <= wall < end - SUMMER or (end - SUMMER <= wall < end and not dt.fold):
            return SUMMER
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return self.utcoffset(dt)

    def tzname(self, dt: datetime | None) -> str:
        return "BST" if self.utcoffset(dt) else "GMT"

    def fromutc(self, dt: datetime) -> datetime:
        naive = dt.replace(tzinfo=None)
        if self._summer_utc(naive):
            return (naive + SUMMER).replace(tzinfo=self)
        # the hour after the change back happens twice on the wall clock; this is its second time
        fold = int(_last_sunday_utc(naive.year, OCTOBER) <= naive < _last_sunday_utc(naive.year, OCTOBER) + SUMMER)
        return naive.replace(tzinfo=self, fold=fold)

    def __repr__(self) -> str:
        return "UkRule()"


_UK_RULE = _UkRule()


def uk_zone() -> tzinfo:
    """UK time: the zone database's Europe/London, else the UK's own rule (Windows without tzdata)."""
    try:
        return ZoneInfo(UK_ZONE)
    except ZoneInfoNotFoundError:
        return _UK_RULE

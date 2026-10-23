"""The one clock: UTC now, its ISO form with a `Z`, and the local day.

Tests fix the time with `HAL_EMPLOYMENT_FAKE_NOW=2026-10-22T09:00:00Z`, read on every call so a
test can change it between steps. New rows store `iso()`; old rows with `+00:00` compare the same on their
first 19 characters.

Local time is this computer's time zone, or the IANA zone in `HAL_EMPLOYMENT_TZ` when that is set. Tests set
it instead of `TZ`, because `time.tzset()` exists only on Unix and Windows reads `TZ` once at start-up.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo

ENV_FAKE_NOW = "HAL_EMPLOYMENT_FAKE_NOW"
ENV_TZ = "HAL_EMPLOYMENT_TZ"
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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

"""The one clock: UTC now, its ISO form with a `Z`, and the local day.

Tests fix the time with `HAL_EMPLOYMENT_FAKE_NOW=2026-10-22T09:00:00Z`, read on every call so a
test can change it between steps. New rows store `iso()`; old rows with `+00:00` compare the same on their
first 19 characters.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime

ENV_FAKE_NOW = "HAL_EMPLOYMENT_FAKE_NOW"
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


def local_day(at: datetime | None = None) -> date:
    """The person's calendar day for `at` (default now), in this computer's time zone."""
    return (now_utc() if at is None else at).astimezone().date()

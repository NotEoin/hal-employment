"""The one clock (core/clock.py): local time follows `HAL_EMPLOYMENT_TZ` when set, else this computer's zone,
and UK time is right with or without a zone database.
"""
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import clock

NOON = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
try:
    ZoneInfo("Europe/London")
    HAS_DATABASE = True
except ZoneInfoNotFoundError:
    HAS_DATABASE = False


class LocalTime(unittest.TestCase):
    def test_utc_needs_no_time_zone_database(self):
        # Windows has no zone database unless tzdata is installed, and the tests run there in UTC
        with mock.patch.dict(os.environ, {clock.ENV_TZ: "UTC"}), \
                mock.patch.object(clock, "ZoneInfo", side_effect=AssertionError("looked up a zone")):
            self.assertIs(clock.local_zone(), UTC)
            self.assertEqual(clock.local(NOON), NOON)
            self.assertEqual(clock.from_local(datetime(2026, 10, 22, 7, 30)), datetime(2026, 10, 22, 7, 30, tzinfo=UTC))

    def test_without_the_setting_local_time_is_this_computers(self):
        with mock.patch.dict(os.environ, {clock.ENV_TZ: ""}):
            self.assertIsNone(clock.local_zone())
            self.assertEqual(clock.local(NOON), NOON.astimezone())
            self.assertEqual(clock.local_day(NOON), NOON.astimezone().date())

    def test_a_naive_time_is_taken_as_utc(self):
        with mock.patch.dict(os.environ, {clock.ENV_TZ: "UTC"}):
            self.assertEqual(clock.local(datetime(2026, 10, 22, 12, 0)), NOON)

    def test_the_fixed_test_time_is_the_local_default(self):
        with mock.patch.dict(os.environ, {clock.ENV_TZ: "UTC", clock.ENV_FAKE_NOW: "2026-10-22T23:30:00Z"}):
            self.assertEqual(clock.local().hour, 23)
            self.assertEqual(str(clock.local_day()), "2026-10-22")


class UkTime(unittest.TestCase):
    """`uk_zone()` is UK time on any computer, also one with no zone database (Windows without tzdata)."""

    def no_database(self):
        return mock.patch.object(clock, "ZoneInfo", side_effect=clock.ZoneInfoNotFoundError(clock.UK_ZONE))

    def test_without_a_zone_database_summer_and_winter_times_are_right(self):
        # regression: on a Windows computer set to UTC, a limit that resets "3pm (Europe/London)" was read as 15:00 UTC
        with self.no_database():
            uk = clock.uk_zone()
        self.assertEqual(datetime(2026, 10, 22, 15, 0, tzinfo=uk).astimezone(UTC),
                         datetime(2026, 10, 22, 14, 0, tzinfo=UTC))
        self.assertEqual(datetime(2026, 12, 1, 15, 0, tzinfo=uk).astimezone(UTC),
                         datetime(2026, 12, 1, 15, 0, tzinfo=UTC))
        self.assertEqual(NOON.astimezone(uk).hour, 13)

    @unittest.skipUnless(HAS_DATABASE, "needs the zone database to compare against (tzdata on Windows)")
    def test_the_built_in_rule_matches_the_zone_database_every_half_hour_for_two_years(self):
        with self.no_database():
            rule = clock.uk_zone()
        real = ZoneInfo(clock.UK_ZONE)
        at = datetime(2026, 1, 1, tzinfo=UTC)
        while at.year < 2028:
            self.assertEqual(at.astimezone(rule).replace(tzinfo=None), at.astimezone(real).replace(tzinfo=None), at)
            wall = at.astimezone(real)
            for fold in (0, 1):
                naive = wall.replace(tzinfo=None, fold=fold)
                self.assertEqual(naive.replace(tzinfo=rule).utcoffset(), naive.replace(tzinfo=real).utcoffset(),
                                 (naive, fold))
            at += timedelta(minutes=30)


if __name__ == "__main__":
    unittest.main()

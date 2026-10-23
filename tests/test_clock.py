"""The one clock (core/clock.py): local time follows `HAL_EMPLOYMENT_TZ` when set, else this computer's zone."""
import os
import unittest
from datetime import UTC, datetime
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import clock

NOON = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


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


if __name__ == "__main__":
    unittest.main()

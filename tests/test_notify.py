"""Alerts (notify.py): pop-ups and phone alerts follow their settings and quiet hours, the phone topic is read
without ever prompting, and only the Hal add-on speaks urgent alerts.
"""
import os
import time
import unittest
from datetime import UTC, datetime
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_runner import fresh_home

from jobhunt import notify
from jobhunt.addons import hal
from jobhunt.core import settings
from jobhunt.core.settings import secrets

NOON = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
LATE = datetime(2026, 10, 22, 23, 30, tzinfo=UTC)


def setUpModule():
    os.environ["TZ"] = "UTC"
    time.tzset()


def tearDownModule():
    os.environ.pop("TZ", None)
    time.tzset()


class Alerts(unittest.TestCase):
    def setUp(self):
        self.enterContext(fresh_home())
        self.popups = mock.Mock()
        self.enterContext(mock.patch("jobhunt.core.platform.current", return_value=self.popups))
        self.sent = self.enterContext(mock.patch.object(notify.urllib.request, "urlopen"))
        self.spoken = self.enterContext(mock.patch.object(hal.subprocess, "Popen"))
        self.enterContext(mock.patch("jobhunt.core.clock.now_utc", return_value=NOON))

    def test_a_popup_shows_when_the_setting_is_on(self):
        notify.notify("2 strong new job matches", "Museum Assistant at Riverside Trust")
        self.popups.notify.assert_called_once_with("2 strong new job matches", "Museum Assistant at Riverside Trust",
                                                   urgent=False)
        settings.set("notify.desktop", False)
        notify.notify("Another match", "Archive Assistant")
        self.assertEqual(self.popups.notify.call_count, 1)

    def test_quiet_hours_hold_back_everything_but_urgent_alerts(self):
        with mock.patch("jobhunt.core.clock.now_utc", return_value=LATE):
            notify.notify("A new match", "Library Assistant")
            self.popups.notify.assert_not_called()
            notify.notify("Interview: Northgate Institute", "Invitation to interview", urgent=True)
            self.popups.notify.assert_called_once()

    def test_quiet_hours_run_past_midnight(self):
        self.assertTrue(notify.quiet_now(LATE))
        self.assertFalse(notify.quiet_now(NOON))
        settings.set("notify.quiet_hours", "Off")
        self.assertFalse(notify.quiet_now(LATE))

    def test_phone_alerts_need_the_setting_and_a_saved_topic(self):
        notify.notify("A new match", "Library Assistant")
        self.sent.assert_not_called()
        settings.set("notify.phone", True)
        notify.notify("A new match", "Library Assistant")
        self.sent.assert_not_called()
        secrets.put(notify.TOPIC_SECRET, "made-up-topic-1234")
        notify.notify("A new match", "Library Assistant", url="http://127.0.0.1:24321/")
        request = self.sent.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.sh/made-up-topic-1234")
        self.assertEqual(self.sent.call_args.kwargs["timeout"], notify.NTFY_TIMEOUT_S)

    def test_a_locked_keyring_never_prompts_for_the_phone_topic(self):
        settings.set("notify.phone", True)
        calls = []

        def locked(name, *, interactive=False):
            calls.append(interactive)
            raise secrets.SecretsLocked("locked")
        with mock.patch.object(secrets, "get", locked), self.assertLogs("jobhunt.notify", "WARNING"):
            notify.notify("A new match", "Library Assistant")
        self.assertEqual(calls, [False])
        self.sent.assert_not_called()

    def test_urgent_alerts_are_spoken_only_with_the_hal_addon(self):
        notify.notify("Interview: Northgate Institute", "Invitation to interview", urgent=True)
        self.spoken.assert_not_called()
        settings.set("addon.enabled", True)
        settings.set("addon.speak_command", "/opt/made-up/speak")
        notify.notify("Interview: Northgate Institute", "Invitation to interview", urgent=True)
        self.assertEqual(self.spoken.call_args.args[0],
                         ["/opt/made-up/speak", "Interview: Northgate Institute. Invitation to interview"])
        notify.notify("A new match", "Library Assistant")
        self.assertEqual(self.spoken.call_count, 1)


if __name__ == "__main__":
    unittest.main()

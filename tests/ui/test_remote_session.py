"""A window opened from another computer, in a real browser against a real server.

The window is signed in with an `open-url --remote` link, as the laptop launcher does. Needs
`HAL_TEST_BROWSER=1` and Playwright. The SSH server's state is made up, so no system tool is asked.
"""
from __future__ import annotations

import contextlib
import unittest
from pathlib import Path
from unittest import mock

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import record
from jobhunt.core import paths
from jobhunt.evidence import routes as record_routes
from jobhunt.remote import instructions

DESK = "deskbox"
# The shell asks for Home's summary, which the scheduler serves and this test does not start.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"


@skip_unless_browser
class RemoteWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(instructions, "ssh_server",
                                            return_value={"installed": True, "running": False}))
        self.enterContext(mock.patch.object(record, "VAULT", Path(paths.record_default()), create=True))
        self.addCleanup(record._CACHE.clear)
        record_routes.make_default()
        record.sync()

    def page(self, route: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url(remote=True, remote_host=DESK, next="/#" + route))
        p.settle()
        return p

    def errors(self, p: AppPage) -> list[str]:
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        return [e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours

    def test_the_rail_says_where_hal_runs_and_settings_shows_the_steps(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("settings-remote", size, scheme)
                p.page.wait_for_selector("[data-panel=remote] [data-key='remote.sessions'] .sc-note")
                self.assertIn("(this window)", p.page.inner_text("[data-panel=remote] [data-key='remote.sessions']"))
                body = p.page.inner_text("[data-panel=remote]")
                for words in ("For people comfortable with a terminal", "Installed, not running: see step 2",
                              "sudo apt install openssh-server", "Linux and Mac", "Windows",
                              "Alerts in this browser"):
                    self.assertIn(words, body)
                if size == "wide":
                    self.assertIn(f"On {DESK}, from this computer", p.page.inner_text("body"))
                # the registry's rows used to draw above the panel too, so each label showed twice
                for key in ("remote.how", "remote.launcher", "remote.sessions"):
                    self.assertEqual(p.page.locator(f"[data-group=remote] [data-key='{key}']").count(), 1, key)
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_signing_this_window_out_shows_the_launcher_words(self) -> None:
        p = self.page("settings-remote")
        p.page.click("[data-key='remote.sessions'] button:has-text('Sign out')")
        p.open("home")
        p.page.wait_for_selector("text=For your safety, Hal opens from its shortcut.")
        self.assertIn("Run the launcher on this computer again.", p.page.inner_text("body"))

    def test_the_record_offers_no_folder_to_open_from_another_computer(self) -> None:
        p = self.page("record")
        p.page.wait_for_selector("text=What is in it")
        self.assertEqual(p.page.locator("button:has-text('Open the folder')").count(), 0)

"""The setup guide's "Browser helper" step in a real browser against a real server.

Needs `HAL_TEST_BROWSER=1` and Playwright. The browsers Hal finds come from a stand-in for the platform
layer, so no real browser on the test computer is looked for or opened.
"""
from __future__ import annotations

import contextlib
import unittest
from pathlib import Path
from unittest import mock

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt.core import platform

PICTURES = "/app/setup/img/helper/"
WAIT_TRIES = 50                 # 50 x 100 ms: the press reaches the server well within 5 s
WAIT_STEP_MS = 100


@skip_unless_browser
class HelperStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def test_the_step_loads_only_the_pictures_of_the_browser_found(self) -> None:
        # the step drew Opera's pictures first and swapped to the browser found, so
        # Opera's three picture requests were aborted on every visit
        vivaldi = platform.found("vivaldi", Path("/opt/vivaldi/vivaldi"))
        with HalServer() as hal, mock.patch("jobhunt.apply.helper_routes._found",
                                            return_value={"vivaldi": vivaldi}):
            p = AppPage(self.browser, f"http://127.0.0.1:{hal.port}")
            self.addCleanup(p.close)
            asked, failed = [], []
            p.page.on("request", lambda r: PICTURES in r.url and asked.append(r.url.rsplit("/", 1)[-1]))
            p.page.on("requestfailed", lambda r: failed.append(f"{r.url} {r.failure}"))
            p.page.goto(hal.open_url())
            p.open("setup-helper")
            p.page.wait_for_selector("img.helper-pic")
            p.page.wait_for_load_state("networkidle")
            self.assertEqual(failed, [])
            self.assertEqual(sorted(asked), ["vivaldi-1.svg", "vivaldi-2.svg", "vivaldi-3.svg"])
            self.assertEqual(p.page.get_attribute(".pick.on", "aria-pressed"), "true")
            self.assertIn("Vivaldi", p.page.inner_text(".pick.on"))


    def test_the_first_button_copies_the_address_and_opens_a_window_to_paste_it_in(self) -> None:
        # "Open Edge's extensions page" opened Edge on a new tab, because
        # Chromium browsers drop their own addresses given on the command line
        edge = platform.found("edge", Path("/opt/edge/msedge"))
        cls = type(platform.current())
        with HalServer() as hal, mock.patch("jobhunt.apply.helper_routes._found", return_value={"edge": edge}), \
                mock.patch.object(cls, "open_browser_window") as window:
            p = AppPage(self.browser, f"http://127.0.0.1:{hal.port}")
            self.addCleanup(p.close)
            p.ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{hal.port}")
            p.page.goto(hal.open_url())
            p.open("setup-helper")
            p.page.click("text=Copy the address and open Microsoft Edge")
            for _ in range(WAIT_TRIES):
                if window.called:
                    break
                p.page.wait_for_timeout(WAIT_STEP_MS)
            self.assertIn("press Ctrl and V together", p.page.inner_text(".helper-paste"))
            self.assertEqual(p.page.evaluate("() => navigator.clipboard.readText()"), "edge://extensions")
            self.assertEqual([c.args[0].id for c in window.call_args_list], ["edge"])
            self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

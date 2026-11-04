"""The Applications screens in a real browser: the board, approving with the first-application guide, the
Form step's marks, and "I sent it myself".

They run the real files of `jobhunt/web/static/app/` against the fake server of `tests/support/fake_api.py`,
which answers the Applications GET routes with a made-up person and made-up applications. They need
`HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import json
import unittest

from support.fake_api import MADE_UP_APP, FakeServer, FakeState
from support.ui import AppPage, chromium, skip_unless_browser

APP = "app-demo%3A1"
APP_PATH = "/applications/demo:1"   # what the page asks for at APP



def apps_state() -> FakeState:
    """The fake server's state with one application of its own, which a test may change."""
    state = FakeState()
    state.pages[APP_PATH] = json.loads(json.dumps(MADE_UP_APP))
    return state


@skip_unless_browser
class ApplicationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def serve(self, state: FakeState | None = None) -> FakeServer:
        srv = FakeServer(state or apps_state())
        self.addCleanup(srv.__exit__, None, None, None)
        return srv.__enter__()

    def tab(self, srv: FakeServer, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, srv.url, size, scheme)
        self.addCleanup(p.close)
        return p

    def test_the_board_shows_each_column_and_the_closed_list(self) -> None:
        p = self.tab(self.serve()).open("apps")
        p.page.wait_for_selector(".board .colm")
        heads = p.page.locator(".board .colm h3").all_inner_texts()
        self.assertEqual(heads, ["Getting ready", "Form filled", "Sent", "Interview", "Offer"])
        self.assertIn("1 answer needs you", p.page.locator(".colm", has_text="Form filled").inner_text())
        self.assertIn("too much effort", p.page.locator("section.card", has_text="Closed").inner_text())
        p.page.click("text=Museum Assistant")
        p.page.wait_for_selector("h1:text('Museum Assistant')")
        self.assertEqual(p.errors, [])

    def test_approving_shows_the_first_application_guide_then_the_form_step(self) -> None:
        state = apps_state()
        state.pages[APP_PATH].update(step=1, column="ready", form=None)
        state.pages[APP_PATH]["docs"]["state"] = "ready"
        state.pages[APP_PATH]["steps"][0].update(state="now", note="Waiting for you")
        srv = self.serve(state)
        p = self.tab(srv).open(APP)
        p.page.wait_for_selector(".approve")
        p.page.click("text=Approve and fill in the form")
        dialog = p.page.locator("[role=dialog]")
        self.assertIn("Your first application with Hal", dialog.inner_text())
        self.assertIn("press Submit on the site yourself", dialog.inner_text())
        self.assertEqual([path for path, _ in state.posts], [])
        p.page.click("text=Got it, open the form")
        # The page stays on the application and shows its Form step; it went back to the board (testers, 2026-11-04).
        p.page.wait_for_selector("[role=tab][aria-selected=true]:has-text('Form')")
        self.assertEqual(p.page.locator("h1").inner_text(), "Museum Assistant")
        self.assertEqual([path for path, _ in state.posts], ["/api/v2/applications/demo%3A1/documents/approve"])
        self.assertEqual(p.errors, [])

    def test_the_form_step_marks_each_answer_and_i_sent_it_myself_moves_it_on(self) -> None:
        state = apps_state()
        srv = self.serve(state)
        p = self.tab(srv, "narrow", "dark").open(APP)
        p.page.wait_for_selector(".field-row")
        marks = p.page.locator(".field-row .flag").all_inner_texts()
        # The answer to check comes first.
        self.assertEqual(marks, ["Check this", "Fine"])
        self.assertIn("Hal filled 1 of 2 answers. 1 needs you.", p.page.locator(".approve").inner_text())
        self.assertIn("Written by Hal", p.page.locator(".field-row").first.inner_text())
        p.page.click("text=I sent it myself")
        p.page.wait_for_selector("text=Moved to Sent")
        self.assertIn(("/api/v2/applications/demo%3A1/sent", {}), state.posts)
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

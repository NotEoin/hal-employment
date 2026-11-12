"""Critique as a recruiter in a real browser: the button on the Documents step, the dialog while Hal reads,
then the top fixes, the requirements, each document's grades and the line comments.

They run the real files of `jobhunt/web/static/app/` against the fake server of `tests/support/fake_api.py`,
whose made-up action answers at once with a made-up read. They need `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from unittest import mock

from support import fake_api
from support.fake_api import MADE_UP_APP, FakeServer, FakeState
from support.ui import AppPage, chromium, skip_unless_browser

APP = "app-demo%3A1"
APP_PATH = "/applications/demo:1"
CRITIQUE = "/api/v2/applications/demo%3A1/documents/critique"
ACTION = "made-up-critique"
READ = {"field": "Humanities, heritage and the written word",
        "screen": {"who": "A history graduate", "wants": "collections work", "read_on": True,
                   "why": "The dissertation sits near the top."},
        "requirements": [{"requirement": "Writing reports", "evidence": "Dissertation on river trade", "strength": 4},
                         {"requirement": "Answering enquiries", "evidence": "", "strength": 1}],
        "documents": [{"document": "cv", "title": "Your CV", "summary": "The dissertation is the strongest thing.",
                       "grades": [{"criterion": "Impact", "grade": 2, "why": "Duties, not results.",
                                   "fix": "Say how many visitors you guided.", "needs_fact": True}],
                       "lines": [{"quote": "Volunteer guide", "comment": "Say what came of it.", "by": "ai"}]},
                      {"document": "letter", "title": "Your cover letter", "summary": "",
                       "grades": [{"criterion": "Why them", "grade": 4, "why": "Names the Trust's archive.",
                                   "fix": "", "needs_fact": False}],
                       "lines": [{"quote": "passionate", "comment": "Say what was done in plain words.",
                                  "by": "rules"}]}],
        "top_fixes": ["Say how many visitors you guided.", "Name the enquiries you answered.",
                      "Put the dissertation first."],
        "ai": True, "note": ""}


def docs_state() -> FakeState:
    """One made-up application waiting on its Documents step, and the made-up critique it gets."""
    state = FakeState()
    app = json.loads(json.dumps(MADE_UP_APP))
    app.update(step=1, column="ready", form=None, first_time=False)
    app["docs"]["state"] = "ready"
    app["steps"][0].update(state="now", note="Waiting for you")
    state.pages[APP_PATH] = app
    state.pages[f"/actions/{ACTION}"] = {"id": ACTION, "state": "done", "result": READ}
    return state


@skip_unless_browser
class CritiqueDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(fake_api.MADE_UP_POSTS, {CRITIQUE: {"action_id": ACTION}}))

    def serve(self, state: FakeState) -> FakeServer:
        srv = FakeServer(state)
        self.addCleanup(srv.__exit__, None, None, None)
        return srv.__enter__()

    def tab(self, srv: FakeServer, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, srv.url, size, scheme)
        self.addCleanup(p.close)
        return p

    def test_the_documents_step_asks_for_a_recruiters_read_and_shows_it(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                state = docs_state()
                p = self.tab(self.serve(state), size, scheme).open(APP)
                p.page.wait_for_selector(".approve")
                p.page.click("button:has-text('Critique as a recruiter')")
                dialog = p.page.locator("[role=dialog]")
                dialog.locator("[data-top-fixes]").wait_for()
                self.assertIn("Critique as a recruiter", dialog.inner_text())
                self.assertEqual(dialog.locator("[data-top-fixes] li").all_inner_texts(), READ["top_fixes"])
                self.assertIn("a recruiter reads on", dialog.inner_text())
                self.assertIn("Answering enquiries: not shown yet", dialog.inner_text())
                self.assertIn("2 of 5", dialog.locator("[data-doc=cv] [data-grade=Impact]").inner_text())
                self.assertIn("You know this one: Say how many visitors you guided.",
                              dialog.locator("[data-doc=cv]").inner_text())
                self.assertIn("Say what was done in plain words.", dialog.locator("[data-doc=letter]").inner_text())
                self.assertEqual([path for path, _ in state.posts], [CRITIQUE])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                p.page.click("[role=dialog] footer button:has-text('Close')")
                p.page.wait_for_selector("[role=dialog]", state="detached")
                self.assertEqual(p.errors, [])

    def test_a_read_that_could_not_finish_says_why(self) -> None:
        state = docs_state()
        state.pages[f"/actions/{ACTION}"] = {"id": ACTION, "state": "error", "error": "The made-up AI is busy."}
        p = self.tab(self.serve(state)).open(APP)
        p.page.wait_for_selector(".approve")
        p.page.click("button:has-text('Critique as a recruiter')")
        alert = p.page.wait_for_selector("[role=dialog] [role=alert]")
        self.assertEqual(alert.inner_text(), "The made-up AI is busy.")


if __name__ == "__main__":
    unittest.main()

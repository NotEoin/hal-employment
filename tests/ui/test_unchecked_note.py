"""The notice of facts not checked yet, on the pages where Hal writes: My CV and an application's documents.

My CV runs against a real server in a temporary home, with facts added through the store as an ingest would;
the Documents step runs against the fake server of `tests/support/fake_api.py`. Every fact and file is made
up. Needs `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from contextlib import closing

from support.api import HalServer
from support.fake_api import MADE_UP_APP, FakeServer, FakeState
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.profile import store

CV = "Sources/CVs and letters/Alex Doe CV.md"
NOTICE = "that you have not checked yet. Hal only uses facts you have checked."
APP = "app-demo%3A1"
APP_PATH = "/applications/demo:1"


@skip_unless_browser
class UncheckedNote(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def tab(self, url: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, url, size, scheme)
        self.addCleanup(p.close)
        return p

    def test_my_cv_says_how_many_facts_wait_and_goes_to_check_them(self) -> None:
        hal = self.enterContext(HalServer())
        with closing(db.connect()) as con:
            ids = [store.add_found(con, "skill", {"name": name}, {"source_rel": CV, "quote": name, "doc_type": "cv"})
                   for name in ("Cataloguing", "Oral history interviewing")]
        p = self.tab(f"http://127.0.0.1:{hal.port}", "narrow", "dark")
        p.page.goto(hal.open_url())
        p.open("cv")
        note = p.page.locator(".unchecked-note")
        p.shows(note, "the notice")
        self.assertIn("Hal found 2 facts in your files " + NOTICE, note.inner_text())
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        with closing(db.connect()) as con:
            for item_id in ids:
                store.confirm(con, item_id)
        p.open("cv")
        p.page.wait_for_selector("text=Your CV")
        self.assertEqual(p.page.locator(".unchecked-note").count(), 0, "nothing to check, nothing said")

    def test_the_documents_step_shows_the_same_notice(self) -> None:
        state = FakeState()
        state.pages[APP_PATH] = json.loads(json.dumps(MADE_UP_APP))
        state.pages[APP_PATH].update(step=1, column="ready", form=None)
        state.pages[APP_PATH]["docs"]["state"] = "ready"
        srv = FakeServer(state)
        self.addCleanup(srv.__exit__, None, None, None)
        srv.__enter__()
        p = self.tab(srv.url).open(APP)
        p.page.wait_for_selector(".approve")
        unchecked = state.pages["/profile"]["to_check"]
        note = p.page.locator(".unchecked-note")
        p.shows(note, "the notice")
        self.assertIn(f"Hal found {unchecked} facts in your files " + NOTICE, note.inner_text())
        note.get_by_role("button", name="Check them").click()
        p.page.wait_for_selector("#main h1:text('Check your record')")
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

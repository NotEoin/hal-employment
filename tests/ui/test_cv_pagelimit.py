"""A CV's page limit in a real browser, against a real server with a made-up imported CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. The page count comes from a stand-in printer that says every CV
prints on three pages (`PRINTED`), so no browser prints here; the real printer is tested in test_cv_printer.
This checks the Length card on Type and look at both sizes and the warning after a save in Edit your CV.
"""
from __future__ import annotations

import contextlib
import unittest
from unittest import mock

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_import import lay_out

from jobhunt.cvstudio import import_v1, pagelimit, store

PRINTED = 3                 # pages the stand-in printer gives every CV
JOB = "Lab assistant"       # an entry of the imported made-up CV (`test_cv_import.SITE`)


def three_pages(html, out, expect_name, expect_text) -> int:
    """The stand-in printer."""
    return PRINTED


@skip_unless_browser
class PageLimit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(pagelimit, "PRINT", three_pages))
        lay_out(import_v1.folder())
        store.current()
        self.doc_id = store.lead_id()

    def page(self, route: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def test_keep_this_cv_to_is_chosen_on_type_and_look_and_warns_with_the_real_count(self) -> None:
        for size, scheme, pages, words in (("wide", "light", 1, "One page"), ("narrow", "dark", 2, "Two pages")):
            with self.subTest(size=size):
                p = self.page("cv-style", size, scheme)
                card = p.page.locator("section.card", has_text="Keep this CV to")
                p.shows(card.get_by_role("radiogroup", name="Keep this CV to"), "the three choices")
                card.get_by_role("radio", name=words).click()
                p.page.wait_for_selector(f"text=This CV is {PRINTED} pages. You asked for {pages}.")
                self.assertEqual(pagelimit.limit_of(self.doc_id), (pages, True))
                self.assertEqual(card.get_by_role("radio", name=words).get_attribute("aria-checked"), "true")
                card.get_by_role("radio", name="No limit").click()
                p.page.wait_for_selector(f"text=It prints on {PRINTED} pages now.")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_a_save_that_runs_over_the_limit_is_kept_and_warned_about(self) -> None:
        store.set_page_limit(2, doc_id=self.doc_id)
        for size in ("wide", "narrow"):
            with self.subTest(size=size):
                p = self.page("cv-edit", size)
                line = p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']")
                p.shows(line, "the first line of the job")
                line.fill(f"Helped first-years debug their labs ({size})")
                n = len(store.versions())
                p.page.locator("button:has-text('Save changes')").click()
                p.page.wait_for_selector(f"text=This CV is now {PRINTED} pages. You asked for 2.")
                self.assertEqual(len(store.versions()), n + 1, "the save is never refused")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])


if __name__ == "__main__":
    unittest.main()

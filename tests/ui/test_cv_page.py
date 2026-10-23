"""My CV in a real browser, against a real server in a temporary home with a made-up imported CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. This checks the header, Your CV, the Sections card, Versions and
the "How your CV is made" dialog.
"""
from __future__ import annotations

import contextlib
import unittest

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_import import SITE, lay_out

from jobhunt.cvstudio import import_v1, store

# Home's summary, which this test's bare server does not draw.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"


@skip_unless_browser
class CvPage(unittest.TestCase):
    def errors(self, p: AppPage) -> list[str]:
        """Console errors, leaving out failed loads that are all of addresses still to come."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        return [e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours

    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        lay_out(import_v1.folder())
        store.current()

    def page(self, route: str = "cv", size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def test_the_page_says_what_employers_get_and_draws_cleanly_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("cv", size, scheme)
                p.page.wait_for_selector("text=At a glance")
                self.assertEqual(p.page.inner_text("#main h1"), "My CV")
                self.assertIn("Employers get: Hal's copy of your CV, version 4.", p.page.inner_text("#main"))
                paper = p.page.frame_locator("iframe[title='Your CV']").locator("main").inner_text()
                self.assertIn(SITE["education"][0]["qualification"], paper)
                self.assertIn("Tree index in C", paper)
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_with_no_cv_file_your_file_offers_the_upload(self) -> None:
        # My CV said "You have not added a CV file" with no way to add one there
        p = self.page()
        p.page.wait_for_selector("text=At a glance")
        card = p.page.locator("section.card", has_text="Your file")
        self.assertIn("Drop your CV here", card.inner_text())
        self.assertEqual(self.errors(p), [])

    def test_an_old_version_can_be_used_again_and_compared(self) -> None:
        p = self.page("cv-versions")
        p.page.wait_for_selector("text=Version 4")
        p.page.click("input[aria-label='Pick version 1']")
        p.page.click("input[aria-label='Pick version 4']")
        p.page.click("button:has-text('Compare 1 and 4')")
        p.page.wait_for_selector("text=From version 1 to version 4")
        self.assertIn("Tree index in C", p.page.inner_text(".cv-changes"))
        p.page.locator(".li", has_text="Version 3").locator("button:has-text('Use this version')").click()
        p.page.wait_for_selector("text=Version 3 is now in use. Version 4 is kept.")
        self.assertEqual(store.version(store.current()[0])["n"], 3)
        p.page.wait_for_selector(".cv-employers:has-text('version 3.')")
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.serious_axe(), [])

    def test_renaming_a_section_saves_a_new_version(self) -> None:
        p = self.page("cv-style")
        box = p.page.locator(".secrow input[aria-label='Section name']").nth(3)
        box.fill("Work experience")
        box.press("Tab")
        p.page.locator(".tab", has_text="Versions").click()
        p.page.wait_for_selector("text=You changed your CV's sections")
        self.assertEqual(store.current()[1].sections[3].title, "Work experience")
        self.assertEqual(self.errors(p), [])

    def test_the_route_dialog_says_why_your_file_cannot_be_sent(self) -> None:
        p = self.page("cv")
        p.page.click("button:has-text('Change how your CV is made')")
        p.page.click("button[role=radio]:has-text('Use my CV as it is')")
        p.page.click("button:has-text('Use this')")
        p.page.wait_for_selector("text=Hal has no file of yours to send as it is. Add your CV file first.")
        self.assertEqual(store.doc()["send"], "hal_copy")


if __name__ == "__main__":
    unittest.main()

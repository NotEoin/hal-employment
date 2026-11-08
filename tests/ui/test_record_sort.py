""""Sort a big folder for me" in a real browser: start a sort, check its proposals a section at a time, and add only
what is ticked. Against a real server in a temporary home; needs `HAL_TEST_BROWSER=1` and Playwright.

The folder is the made-up history graduate's (Marnie Quillfeather); every name and file is invented. The AI is
the fake one, which places nothing.
"""
from __future__ import annotations

import contextlib
import json
import re
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from support.api import HalServer
from support.fake_ai import fake_ai
from support.ui import AppPage, chromium, skip_unless_browser
from test_sections_sorter import history_dump

from jobhunt import db, record
from jobhunt.core import paths
from jobhunt.evidence import routes
from jobhunt.sections import store as sources

NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"


@skip_unless_browser
class SortAFolder(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(record, "VAULT", Path(paths.record_default()), create=True))
        self.addCleanup(record._CACHE.clear)
        self.enterContext(fake_ai({"sort_pick": json.dumps({"choice": "none"})}))
        routes.make_default()

    def page(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open("record")
        return p

    def clean(self, p: AppPage) -> None:
        """No console errors, no sideways scroll and no serious accessibility findings."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        self.assertEqual([e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours, [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def listed(self, section: str) -> list[dict]:
        with closing(db.connect()) as con:
            return sources.listing(con, section)

    def test_a_folder_is_sorted_checked_by_section_and_only_the_ticked_files_are_added(self) -> None:
        dump = history_dump(Path(self.hal.home) / "Files")
        p = self.page()
        card = p.page.locator("section.card", has=p.page.get_by_role("heading", name="Sort a big folder for me"))
        p.shows(card, "the sort card")
        card.get_by_label("A folder on this computer").fill(str(dump))
        card.get_by_role("button", name="Sort this folder").click()
        review = p.page.locator("section.rec-sort")
        p.shows(review.get_by_text("Ready for you to check"), "the finished sort")
        review.get_by_role("button", name="CVs: 1").click()
        p.shows(review.get_by_text("Marnie Quillfeather CV.md", exact=True), "the CV's row")
        self.assertEqual(self.listed("cv"), [], "nothing is added before the person says so")
        review.get_by_role("button", name="Add the ticked CVs").click()
        p.shows(review.get_by_role("button", name="Added: 1"), "the added count")
        self.assertEqual([s["section"] for s in self.listed("cv")], ["cv"])
        self.assertEqual(self.listed("lecture"), [])
        review.get_by_role("button", name=re.compile(r"^Left out: \d+$")).click()
        p.shows(review.get_by_text("Fit note.md", exact=True), "a private file left out")
        self.clean(p)
        for size, scheme in (("narrow", "dark"),):
            with self.subTest(size=size):
                self.clean(self.page(size, scheme))


if __name__ == "__main__":
    unittest.main()

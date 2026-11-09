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
from test_ingest import put
from test_sections_sorter import REFERENCE, history_dump

from jobhunt import db, record
from jobhunt.core import paths, runner
from jobhunt.evidence import routes
from jobhunt.profile import store as profile_store
from jobhunt.sections import sorts
from jobhunt.sections import store as sources

NOT_YET = ("/api/v2/home",)
WAIT_S = 30
FAILED_LOAD = "Failed to load resource"
# A reference kept deep in its folders, so its path is longer than the review shows whole.
LONG_REL = ("Volunteering/2023 to 2024 placements at the Fenwick Lantern Museum Trust/Letters and references kept "
            "for applications/Reference from the museum archivist.md")
WIDE_MIN = 1000      # the review's width in the 1400-pixel window, less the menu (252) and the page's margins
RESIZED = 1000       # the window's width after it is made narrower
RAIL = 252           # the menu's width beside the page (app.css `.app`)


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

    def sorted_dump(self) -> int:
        """The made-up history graduate's folder, with a deeply kept reference, sorted in this thread; its id."""
        dump = history_dump(Path(self.hal.home) / "Files")
        put(dump, LONG_REL, REFERENCE)
        with closing(db.connect()) as con:
            sort_id = sorts.create(con, "folder", [str(dump)], dump.name)
            sorts.run(con, sort_id)
        return sort_id

    def width(self, p: AppPage, selector: str) -> float:
        """The drawn width of the first element `selector` finds, in pixels."""
        return p.page.locator(selector).first.bounding_box()["width"]

    def listed(self, section: str) -> list[dict]:
        with closing(db.connect()) as con:
            return sources.listing(con, section)

    def step(self, review, n: int) -> None:
        """Opens step `n` of the review."""
        review.get_by_role("button", name=re.compile(rf"^Step {n}: ")).click()
        self.assertEqual(review.locator("[aria-current=step]").get_attribute("aria-label").split(":")[0], f"Step {n}")

    def test_a_folder_is_sorted_checked_step_by_step_and_only_the_ticked_files_are_added(self) -> None:
        dump = history_dump(Path(self.hal.home) / "Files")
        p = self.page()
        card = p.page.locator("section.card", has=p.page.get_by_role("heading", name="Sort a big folder for me"))
        p.shows(card, "the sort card")
        card.get_by_label("A folder on this computer").fill(str(dump))
        card.get_by_role("button", name="Sort this folder").click()
        review = p.page.locator("section.rec-sort")
        p.shows(review.get_by_text("Ready for you to check"), "the list, opened by itself once sorted")
        self.assertRegex(p.page.url, r"#sort-\d+$")
        self.assertEqual(review.locator("[aria-current=step]").get_attribute("aria-label"), "Step 1: Your courses",
                         "the review starts with the courses the folders name")
        p.shows(review.get_by_text("Add your CVs and papers first, so your courses exist."), "the order to add in")
        p.shows(review.get_by_role("heading", name="Courses Hal found"), "the courses the folders name")
        review.get_by_role("button", name="Add the course BA History").click()
        p.shows(p.page.get_by_text(re.compile(r"^Added BA History\.")), "the course added")
        with closing(db.connect()) as con:
            self.assertEqual([c["title"] for c in profile_store.items(con, "education")], ["BA History"])
        self.assertEqual(review.get_by_role("button", name="Add everything ticked").count(), 0,
                         "adding everything is only on the last step")
        review.get_by_role("button", name="Next", exact=True).click()
        self.assertEqual(review.locator("[aria-current=step]").get_attribute("aria-label"),
                         "Step 2: CVs and official papers")
        p.shows(review.get_by_text("Marnie Quillfeather CV.md", exact=True), "the CV's row")
        self.assertEqual(review.get_by_text("Seminar notes week 2.md", exact=True).count(), 0, "only this step's rows")
        self.assertEqual(self.listed("cv"), [], "nothing is added before the person says so")
        review.get_by_role("button", name="Add the ticked CVs and papers").click()
        p.shows(p.page.get_by_text(re.compile(r"^Adding \d+ files? to your record")), "the papers added")
        self.assertTrue(runner.idle(WAIT_S))
        self.assertEqual([s["section"] for s in self.listed("cv")], ["cv"])
        self.assertEqual(self.listed("lecture"), [])
        self.step(review, 4)
        review.get_by_role("button", name="Show the files of HIS2040 Archival Methods").click()
        p.shows(review.get_by_text("Seminar notes week 2.md", exact=True), "a module's own files")
        review.get_by_role("button", name="Back to the list").click()
        p.shows(review.get_by_text("(3 files)"), "the module as one row")
        self.step(review, 5)
        p.shows(review.get_by_text("Hal could not see who wrote these (no history). Mark your best work as Showcase."),
                "said once: a project folder with no history got no showcase guess")
        self.step(review, 6)
        p.shows(review.get_by_role("button", name="Add everything ticked"), "adding everything on the last step")
        review.get_by_role("button", name=re.compile(r"^Left out: \d+$")).click()
        p.shows(review.get_by_text("Fit note.md", exact=True), "a private file left out")
        self.clean(p)
        for size, scheme in (("narrow", "dark"),):
            with self.subTest(size=size):
                self.clean(self.page(size, scheme))

    def test_overview_leads_to_a_sort_waiting_to_be_checked_and_the_menu_shows_a_badge(self) -> None:
        sort_id = self.sorted_dump()
        for size in ("wide", "narrow"):
            with self.subTest(size=size):
                p = self.page(size)
                banner = p.page.locator(".rec-sort-banner")
                p.shows(banner.get_by_text(re.compile(r"^Hal sorted \d+ files from Marnie files\.")), "the banner")
                p.shows(banner.get_by_text("Nothing is added until you check the list."), "what happens next")
                badge = p.page.locator("nav.rail .nav", has_text="Your record").locator(".count")
                self.assertEqual(badge.text_content(), "1", "the menu counts the sort waiting")
                button = banner.get_by_role("button", name="Check the list")
                self.assertIn("primary", button.get_attribute("class"))
                self.clean(p)
                button.click()
                p.shows(p.page.get_by_role("heading", name="Check the sorted list", level=1), "the list's own page")
                self.assertTrue(p.page.url.endswith(f"#sort-{sort_id}"))

    def test_the_steps_show_one_part_at_a_time_and_next_moves_on(self) -> None:
        sort_id = self.sorted_dump()
        for size in ("narrow", "wide"):
            with self.subTest(size=size):
                p = self.page(size)
                p.go(f"sort-{sort_id}")
                review = p.page.locator("section.rec-sort")
                p.shows(review.get_by_text("Ready for you to check"), "the review")
                names = review.locator(".rec-steps button").evaluate_all(
                    "bs => bs.map(b => b.getAttribute('aria-label'))")
                self.assertEqual(names, ["Step 1: Your courses", "Step 2: CVs and official papers",
                                         "Step 3: Coursework", "Step 4: Lecture material", "Step 5: Projects",
                                         "Step 6: Done"])
                for n, words in ((2, "add these first"), (3, "handed-in work"), (4, "you studied"),
                                 (5, "Tick Showcase"), (6, "add everything still ticked")):
                    review.get_by_role("button", name="Next", exact=True).click()
                    current = review.locator("[aria-current=step]").get_attribute("aria-label")
                    self.assertTrue(current.startswith(f"Step {n}:"), current)
                    p.shows(review.locator(".rec-step-how", has_text=words), f"what to do in step {n}")
                    self.assertEqual(review.get_by_role("button", name="Add everything ticked").count(),
                                     1 if n == 6 else 0)
                self.assertEqual(review.get_by_role("button", name="Next", exact=True).count(), 0)
                self.assertEqual(p.sideways(), 0)
                self.clean(p)

    def test_the_list_fills_a_wide_window_and_follows_it_when_it_is_resized(self) -> None:
        sort_id = self.sorted_dump()
        p = self.page()
        p.go(f"sort-{sort_id}")
        review = p.page.locator("section.rec-sort")
        p.shows(review.get_by_text("Ready for you to check"), "the review on its own page")
        self.assertGreater(self.width(p, "section.rec-sort"), WIDE_MIN, "the review is not a narrow card")
        p.page.set_viewport_size({"width": RESIZED, "height": 900})
        self.assertLess(self.width(p, "section.rec-sort"), RESIZED - RAIL + 1, "the review follows the window")
        self.assertEqual(p.sideways(), 0)
        self.clean(p)

    def test_on_a_phone_the_table_scrolls_inside_itself_and_long_paths_are_shortened(self) -> None:
        sort_id = self.sorted_dump()
        p = self.page("narrow")
        p.go(f"sort-{sort_id}")
        review = p.page.locator("section.rec-sort")
        p.shows(review.get_by_text("Ready for you to check"), "the review on its own page")
        self.step(review, 2)
        long_path = review.locator(f'[title="Marnie files/{LONG_REL}"]')
        p.shows(long_path, "the long path, whole in its title")
        self.assertIn("...", long_path.inner_text(), "the path is shortened in the middle")
        self.assertTrue(long_path.inner_text().endswith("Reference from the museum archivist.md"))
        box = p.page.locator(".rec-sort-scroll").first
        self.assertGreater(box.evaluate("e => e.scrollWidth - e.clientWidth"), 0, "the table scrolls in its box")
        self.assertEqual(p.sideways(), 0, "the page itself never scrolls sideways")
        self.clean(p)

    def test_check_the_list_opens_the_review_on_its_own_page(self) -> None:
        sort_id = self.sorted_dump()
        p = self.page()
        p.page.get_by_role("button", name="Check the list").first.click()
        p.shows(p.page.get_by_role("heading", name="Check the sorted list", level=1), "the review's own page")
        self.assertTrue(p.page.url.endswith(f"#sort-{sort_id}"))
        p.page.get_by_role("button", name="Back to your record").click()
        p.shows(p.page.get_by_role("heading", name="Your record", level=1), "back on Your record")

    def test_an_older_record_is_moved_to_sections_and_one_click_undoes_it(self) -> None:
        from test_sections_move import old_record

        with closing(db.connect()) as con:
            old_record(con, Path(paths.record_default()), Path(self.hal.home) / "Files")
        p = self.page()
        p.page.get_by_role("button", name="Move my record to sections").click()
        review = p.page.locator("section.rec-sort")
        p.shows(review.get_by_text("Ready for you to check"), "the proposals for the older notes")
        self.step(review, 6)
        review.get_by_role("button", name="Add everything ticked").click()
        p.shows(review.get_by_role("button", name=re.compile(r"^Added: \d+$")), "the added count")
        self.assertTrue(runner.idle(WAIT_S), "the accepted notes are read before the move is finished")
        review.get_by_role("button", name="Finish the move").click()
        p.shows(p.page.get_by_text(re.compile("gave is set aside")), "the finished move")
        self.assertTrue(self.listed("cv"))
        review.get_by_role("button", name="Undo the move").click()
        p.shows(p.page.get_by_text(re.compile("your record is as it was")), "the undone move")
        self.assertEqual(self.listed("cv"), [])
        self.clean(p)


if __name__ == "__main__":
    unittest.main()

"""Activity in a real browser against the real server and the made-up world: lines by day, "Why and what",
the area filter and "Show earlier".

They need `HAL_TEST_BROWSER=1` and Playwright, as every browser test does (docs/dev/testing.md).
"""
from __future__ import annotations

import contextlib
import unittest

from support.ui import chromium, real_app, skip_unless_browser
from support.world import World

from jobhunt.core import activity

PAGE = activity.PAGE_LIMIT   # one more line than this makes "Show earlier" appear


@skip_unless_browser
class ActivityPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.enterContext(World())
        activity.record("email", "mail.check", "Sorted 3 new made-up emails", "Email check, every 15 minutes",
                        changed="Made 1 to-do")
        for n in range(PAGE + 2):
            activity.record("find", "find.discover", f"Searched made-up site {n}", "Scheduled search at 07:30")

    def test_why_and_what_opens_each_line_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                p.open("activity")
                pg = p.page
                self.assertEqual(pg.locator("h2.day").first.inner_text(), "Today")
                line = pg.locator(".act", has_text=f"Searched made-up site {PAGE + 1}")
                line.get_by_role("button", name="Why and what").click()
                self.assertIn("Scheduled search at 07:30", line.locator(".detail").inner_text())
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_the_area_filter_and_show_earlier(self) -> None:
        with real_app(self.browser) as p:
            p.open("activity")
            pg = p.page
            # the heading draws before the lines arrive, so the count below means something only after this
            p.shows(pg.locator(".act", has_text=f"Searched made-up site {PAGE + 1}"), "the newest line")
            self.assertEqual(pg.locator(".act", has_text="Sorted 3 new made-up emails").count(), 0)
            pg.get_by_role("button", name="Show earlier").click()
            p.shows(pg.locator(".act", has_text="Sorted 3 new made-up emails"), "the earlier email line")
            pg.get_by_role("button", name="Email", exact=True).click()
            pg.locator(".act", has_text="Searched made-up site").first.wait_for(state="detached")
            pg.locator(".act", has_text="Sorted 3 new made-up emails").wait_for()
            self.assertEqual(pg.locator(".act").count(), 1)
            self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

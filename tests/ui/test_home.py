"""Home in a real browser against the real server and the made-up world: what needs you, most urgent first,
this week, the goal card and what Hal did today with its Why? drawer.

They need `HAL_TEST_BROWSER=1` and Playwright, as every browser test does (docs/dev/testing.md).
"""
from __future__ import annotations

import contextlib
import threading
import unittest

from support.ui import AppPage, chromium, skip_unless_browser
from support.world import World, job_id

from jobhunt.core import activity
from jobhunt.core.settings import store
from jobhunt.web import app, auth


@contextlib.contextmanager
def real_app(browser, size: str = "wide", scheme: str = "light"):
    """The new server inside the made-up world, and a signed-in tab on it."""
    store.drop_cache()
    httpd = app.make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="ui-test-server", daemon=True).start()
    p = AppPage(browser, f"http://127.0.0.1:{port}", size, scheme)
    try:
        p.page.goto(auth.mint_open_url(port=port))
        yield p
    finally:
        p.close()
        httpd.stop()   # waits for its requests, which hold the world's database open until they end


@skip_unless_browser
class HomePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.w = self.enterContext(World())
        self.w.con.execute("UPDATE applications SET docs_need_redraft = 1 WHERE job_id = ?", (job_id("sorrel-ready"),))
        self.w.con.commit()
        activity.record("find", "find.discover", "Searched a made-up job site", "Scheduled search at 07:30",
                        changed="Kept 2 of 9 jobs")

    def test_home_shows_what_needs_you_most_urgent_first_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                p.open("home")
                pg = p.page
                self.assertTrue(pg.inner_text("#main h1").startswith("Good "))
                needs = pg.locator("[aria-labelledby=needs-h] .li b").all_inner_texts()
                self.assertEqual(needs[0], "Book the interview with Cobalt Ledger")
                self.assertIn("Documents for Software Engineer, Graduate need making again", needs)
                self.assertEqual(needs[-1], "Update the portfolio page")
                self.assertIn("Make documents again", pg.inner_text("[aria-labelledby=needs-h]"))
                week = pg.inner_text("[aria-labelledby=week-h]")
                self.assertIn("Where your 4 applications are", week)
                self.assertIn("Interview 2", week)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_why_opens_the_drawer_for_what_hal_did_today(self) -> None:
        with real_app(self.browser) as p:
            p.open("home")
            pg = p.page
            self.assertIn("Searched a made-up job site", pg.inner_text("[aria-labelledby=today-h]"))
            pg.locator("[aria-labelledby=today-h] button.why").first.click()
            drawer = pg.get_by_role("dialog", name="Why Hal did this")
            drawer.wait_for()
            words = drawer.inner_text()
            self.assertIn("Scheduled search at 07:30", words)
            self.assertIn("Kept 2 of 9 jobs", words)
            drawer.get_by_role("button", name="See all activity").click()
            pg.wait_for_url("**/#activity")
            self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

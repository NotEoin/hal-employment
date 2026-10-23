"""The Jobs pages in a real browser against the real server: sort, filters, the kind row, the J K A S N keys,
Hidden with its reasons and fixes, and one job's points.

Each test starts a server in a temporary home with made-up adverts scored by the real rules. They need
`HAL_TEST_BROWSER=1` and Playwright. Every employer, place and advert here is invented.
"""
from __future__ import annotations

import contextlib
import os
import unittest
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_find_routes import GRAD_TEXT, GRADUATE, INTERN_TEXT, NOW, STUDENTS_TEXT, add

from jobhunt import db
from jobhunt.core import clock, runner, settings
from jobhunt.core.settings import store

KINDS = ["graduate_job", "graduate_scheme"]
# Home's route is not built yet; the shell asks it for the menu counts and gets a 404.
NOT_YET_BUILT = ("/api/v2/home",)
ACTION_WAIT_S = 10   # a stand-in action ends at once; this only bounds a broken one
LOAD_FAILED = "Failed to load resource"


@skip_unless_browser
class JobsPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ, {clock.ENV_FAKE_NOW: NOW}))
        self.hal = self.enterContext(HalServer())
        settings.set_many({**GRADUATE, "goal.kinds": KINDS}, by="setup")
        store.drop_cache()
        self.con = db.connect()
        self.addCleanup(self.con.close)
        self.best = add(self.con, "best", "Graduate Research Assistant", GRAD_TEXT + " Research and policy work.")
        self.paid = add(self.con, "paid", "Graduate Policy Officer", GRAD_TEXT.replace("25,000", "29,000"))
        self.scheme = add(self.con, "scheme", "Policy Graduate Scheme", GRAD_TEXT.replace("25,000", "23,000"))
        self.intern = add(self.con, "intern", "Summer Intern, Research", INTERN_TEXT)
        self.students = add(self.con, "students", "Research Graduate Programme", STUDENTS_TEXT)

    def tab(self, route: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.failed = []
        p.page.on("response", lambda r: r.status >= 400 and not r.url.split("?")[0].endswith(NOT_YET_BUILT)
                  and p.failed.append(f"{r.status} {r.url}"))
        p.page.goto(self.hal.open_url())       # the one-time link gives this browser its cookie
        p.open(route)
        return p

    def assert_no_errors(self, p: AppPage) -> None:
        """No failed request to a built route, and no console error other than those failed loads."""
        self.assertEqual(p.failed, [])
        self.assertEqual([e for e in p.errors if not e.startswith(LOAD_FAILED)], [])

    def titles(self, p: AppPage) -> list[str]:
        return p.page.locator(".joblist .job .title").all_inner_texts()

    def wait_titles(self, p: AppPage, first: str) -> list[str]:
        p.page.wait_for_selector(f".joblist .job:first-child .title:text-is('{first}')")
        return self.titles(p)

    def test_matches_sort_and_filter_by_place_and_kind(self) -> None:
        p = self.tab("jobs")
        p.page.wait_for_selector(".joblist .job")
        scores = [int(s) for s in p.page.locator(".joblist .job .score b").all_inner_texts()]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(scores), 3)

        p.page.select_option("#jobs-sort", "pay")
        self.assertEqual(self.wait_titles(p, "Graduate Policy Officer"),
                         ["Graduate Policy Officer", "Graduate Research Assistant", "Policy Graduate Scheme"])

        kinds = p.page.locator(".toolbar.kinds .pick").all_inner_texts()
        self.assertEqual(kinds, ["All kinds", "Graduate and entry-level jobs (2)", "Graduate schemes (1)"])
        p.page.click(".toolbar.kinds .pick:has-text('Graduate schemes')")
        self.assertEqual(self.wait_titles(p, "Policy Graduate Scheme"), ["Policy Graduate Scheme"])
        p.page.click(".toolbar.kinds .pick:has-text('All kinds')")
        p.page.click(".picks .pick:text-is('Northby')")
        p.page.wait_for_selector(".joblist .job >> nth=2")
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assert_no_errors(p)

    def test_keys_j_k_a_s_n_act_on_the_chosen_job(self) -> None:
        started = []
        # The real kind writes documents; a stand-in replaces it for this test and the real one comes back.
        runner.load_kinds()
        self.enterContext(mock.patch.dict(runner.KINDS))
        self.enterContext(mock.patch.dict(runner._FNS))
        runner.forget("docs.prepare")
        runner.action("docs.prepare", area="documents", label="Making documents", why="You pressed Apply",
                      example=lambda w: {})(lambda ctx, job_id: (started.append(job_id), ctx.step("started")))
        p = self.tab("jobs")
        p.page.wait_for_selector(".joblist .job")
        for key, n in (("j", 0), ("j", 1), ("k", 0)):
            p.page.keyboard.press(key)
            self.assertEqual(p.page.inner_text(".job.sel .title"), self.titles(p)[n])
        first, second = self.titles(p)[:2]

        p.page.keyboard.press("s")
        p.page.wait_for_selector(".toast:has-text('Saved')")
        self.assertIsNotNone(self.con.execute("SELECT saved_at FROM jobs WHERE title = ?", (first,)).fetchone()[0])

        p.page.wait_for_selector(f".joblist .job:first-child .title:text-is('{second}')")
        p.page.keyboard.press("j")
        p.page.keyboard.press("n")
        p.page.wait_for_selector('[role="dialog"] h2:text-is("Not for you?")')
        p.page.keyboard.press("Escape")
        p.page.wait_for_selector('[role="dialog"]', state="detached")

        p.page.keyboard.press("a")
        p.page.wait_for_url("**#app-*")
        # the page moves on when the action is queued; the action itself runs on a pool thread
        self.assertTrue(runner.idle(ACTION_WAIT_S))
        self.assertEqual(len(started), 1)
        self.assert_no_errors(p)

    def test_hidden_shows_each_reason_and_a_way_to_change_it(self) -> None:
        p = self.tab("jobs-hidden")
        p.page.wait_for_selector(".hidden-group")
        text = p.page.inner_text(".card:has(.hidden-group)")
        self.assertIn("Paid internships: not a kind of role you chose", text)
        self.assertIn("For current students only. You finished your course in 2026.", text)
        self.assertIn("Check when your course ends", text)
        self.assertEqual(p.serious_axe(), [])

        group = p.page.locator(".hidden-group:has-text('Paid internships')")
        group.locator("button:text-is('Show them')").click()
        group.locator(".job .title:text-is('Summer Intern, Research')").wait_for()
        group.locator("button:text-is('Add paid internships to your goal')").click()
        p.page.click("button:text-is('Re-check jobs')")
        p.page.wait_for_selector(".toast:has-text('Jobs re-checked')")
        self.assertIn("internship", settings.get("goal.kinds"))
        p.go("jobs")
        p.page.wait_for_selector(".joblist .job .title:text-is('Summer Intern, Research')")
        self.assert_no_errors(p)

    def test_the_job_page_adds_up_and_works_at_phone_width(self) -> None:
        p = self.tab("job-" + quote(self.best, safe=""), size="narrow", scheme="dark")
        p.page.wait_for_selector(".pt.total")
        base = int(p.page.inner_text(".pts .pt:first-child .v"))
        rows = p.page.locator(".pts .pt:not(.total):not(:first-child) .v").all_inner_texts()
        points = [int(v.replace("+", "")) for v in rows]
        total = int(p.page.inner_text(".pt.total .v"))
        self.assertEqual(total, max(1, min(100, base + sum(points))))
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assert_no_errors(p)


if __name__ == "__main__":
    unittest.main()

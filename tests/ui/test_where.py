"""Where Hal looks and "Add a job site" in a real browser against the real server: the sites with their
health and what is missed without keys, the two-step key guide, switching a site off, adding a Workable
careers page with its jobs ticked, and the goal step's quick questions ending in "Here is what I understood".

They need `HAL_TEST_BROWSER=1` and Playwright. Every employer, place and person here is made up, and boards
are fakes, so no test touches the network.
"""
from __future__ import annotations

import contextlib
import os
import unittest
from unittest import mock

from support.api import HalServer
from support.jobs import advert
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.core import clock, settings
from jobhunt.core.settings import store
from jobhunt.sources import ats

NOW = "2026-10-22T09:00:00Z"
GOAL = {"goal.stage": "new_grad", "goal.kinds": ["graduate_job", "graduate_scheme"],
        "goal.roles": ["learning officer"], "goal.places": ["Hollybridge"], "scoring.preset": "humanities_grad",
        "scoring.min_show": 1, "goal.avoid_sectors": []}
# The step is drawn on its own over the app, with the app's own modules, so the test does not walk the
# guide's earlier steps.
MOUNT_STEP = """async () => {
  const [{ html, render }, { Step }] = await Promise.all([import("/app/core/html.js"),
                                                         import("/app/setup/steps/goal.js")]);
  const host = document.createElement("div");
  host.className = "card";
  host.id = "test-step";
  document.querySelector("#main").before(host);
  render(html`<${Step} />`, host);
}"""
LOAD_FAILED = "Failed to load resource"


def board(slug, name):
    return [advert("Graduate Learning Officer", "A graduate role in Hollybridge. Graduates are welcome.",
                   id=f"workable:{name}:1", source="workable", company=name, location="Hollybridge",
                   url="https://apply.workable.example/harbour/1"),
            advert("Head of Finance", "A senior role needing ten years of experience.", id=f"workable:{name}:2",
                   source="workable", company=name, location="Hollybridge",
                   url="https://apply.workable.example/harbour/2")], 2


@skip_unless_browser
class WhereHalLooks(unittest.TestCase):
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
        settings.set_many(GOAL, by="setup")
        store.drop_cache()

    def tab(self, route: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.failed = []
        p.page.on("response", lambda r: r.status >= 400 and p.failed.append(f"{r.status} {r.url}"))
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def assert_clean(self, p: AppPage) -> None:
        self.assertEqual(p.failed, [])
        self.assertEqual([e for e in p.errors if not e.startswith(LOAD_FAILED)], [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def test_the_sites_show_their_health_and_what_is_missed_without_keys(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.tab("jobs-where", size, scheme)
                p.page.wait_for_selector("text=Reed has no key, so Hal skips it.")
                body = p.page.inner_text("#main")
                self.assertIn("Hal works without these keys.", body)
                self.assertIn("Guardian Jobs", body)
                hn = p.page.get_by_role("switch", name="Search Hacker News: Who is hiring")
                self.assertEqual(hn.get_attribute("aria-checked"), "false")
                self.assert_clean(p)

        p = self.tab("jobs-where")
        row = p.page.locator(".li", has_text="Reed").first
        row.get_by_role("button", name="Get a free key").click()
        p.page.wait_for_selector("text=Get a free Reed key")
        self.assertEqual(row.locator("ol li").count(), 2)

        guardian = p.page.get_by_role("switch", name="Search Guardian Jobs")
        guardian.click()
        p.page.wait_for_selector("[role=switch][aria-label='Search Guardian Jobs'][aria-checked=false]")
        with contextlib.closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT enabled FROM sources WHERE id = 'guardian'").fetchone()[0], 0)

    def test_a_workable_careers_page_is_added_with_its_ticked_jobs(self) -> None:
        p = self.tab("jobs-where")
        p.page.get_by_role("button", name="Add a job site or careers page").click()
        box = p.page.get_by_label("Address")
        box.fill("https://apply.workable.com/harbour-arts-trust/")
        with mock.patch.dict(ats.FETCHERS, {"workable": board}):
            p.page.get_by_role("button", name="Look").click()
            p.page.wait_for_selector("text=This is a Workable careers page for Harbour Arts Trust.")
        boxes = p.page.locator("input[type=checkbox]")
        self.assertEqual(boxes.count(), 2)
        self.assertEqual([boxes.nth(i).is_checked() for i in range(2)], [True, False])
        self.assertEqual(p.serious_axe(), [])
        p.page.get_by_role("button", name="Save the site").click()
        p.page.wait_for_selector("text=Saved. Hal added 1 job and will check this site at every search.")
        p.page.wait_for_selector(".li:has-text('Harbour Arts Trust')")
        self.assert_clean(p)


    def test_the_goal_step_ends_with_what_hal_understood_and_saves_it(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.tab("home", size, scheme)
                p.page.evaluate(MOUNT_STEP)
                p.page.wait_for_selector("#test-step h2:text-is('Here is what I understood')")
                self.assertEqual(p.page.inner_text("#test-step h1"), "What are you looking for?")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
        step = p.page.locator("#test-step")
        step.get_by_role("button", name="Heritage and museums").click()
        step.get_by_role("button", name="Paid internships").click()
        step.get_by_label("Places").fill("Hollybridge, Fernvale")
        step.get_by_label("Lowest pay").fill("23k")
        p.page.wait_for_selector("#test-step dd:text-is('Hollybridge first, then Fernvale')")
        p.page.wait_for_selector("#test-step span:has-text('Hal reads this as')")
        self.assertIn("collections assistant", step.locator("dl").inner_text())
        step.get_by_role("button", name="Yes, save this").click()
        p.page.wait_for_selector("#test-step button:has-text('Saved')")
        store.drop_cache()
        self.assertEqual(settings.get("goal.places"), ["Hollybridge", "Fernvale"])
        self.assertEqual(settings.get("goal.min_pay"), 23000)
        self.assertIn("internship", settings.get("goal.kinds"))
        self.assertIn("collections assistant", settings.get("goal.roles"))
        self.assertEqual(settings.get("scoring.preset"), "humanities_grad")
        self.assertEqual([e for e in p.errors if not e.startswith(LOAD_FAILED)], [])


if __name__ == "__main__":
    unittest.main()

"""The Suggestions tab, the Adjust for each job card and a job's change list in a real browser, against a real
server in a temporary home holding a made-up CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. My CV itself may not be there yet, so these parts are drawn
into the app's main area on their own, from their modules, the way the page will draw them.
"""
from __future__ import annotations

import contextlib
import unittest

from support.stubs import cv_early_contracts

cv_early_contracts.install()

from support.api import HalServer  # noqa: E402 - the CV stand-ins go in before any jobhunt.cvstudio import
from support.ui import AppPage, chromium, skip_unless_browser  # noqa: E402
from test_cv_suggest import made_up_cv  # noqa: E402

from jobhunt import db  # noqa: E402
from jobhunt.cvstudio import store, suggest  # noqa: E402

FAILED_LOAD = "Failed to load resource"
# Draws a component from its module into a new box at the end of the main area.
MOUNT = """async ([file, name, props]) => {
  const h = await import('/app/core/html.js');
  const m = await import('/app/cv/' + file);
  const box = document.createElement('div');
  box.id = 'p34-' + name;
  document.querySelector('#main').appendChild(box);
  window.jobChangeToggles = [];
  const extra = name === 'JobChanges' ? { onToggle: (c, on) => window.jobChangeToggles.push([c.id, on]) } : {};
  h.render(h.html`<${m[name]} ...${{ ...props, ...extra }} />`, box);
}"""
CHANGES = [{"id": "c1", "kind": "Moved", "where": "Skills", "before": "Writing, Research, Tools",
            "after": "Tools, Writing, Research", "why": "The advert asks for Excel.", "ev": "", "on": True},
           {"id": "c2", "kind": "Reworded", "where": "Profile", "before": "History graduate.",
            "after": "History graduate who catalogued [how many?] letters.", "why": "", "ev": "", "on": False}]


@skip_unless_browser
class SuggestionsTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        store.new_version(made_up_cv(), origin="imported", what="Your made-up CV", by="baseline")
        con = db.connect()
        try:
            suggest.look_again(con)
        finally:
            con.close()

    def page(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open("cv")
        return p

    def errors(self, p: AppPage) -> list[str]:
        return [e for e in p.errors if not e.startswith(FAILED_LOAD)]

    def test_the_tab_shows_each_suggestion_with_its_reason_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                p.page.evaluate(MOUNT, ["sugg-tab.js", "SuggTab", {}])
                p.page.wait_for_selector("text=How suggestions work")
                body = p.page.inner_text("#p34-SuggTab")
                for words in ("0 of 5", "reviewed", "Accept all consistency fixes", "Look again",
                              "Write dates the same way", "Based on:", "Try another way",
                              "Hal never adds a number, date or qualification it cannot find in your CV or record."):
                    self.assertIn(words, body)
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_accept_all_and_leave_it_as_it_is_move_cards_to_reviewed(self) -> None:
        p = self.page()
        p.page.evaluate(MOUNT, ["sugg-tab.js", "SuggTab", {}])
        p.page.click("button:has-text('Accept all consistency fixes')")
        p.page.wait_for_selector("#p34-SuggTab h2:has-text('Reviewed')")
        self.assertIn("2 of ", p.page.inner_text("#p34-SuggTab"))   # the next review may have found more
        card = p.page.locator("article.sugg", has_text="Use characters job sites can read")
        card.locator("button:has-text('Leave it as it is')").click()
        card.locator("button:has-text('Not relevant')").click()
        card.locator(".row button:has-text('Leave it as it is')").click()
        p.page.wait_for_selector("text=Left as it was. Hal will remember: not relevant.")
        p.page.wait_for_selector("#p34-SuggTab .sugg.done:has-text('Use characters job sites can read')")
        self.assertEqual(self.errors(p), [])

    def test_a_gap_is_marked_and_accept_says_to_fill_it_first(self) -> None:
        con = db.connect()
        try:
            con.execute("UPDATE cv_suggestions SET afters = ? WHERE kind = 'Dates'", ('["Sep [when?]"]',))
            con.commit()
        finally:
            con.close()
        p = self.page()
        p.page.evaluate(MOUNT, ["sugg-tab.js", "SuggTab", {}])
        card = p.page.locator("article.sugg", has_text="Write dates the same way")
        self.assertEqual(card.locator(".ph").inner_text(), "[when?]")
        card.locator("button:has-text('Accept')").first.click()
        p.page.wait_for_selector("text=Fill in the gap first")

    def test_the_adjust_card_and_a_jobs_change_list_draw_and_answer(self) -> None:
        p = self.page("narrow", "dark")
        p.page.evaluate(MOUNT, ["tailor-setting.js", "TailorSetting", {}])
        p.page.wait_for_selector("#p34-TailorSetting >> text=A little: Hal reorders sections and skills")
        p.page.click("#p34-TailorSetting [role=radio]:has-text('More')")
        p.page.wait_for_selector("#p34-TailorSetting >> text=More: Hal may also rewrite bullets")
        p.page.evaluate(MOUNT, ["job-changes.js", "JobChanges", {"changes": CHANGES}])
        box = p.page.inner_text("#p34-JobChanges")
        self.assertIn("Hal changed 2 things from your CV for this job.", box)
        self.assertIn("Undone: your CV's version is used", box)
        p.page.click("#p34-JobChanges button:has-text('Redo')")
        self.assertEqual(p.page.evaluate("window.jobChangeToggles"), [["c2", True]])
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])


if __name__ == "__main__":
    unittest.main()

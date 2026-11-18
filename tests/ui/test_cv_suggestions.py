"""The Suggestions tab, the Adjust for each job card and a job's change list in a real browser, against a real
server in a temporary home holding a made-up CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. The parts are drawn into the app's main area on their own, from
their modules, the way My CV draws them (the page itself is drawn by `ui.test_cv_page` and the shell smoke).
"""
from __future__ import annotations

import contextlib
import unittest

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_suggest import made_up_cv

from jobhunt import db
from jobhunt.cvstudio import model, store, suggest

FAILED_LOAD = "Failed to load resource"
MARKED = "Organized **author events** for *the* [shop](https://example.org/shop)"
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
        store.set_type("humanities_grad")   # the store starts on the technical type; this CV is a history one
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

    def test_a_lines_bold_italic_and_links_are_drawn_never_shown_as_marks_at_both_sizes(self) -> None:
        events = "s:experience/e:ex-shop/b:b-events"
        doc = model.apply(store.current()[1], model.Change("replace", events, MARKED))
        store.new_version(doc, origin="manual", what="Bold", by="you")
        con = db.connect()
        try:
            suggest.look_again(con, use_ai=False)
        finally:
            con.close()
        changes = [{"id": "c9", "kind": "Reworded", "where": "Work experience, Bookseller", "before": MARKED,
                    "after": "Ran **author events** for *the* [shop](https://example.org/shop), [how many?] a year",
                    "why": "", "ev": "", "on": True}]
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                p.page.evaluate(MOUNT, ["sugg-tab.js", "SuggTab", {}])
                card = p.page.locator("article.sugg", has_text="author events").filter(has_text="Use one spelling")
                p.shows(card, "the spelling card")
                p.page.evaluate(MOUNT, ["job-changes.js", "JobChanges", {"changes": changes}])
                for box in (card, p.page.locator("#p34-JobChanges")):
                    self.assertNotIn("*", box.inner_text())
                    self.assertNotIn("](", box.inner_text())
                    self.assertGreaterEqual(box.locator("strong", has_text="author events").count(), 2)
                    self.assertGreaterEqual(box.locator("em", has_text="the").count(), 1)
                    self.assertEqual({box.locator("a", has_text="shop").nth(i).get_attribute("href")
                                      for i in range(box.locator("a", has_text="shop").count())},
                                     {"https://example.org/shop"})
                jobs = p.page.locator("#p34-JobChanges")
                self.assertEqual(jobs.locator(".ph").inner_text(), "[how many?]")
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_the_adjust_card_and_a_jobs_change_list_draw_and_answer(self) -> None:
        p = self.page("narrow", "dark")
        p.page.evaluate(MOUNT, ["tailor-setting.js", "TailorSetting", {}])
        p.page.wait_for_selector("#p34-TailorSetting >> text=A little: Hal puts the projects and skill groups")
        # regression: the card promised rewritten bullets and a "cares about first" step no application did
        self.assertNotIn("Ask me what the job cares about first", p.page.inner_text("#p34-TailorSetting"))
        p.page.click("#p34-TailorSetting [role=radio]:has-text('More')")
        p.page.wait_for_selector("#p34-TailorSetting >> text=More: as A little, and Hal may also reword")
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

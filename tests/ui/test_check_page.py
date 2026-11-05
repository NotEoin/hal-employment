"""The Check your record page in a real browser, against a real server in a temporary home.

The facts are added through the store as an ingest would add them; the person, her files and employers are
made up. Needs `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import unittest
from contextlib import closing

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.profile import store

CV = "Sources/CVs and letters/Alex Doe CV.md"
LETTER = "Sources/CVs and letters/Museum letter.md"
PROJECT = "Sources/Projects/Mill town letters.md"
MODULE = "Sources/Study notes/Archive methods week 3.md"
EMPLOYER = "Fenwick Lantern Museum Trust"
# The shell asks for Home's summary, which this test does not serve; its 404 is not this page's fault.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"


def found(kind: str, fields: dict, source: str = CV, doc_type: str = "cv", quote: str = "") -> int:
    """Adds one fact as the ingest would; returns its item's id."""
    with closing(db.connect()) as con:
        return store.add_found(con, kind, fields, {"source_rel": source, "quote": quote, "doc_type": doc_type,
                                                   "field": ""})


def state(item_id: int) -> str:
    """The item's state as the store has it now."""
    with closing(db.connect()) as con:
        return store.item(con, item_id)["state"]


@skip_unless_browser
class CheckPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())

    def page(self, route: str = "check", size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and not any(n in r.url for n in NOT_YET)
                  and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def clean(self, p: AppPage) -> None:
        """No console errors, no sideways scroll and no serious accessibility findings."""
        self.assertEqual(p.missing, [])
        self.assertEqual([e for e in p.errors if not e.startswith(FAILED_LOAD)], [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def card(self, p: AppPage):
        """The card on show."""
        return p.page.locator("section.rec-check").first

    def skills(self) -> dict:
        """Two skills both a project and a CV name, so they are asked about, and one from a lecture only."""
        ids = {name: found("skill", {"name": name}, PROJECT, "notes", name.lower())
               for name in ("Oral history interviewing", "Transcription")}
        for name in ids:
            found("skill", {"name": name}, CV, "cv", name.lower())
        ids["Palaeography"] = found("skill", {"name": "Palaeography"}, MODULE, "notes", "palaeography")
        return ids

    def test_the_queue_asks_the_conflict_then_each_fact_then_the_skills_and_ends_ready(self) -> None:
        job = found("job", {"title": "Collections volunteer", "employer": EMPLOYER, "start": "Sept 2023"},
                    quote="Collections volunteer, Sept 2023 - present")
        found("job", {"title": "Collections volunteer", "employer": EMPLOYER, "start": "October 2023"}, LETTER,
              "letter", "since October 2023")
        name = found("basic", {"key": "name", "value": "Alex Doe"}, quote="Alex Doe")
        ids = self.skills()
        p = self.page()
        self.assertEqual(p.page.inner_text("#main h1"), "Check your record")
        p.shows(self.card(p).get_by_text("Which is right?"), "the conflict first")
        self.assertIn("0 of 4 checked.", p.page.inner_text("#main"))
        self.clean(p)
        self.card(p).get_by_role("button", name="Use October 2023").click()
        p.shows(self.card(p).get_by_text("Alex Doe"), "the name")
        self.assertEqual(state(job), "edited", "choosing a value checks the job too")
        self.card(p).get_by_role("button", name="Yes, that's right").click()
        p.shows(self.card(p).get_by_text("Skills from Alex Doe CV"), "the skills group, from the file trusted most")
        self.assertEqual(state(name), "confirmed")
        self.assertIn("2 of 4 checked.", p.page.inner_text("#main"))
        self.card(p).get_by_label("Transcription").uncheck()
        self.card(p).get_by_role("button", name="Keep the ticked ones").click()
        p.shows(p.page.get_by_text("Your record is ready"), "the end")
        self.assertEqual([state(ids[n]) for n in ("Oral history interviewing", "Transcription", "Palaeography")],
                         ["confirmed", "hidden", "hal"])
        self.clean(p)

    def test_fix_it_saves_the_persons_words_and_not_me_hides_the_fact(self) -> None:
        job = found("job", {"title": "Collections volunteer", "employer": EMPLOYER}, quote="Collections volunteer")
        cert = found("certificate", {"name": "Forklift licence"}, quote="Forklift licence")
        p = self.page()
        card = self.card(p)
        p.shows(card.get_by_text("Collections volunteer").first, "the job")
        self.assertIn('"Collections volunteer"', card.inner_text(), "the quote it came from")
        card.get_by_role("button", name="Fix it").click()
        card.get_by_label("Job title").fill("Collections assistant")
        card.get_by_role("button", name="Save").click()
        p.shows(self.card(p).get_by_text("Forklift licence").first, "the certificate")
        self.assertEqual(state(job), "edited")
        self.card(p).get_by_role("button", name="Not me").click()
        p.shows(p.page.get_by_text("Your record is ready"), "the end")
        self.assertEqual(state(cert), "hidden")

    def test_skills_hal_is_less_sure_of_wait_under_show_more(self) -> None:
        ids = self.skills()
        with closing(db.connect()) as con:
            store.confirm_many(con, [ids["Oral history interviewing"], ids["Transcription"]])
        p = self.page()
        p.shows(p.page.get_by_text("Your record is ready"), "the end of the main queue")
        more = p.page.locator("details.rec-check-more")
        more.locator("summary").click()
        more.get_by_role("button", name="None of these").click()
        p.page.locator("details.rec-check-more").wait_for(state="detached")
        self.assertEqual(state(ids["Palaeography"]), "hidden")

    def test_at_phone_width_the_buttons_are_large_and_the_keyboard_moves_on(self) -> None:
        found("basic", {"key": "name", "value": "Alex Doe"}, quote="Alex Doe")
        found("basic", {"key": "email", "value": "alex@example.org"}, quote="alex@example.org")
        p = self.page("check", "narrow", "dark")
        yes = self.card(p).get_by_role("button", name="Yes, that's right")
        p.shows(yes, "the first card")
        self.assertGreaterEqual(yes.bounding_box()["height"], 44)
        self.clean(p)
        yes.focus()
        p.page.keyboard.press("Enter")
        p.shows(self.card(p).get_by_text("alex@example.org").first, "the next card")
        p.page.locator("#main .rec-check-h:focus").wait_for()
        self.assertEqual(p.focused(), "h2:alex@example.org")

    def test_your_record_links_to_the_check_page(self) -> None:
        found("basic", {"key": "name", "value": "Alex Doe"}, quote="Alex Doe")
        p = self.page("record")
        p.page.get_by_role("button", name="Check your record").click()
        p.shows(p.page.locator("#main h1", has_text="Check your record"), "the check page")
        self.assertTrue(p.page.url.endswith("#check"))


if __name__ == "__main__":
    unittest.main()

"""Edit your CV in a real browser, against a real server in a temporary home with a made-up imported CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. This checks editing by keyboard, the names a screen reader reads,
phone width, adding from your record, the unsaved-changes guard and the "your CV changed" answer.
"""
from __future__ import annotations

import contextlib
import unittest

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_import import lay_out

from jobhunt import db
from jobhunt.cvstudio import import_v1, model, store
from jobhunt.profile import store as profile_store

JOB = "Lab assistant"
CONFLICT = "409 (Conflict)"   # the browser logs the "your CV changed" answer as a failed load
CLERK = {"title": "Ticket clerk", "employer": "Made-up Rail", "start": "2023-09", "end": "2024-06",
         "duties": ["Sold tickets at the morning window"]}


def entry(doc: model.CVDoc, heading: str) -> model.Entry:
    """The entry of the document with this heading."""
    return next(e for s in doc.sections for e in s.entries if e.heading == heading)


@skip_unless_browser
class EditCv(unittest.TestCase):
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
        self.first = store.current()[0]

    def page(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open("cv-edit")
        p.shows(p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']"), "the first line of the job")
        return p

    def test_lines_are_edited_added_moved_and_locked_by_keyboard_and_saved_as_one_version(self) -> None:
        p = self.page()
        first = p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']")
        first.fill("Helped 40 first-years debug their labs")
        p.page.locator(".cv-entry", has_text=JOB).locator("button:has-text('Add a line')").focus()
        p.page.keyboard.press("Enter")
        p.page.wait_for_selector(f"textarea[aria-label='Line 3 of {JOB}']:focus")
        p.page.keyboard.type("Ran the Friday help desk")
        p.page.locator(f"button[aria-label='Move line 3 of {JOB} up']").focus()
        p.page.keyboard.press("Enter")
        p.page.locator(f"button[aria-label='Lock line 1 of {JOB}']").focus()
        p.page.keyboard.press("Space")
        n = len(store.versions())
        p.page.locator("button:has-text('Save changes')").focus()
        p.page.keyboard.press("Enter")
        p.page.wait_for_selector(f"text=Saved your CV as version {n + 1}.")
        self.assertEqual(len(store.versions()), n + 1)
        job = entry(store.current()[1], JOB)
        self.assertEqual([b.text for b in job.bullets],
                         ["Helped 40 first-years debug their labs", "Ran the Friday help desk", "Marked weekly labs"])
        self.assertTrue(job.bullets[0].locked)
        self.assertEqual([model.YOUR_WORDS in b.sources for b in job.bullets], [True, True, False])
        p.shows(p.page.locator(".cv-line .chip:has-text('Your words')"), "the Your words mark")
        self.assertEqual(p.errors, [])

    def test_every_control_is_named_and_the_page_fits_a_phone(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                for name in (f"Delete line 2 of {JOB}", f"Lock {JOB}", f"Show {JOB}", f"Move {JOB} up"):
                    self.assertEqual(p.page.locator(f"[aria-label='{name}']").count(), 1, name)
                self.assertEqual(p.page.locator(".cv-entry[role=group][aria-label='Lab assistant']").count(), 1)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_a_card_from_your_record_is_added_with_its_lines(self) -> None:
        con = db.connect()
        try:
            card = profile_store.add_by_hand(con, "job", CLERK)
        finally:
            con.close()
        p = self.page()
        section = p.page.locator("section.card", has=p.page.locator("h2", has_text="Experience"))
        section.locator("button:has-text('Add from your record')").click()
        section.locator("button[aria-label='Add Ticket clerk']").click()
        p.shows(p.page.locator(".cv-entry[aria-label='Ticket clerk']"), "the added entry")
        p.page.click("button:has-text('Save changes')")
        p.page.wait_for_selector("text=Saved your CV as version")
        clerk = entry(store.current()[1], "Ticket clerk")
        self.assertEqual((clerk.org, clerk.start, clerk.end), ("Made-up Rail", "September 2023", "June 2024"))
        self.assertEqual([(b.text, b.sources) for b in clerk.bullets],
                         [("Sold tickets at the morning window", [f"card:{card['id']}"])])

    def test_leaving_with_unsaved_changes_asks_first(self) -> None:
        p = self.page()
        p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").fill("Marked weekly labs and wrote feedback")
        p.page.locator(".tab", has_text="Versions").click()
        p.page.wait_for_selector("text=Leave without saving?")
        p.page.click("button:has-text('Stay here')")
        self.assertEqual(p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").input_value(),
                         "Marked weekly labs and wrote feedback")
        p.page.click("nav >> text=Jobs")
        p.page.click("button:has-text('Leave without saving')")
        p.page.wait_for_selector("#main h1:has-text('Jobs')")
        self.assertEqual(store.current()[0], self.first)

    def test_a_save_after_your_cv_changed_elsewhere_says_so_and_saves_nothing(self) -> None:
        p = self.page()
        p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").fill("Marked weekly labs and wrote feedback")
        _, doc = store.current()
        elsewhere = store.new_version(model.apply(doc, model.Change("replace", "s:summary/text", "Changed elsewhere.")),
                                      origin="manual", what="Elsewhere", by="you")
        p.page.click("button:has-text('Save changes')")
        p.page.wait_for_selector("text=Your CV changed since you started editing")
        self.assertEqual(store.current()[0], elsewhere)
        p.page.click("button:has-text('Start again from your CV now')")
        p.page.wait_for_selector("text=Start again from your CV now", state="detached")
        profile = p.page.locator("section.card", has=p.page.locator("h2", has_text="Profile")).locator("textarea")
        self.assertEqual(profile.input_value(), "Changed elsewhere.")
        self.assertEqual(p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").input_value(), "Marked weekly labs")
        self.assertEqual([e for e in p.errors if CONFLICT not in e], [])


if __name__ == "__main__":
    unittest.main()

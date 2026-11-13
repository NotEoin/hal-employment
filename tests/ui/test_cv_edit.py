"""Edit your CV in a real browser, against a real server in a temporary home with a made-up imported CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. This checks editing by keyboard, the names a screen reader reads,
phone width, adding from your record, the unsaved-changes guard and the "your CV changed" answer, on My CV
and on an application's CV, and ticking what the job cares about first.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_import import lay_out

from jobhunt import db
from jobhunt.cvstudio import cv, import_v1, model, store, tailor
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


APP_JOB = "test:lab-1"
HALS = "Helped 40 first-years debug their weekly labs"   # Hal's rewording of line 1 for the job
MINE = "Marked weekly labs and wrote feedback"


def draw(variant: dict, out: Path) -> Path:
    """A made-up PDF maker, so the preview has a file without a browser printing one."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "cv.pdf").write_bytes(b"%PDF-1.4 made-up\n%%EOF\n")
    return out / "cv.pdf"


@skip_unless_browser
class EditJobCv(unittest.TestCase):
    """The application's CV step: the same editor on the job's CV, Hal's changes and yours apart."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(cv, "render", draw))
        lay_out(import_v1.folder())
        vid, doc = store.current()
        sec = next(s for s in doc.sections if any(e.heading == JOB for e in s.entries))
        job = entry(doc, JOB)
        line = model.target_of(sec, job, job.bullets[0])
        hal = [{"id": "c1", "kind": "Reworded", "where": JOB, "op": "replace", "target": line, "value": HALS,
                "before": job.bullets[0].text, "after": HALS, "why": "The advert asks for teaching.", "ev": ""}]
        con = db.connect()
        try:
            con.execute("INSERT INTO jobs (id, source, company, title, status, first_seen, last_seen) VALUES "
                        "(?, 'test', 'Made-up Labs', 'Teaching assistant', 'applying', ?, ?)",
                        (APP_JOB, db.now(), db.now()))
            con.execute("INSERT INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                        (APP_JOB, db.now()))
            con.execute("INSERT INTO job_cvs (job_id, base_version_id, level, changes) VALUES (?, ?, 'little', ?)",
                        (APP_JOB, vid, json.dumps(hal)))
            con.commit()
        finally:
            con.close()
        self.main = vid

    def page(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open("app-" + quote(APP_JOB, safe=""))
        p.shows(p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']"), "the first line of the job")
        return p

    def line(self, p: AppPage, n: int):
        return p.page.locator(".cv-line", has=p.page.locator(f"textarea[aria-label='Line {n} of {JOB}']"))

    def test_hals_change_and_yours_are_marked_apart_saved_by_keyboard_and_copied_to_your_main_cv(self) -> None:
        p = self.page()
        self.assertEqual(p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']").input_value(), HALS)
        self.assertIn("Hal: Reworded", self.line(p, 1).inner_text())
        p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").focus()
        p.page.keyboard.press("Control+A")
        p.page.keyboard.type(MINE)
        p.page.locator("button:has-text('Save changes')").focus()
        p.page.keyboard.press("Enter")
        p.page.wait_for_selector("text=Saved your CV for this job.")
        p.shows(self.line(p, 2).locator(".chip:has-text('Your edit')"), "the Your edit mark")
        self.assertNotIn("Hal:", self.line(p, 2).inner_text())
        p.shows(p.page.locator("iframe[title='CV preview']"), "the PDF preview after the save")
        self.assertEqual(tailor.shown(APP_JOB)["edits"], 1)
        self.assertEqual(store.current()[0], self.main, "the main CV stays as it is")
        p.page.locator(f"button[aria-label='Copy to my main CV: {JOB}']").focus()
        p.page.keyboard.press("Enter")
        p.page.wait_for_selector(f"text=Copied {JOB} to your main CV")
        self.assertEqual([b.text for b in entry(store.current()[1], JOB).bullets], [HALS, MINE])
        p.page.locator("article[data-change='c1'] button:has-text('Undo')").focus()
        p.page.keyboard.press("Enter")
        p.page.wait_for_selector("article[data-change='c1'] button:has-text('Redo')")
        first = p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']")
        self.assertEqual(first.input_value(), "Helped 40 first-years")
        self.assertEqual(p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").input_value(), MINE)
        self.assertEqual(p.errors, [])

    def test_approve_waits_for_unsaved_changes_which_survive_a_tab_switch(self) -> None:
        p = self.page()
        p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").fill(MINE)
        approve = p.page.locator("button:has-text('Approve and fill in the form')")
        p.page.wait_for_selector("text=Save or discard your changes to the CV before you approve.")
        self.assertTrue(approve.is_disabled())
        p.page.locator("[role=tab]:has-text('Cover letter')").click()
        p.page.locator("[role=tab]:has-text('CV')").first.click()
        self.assertEqual(p.page.locator(f"textarea[aria-label='Line 2 of {JOB}']").input_value(), MINE)
        p.page.click("button:has-text('Discard changes')")
        p.page.click("[role=dialog] button:has-text('Discard changes')")
        p.page.wait_for_selector("button:has-text('Approve and fill in the form'):enabled")
        self.assertEqual(p.errors, [])

    def test_every_control_is_named_and_the_step_fits_a_phone(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                for name in (f"Copy to my main CV: {JOB}", f"Delete line 2 of {JOB}", f"Lock {JOB}"):
                    self.assertEqual(p.page.locator(f"[aria-label='{name}']").count(), 1, name)
                self.assertEqual(p.page.locator("h2:has-text('What Hal changed for this job')").count(), 1)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])


NEEDS = ["Teaching first-year labs", "Marking lab reports", "Running a help desk", "Safety training"]


@skip_unless_browser
class PickFirstJobCv(unittest.TestCase):
    """The application's CV step with "Ask me what the job cares about first" on: tick, then the job's CV."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        from jobhunt.core import settings

        EditJobCv.setUp(self)   # the same made-up application with Hal's change on line 1
        settings.set("cvdocs.pick_first", True, by="you")
        con = db.connect()
        try:
            for n, text in enumerate(NEEDS, 1):
                con.execute("INSERT INTO job_requirements (job_id, n, text, state, updated) VALUES (?, ?, ?, 'met', ?)",
                            (APP_JOB, n, text, db.now()))
            con.commit()
        finally:
            con.close()

    def page(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open("app-" + quote(APP_JOB, safe=""))
        p.shows(p.page.get_by_role("checkbox", name=NEEDS[0]), "the first requirement to tick")
        return p

    def test_three_ticked_by_keyboard_adjust_the_jobs_cv_and_then_the_editor_shows(self) -> None:
        p = self.page()
        adjust = p.page.get_by_role("button", name="Adjust my CV for these")
        self.assertTrue(adjust.is_disabled())
        self.assertTrue(p.page.locator("button:has-text('Approve and fill in the form')").is_disabled())
        p.page.get_by_role("checkbox", name=NEEDS[0]).focus()
        for _ in range(3):
            p.page.keyboard.press("Space")
            p.page.keyboard.press("Tab")
        p.page.wait_for_selector("text=3 ticked")
        adjust.focus()
        p.page.keyboard.press("Enter")
        p.shows(p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']"), "the job's CV after the picks")
        self.assertEqual(tailor.picked(APP_JOB), NEEDS[:3])
        self.assertEqual(p.page.get_by_role("checkbox", name=NEEDS[0]).count(), 0, "asked once")
        self.assertEqual(p.errors, [])

    def test_every_requirement_is_named_and_the_step_fits_a_phone(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                for name in NEEDS:
                    self.assertEqual(p.page.get_by_role("checkbox", name=name).count(), 1, name)
                self.assertEqual(p.page.get_by_role("group", name="What does this job care about most?").count(), 1)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

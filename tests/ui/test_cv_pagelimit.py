"""A CV's page limit in a real browser, against a real server with a made-up imported CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. The page count comes from a stand-in printer that says every CV
prints on three pages (`PRINTED`), so no browser prints here; the real printer is tested in test_cv_printer.
This checks the Length card on Type and look at both sizes, the warning after a save in Edit your CV, and an
application's CV over its limit: the CV tab and the approve box say so, with the change Hal undid to fit.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_import import lay_out

from jobhunt import db
from jobhunt.cvstudio import cv, import_v1, model, pagelimit, store, tailor
from ui.test_cv_edit import APP_JOB, HALS, draw

PRINTED = 3                 # pages the stand-in printer gives every CV
JOB = "Lab assistant"       # an entry of the imported made-up CV (`test_cv_import.SITE`)


def three_pages(html, out, expect_name, expect_text) -> int:
    """The stand-in printer."""
    return PRINTED


@skip_unless_browser
class PageLimit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(pagelimit, "PRINT", three_pages))
        lay_out(import_v1.folder())
        store.current()
        self.doc_id = store.lead_id()

    def page(self, route: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def test_keep_this_cv_to_is_chosen_on_type_and_look_and_warns_with_the_real_count(self) -> None:
        for size, scheme, pages, words in (("wide", "light", 1, "One page"), ("narrow", "dark", 2, "Two pages")):
            with self.subTest(size=size):
                p = self.page("cv-style", size, scheme)
                card = p.page.locator("section.card", has_text="Keep this CV to")
                p.shows(card.get_by_role("radiogroup", name="Keep this CV to"), "the three choices")
                card.get_by_role("radio", name=words).click()
                # the toast, after the choice is stored: one page is also the starting type's own rule
                p.page.wait_for_selector("text=Saved. Hal keeps an eye on this CV's length.")
                p.page.wait_for_selector(f"text=This CV is {PRINTED} pages. You asked for {pages}.")
                self.assertEqual(pagelimit.limit_of(self.doc_id), (pages, True))
                self.assertEqual(card.get_by_role("radio", name=words).get_attribute("aria-checked"), "true")
                card.get_by_role("radio", name="No limit").click()
                p.page.wait_for_selector(f"text=It prints on {PRINTED} pages now.")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_a_copied_type_of_three_pages_shows_three_pages_on_the_card(self) -> None:
        from test_cv_pagelimit import three_page_type

        store.set_type(three_page_type(), doc_id=self.doc_id)
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("cv-style", size, scheme)
                card = p.page.locator("section.card", has_text="Keep this CV to")
                three = card.get_by_role("radio", name="Three pages")
                p.shows(three, "the type's own longer limit")
                self.assertEqual(three.get_attribute("aria-checked"), "true")
                self.assertIn(f"It prints on {PRINTED} pages now.", card.inner_text())
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_a_save_that_runs_over_the_limit_is_kept_and_warned_about(self) -> None:
        store.set_page_limit(2, doc_id=self.doc_id)
        for size in ("wide", "narrow"):
            with self.subTest(size=size):
                p = self.page("cv-edit", size)
                line = p.page.locator(f"textarea[aria-label='Line 1 of {JOB}']")
                p.shows(line, "the first line of the job")
                line.fill(f"Helped first-years debug their labs ({size})")
                n = len(store.versions())
                p.page.locator("button:has-text('Save changes')").click()
                p.page.wait_for_selector(f"text=This CV is now {PRINTED} pages. You asked for 2.")
                self.assertEqual(len(store.versions()), n + 1, "the save is never refused")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])


@skip_unless_browser
class JobCvLength(unittest.TestCase):
    """An application's CV over its page limit: the CV tab and the approve box say so, Approve still works."""

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
        self.enterContext(mock.patch.object(pagelimit, "drawn_pages", lambda v1: PRINTED))
        lay_out(import_v1.folder())
        vid, doc = store.current()
        store.set_page_limit(2)
        sec = next(s for s in doc.sections if any(e.heading == JOB for e in s.entries))
        job = next(e for e in sec.entries if e.heading == JOB)
        line = model.target_of(sec, job, job.bullets[0])
        held = [{"id": "c1", "kind": "Reworded", "where": JOB, "op": "replace", "target": line, "value": HALS,
                 "before": job.bullets[0].text, "after": HALS, "why": "The advert asks for teaching.", "ev": "",
                 "undone": True, "held": tailor.HELD_FIT}]
        con = db.connect()
        try:
            con.execute("INSERT INTO jobs (id, source, company, title, status, first_seen, last_seen) VALUES "
                        "(?, 'test', 'Made-up Labs', 'Teaching assistant', 'applying', ?, ?)",
                        (APP_JOB, db.now(), db.now()))
            con.execute("INSERT INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                        (APP_JOB, db.now()))
            con.execute("INSERT INTO job_cvs (job_id, base_version_id, level, changes) VALUES (?, ?, 'more', ?)",
                        (APP_JOB, vid, json.dumps(held)))
            con.commit()
        finally:
            con.close()

    def test_the_cv_tab_and_the_approve_box_give_the_real_count_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
                self.addCleanup(p.close)
                p.page.goto(self.hal.open_url())
                p.open("app-" + quote(APP_JOB, safe=""))
                over = f"This CV is {PRINTED} pages. You asked for 2."
                p.shows(p.page.locator(".approve .cv-length", has_text=over + " You can still approve it."),
                        "the warning before Approve")
                note = p.page.locator(".cv-job .cv-length")
                p.shows(note, "the warning on the CV tab")
                self.assertIn(over, note.inner_text())
                # the stand-in prints every CV on 3 pages, so the CV is over before Hal changes anything
                self.assertIn("Without Hal's changes your CV is already 3 pages", note.inner_text())
                self.assertIn("undid 1 that would", note.inner_text())
                held = p.page.locator("article[data-change='c1']", has_text="Undone by Hal to keep to your page limit")
                p.shows(held, "why Hal undid its change")
                self.assertTrue(p.page.locator("button:has-text('Approve and fill in the form')").is_enabled())
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])


if __name__ == "__main__":
    unittest.main()

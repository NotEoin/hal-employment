"""My CV in a real browser, against a real server in a temporary home with a made-up imported CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. This checks the header, Your CV, the Sections card, Versions,
the "How your CV is made" dialog, and the list of CVs once there are several ("My CVs").
"""
from __future__ import annotations

import contextlib
import threading
import unittest

from support.api import HalServer
from support.fake_ai import fake_ai
from support.make_cv_fixtures import FOLDER, NAMES
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_import import SITE, lay_out
from test_cv_parse import held_ai

from jobhunt.ai.base import LLMUnavailable
from jobhunt.cvstudio import import_v1, store

# Home's summary, which this test's bare server does not draw.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"
READ_S = 20                          # long enough for the upload to reach the AI on a loaded machine
READING = "Hal is reading this CV"   # cv/cv-list.js READING_WORDS


@skip_unless_browser
class CvPage(unittest.TestCase):
    def errors(self, p: AppPage) -> list[str]:
        """Console errors, leaving out failed loads that are all of addresses still to come."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        return [e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours

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
        store.current()

    def page(self, route: str = "cv", size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def test_the_page_says_what_employers_get_and_draws_cleanly_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("cv", size, scheme)
                p.page.wait_for_selector("text=At a glance")
                self.assertEqual(p.page.inner_text("#main h1"), "My CV")
                self.assertIn("Employers get: Hal's copy of your CV, version 4.", p.page.inner_text("#main"))
                paper = p.page.frame_locator("iframe[title='Your CV']").locator("main").inner_text()
                self.assertIn(SITE["education"][0]["qualification"], paper)
                self.assertIn("Tree index in C", paper)
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def keep_file(self, name: str) -> None:
        """The made-up file `name` kept as the lead CV's own file, as an upload keeps it."""
        store.set_baseline_file(store.add_file((FOLDER / name).read_bytes(), name, role="baseline"))

    def test_sending_your_own_pdf_previews_its_pages_and_hals_copy_comes_back_when_you_switch(self) -> None:
        # 2026-11-19: with "Your original file" chosen, the preview still drew Hal's copy
        name = f"{NAMES['technical']}.pdf"
        self.keep_file(name)
        for size, scheme in (("narrow", "dark"), ("wide", "light")):
            with self.subTest(size=size):
                store.set_send("hal_copy")
                p = self.page("cv", size, scheme)
                p.page.wait_for_selector("iframe[title='Your CV']")
                p.page.click("button[role=radio]:has-text('Your original file')")
                picture = p.page.wait_for_selector("img[alt='Page 1 of 1 of your CV file']")
                p.page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=picture)
                self.assertEqual(p.page.locator("iframe[title='Your CV']").count(), 0)
                self.assertIn(f"This is your file, {name}, as employers get it.", p.page.inner_text("#main"))
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                p.page.click("button[role=radio]:has-text(\"Hal's copy\")")
                p.page.wait_for_selector("iframe[title='Your CV']")
                self.assertEqual(p.page.locator("img[alt$='of your CV file']").count(), 0)
                self.assertEqual(self.errors(p), [])

    def test_sending_your_own_word_file_shows_hals_reading_and_says_so(self) -> None:
        name = f"{NAMES['changer']}.docx"
        self.keep_file(name)
        store.set_send("original")
        p = self.page()
        p.page.wait_for_selector("iframe[title='Your CV']")
        self.assertIn(f"Employers get your file, {name}, as you uploaded it. Hal cannot show a Word file as pages, "
                      "so this is Hal's reading of it.", p.page.inner_text(".sent-preview"))
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.serious_axe(), [])

    def test_with_no_cv_file_your_file_offers_the_upload(self) -> None:
        # My CV said "You have not added a CV file" with no way to add one there
        p = self.page()
        p.page.wait_for_selector("text=At a glance")
        card = p.page.locator("section.card", has_text="Your file")
        self.assertIn("Drop your CV here", card.inner_text())
        self.assertEqual(self.errors(p), [])

    def test_an_old_version_can_be_used_again_and_compared(self) -> None:
        p = self.page("cv-versions")
        p.page.wait_for_selector("text=Version 4")
        p.page.click("input[aria-label='Pick version 1']")
        p.page.click("input[aria-label='Pick version 4']")
        p.page.click("button:has-text('Compare 1 and 4')")
        p.page.wait_for_selector("text=From version 1 to version 4")
        self.assertIn("Tree index in C", p.page.inner_text(".cv-changes"))
        p.page.locator(".li", has_text="Version 3").locator("button:has-text('Use this version')").click()
        p.page.wait_for_selector("text=Version 3 is now in use. Version 4 is kept.")
        self.assertEqual(store.version(store.current()[0])["n"], 3)
        p.page.wait_for_selector(".cv-employers:has-text('version 3.')")
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.serious_axe(), [])

    def test_renaming_a_section_saves_a_new_version(self) -> None:
        p = self.page("cv-style")
        box = p.page.locator(".secrow input[aria-label='Section name']").nth(3)
        box.fill("Work experience")
        box.press("Tab")
        p.page.locator(".tab", has_text="Versions").click()
        p.page.wait_for_selector("text=You changed your CV's sections")
        self.assertEqual(store.current()[1].sections[3].title, "Work experience")
        self.assertEqual(self.errors(p), [])

    def test_the_route_dialog_says_why_your_file_cannot_be_sent(self) -> None:
        p = self.page("cv")
        p.page.click("button:has-text('Change how your CV is made')")
        p.page.click("button[role=radio]:has-text('Use my CV as it is')")
        p.page.click("button:has-text('Use this')")
        p.page.wait_for_selector("text=Hal has no file of yours to send as it is. Add your CV file first.")
        self.assertEqual(store.doc()["send"], "hal_copy")

    def test_several_cvs_switch_star_archive_and_restore_under_my_cvs(self) -> None:
        _, doc = store.current()
        museum = store.create("Museum roles", doc, origin="uploaded", what="Your file: museum.pdf", by="baseline")
        p = self.page("cv-versions")
        p.page.wait_for_selector(".cv-pick.on:has-text('Museum roles')")
        self.assertEqual(p.page.inner_text("#main h1"), "My CVs")
        self.assertIn("My CVs", p.page.inner_text("nav.rail"))
        p.page.wait_for_selector("text=Version 1")
        p.page.wait_for_selector("input[aria-label^='Pick version']")   # the list draws after its heading
        self.assertEqual(p.page.locator("input[aria-label^='Pick version']").count(), 1)
        p.page.click(".cv-name:has-text('My CV')")
        p.page.wait_for_selector("text=Version 4")
        p.page.wait_for_selector(".cv-employers:has-text('version 4.')")
        p.page.click("button[aria-label='Use this for applications: Museum roles']")
        p.page.wait_for_selector("button[aria-label='Use this for applications: Museum roles'][aria-pressed=false]")
        p.page.click("button[aria-label='Use this for applications: My CV']")
        p.page.wait_for_selector("text=Hal needs one CV to use for applications")
        self.assertTrue(store.doc(doc_id=store.DOC_ID)["starred"])
        p.page.click(".cv-name:has-text('Museum roles')")
        p.page.click("button:has-text('Move to the archive')")
        p.page.wait_for_selector("button:has-text('Archived CVs (1)')")
        self.assertEqual(p.page.locator(".cv-pick").count(), 1)
        self.assertTrue(store.doc(doc_id=museum)["archived"])
        p.page.click("button:has-text('Archived CVs (1)')")
        p.page.click(".cv-archive button:has-text('Restore')")
        p.page.wait_for_selector(".cv-pick:has-text('Museum roles')")
        self.assertIsNone(store.doc(doc_id=museum)["archived"])
        # the one 400 is the answer that says why the last star stays on
        refused = [u for u in p.missing if u.endswith("/api/v2/cv/star?cv=main")]
        self.assertEqual(len(refused), 1)
        self.assertEqual([u for u in p.missing if u not in refused]
                         + [e for e in p.errors if not e.startswith(FAILED_LOAD)], [])
        self.assertEqual(p.serious_axe(), [])

    def test_an_archived_cv_is_deleted_only_after_a_plain_question_at_phone_width(self) -> None:
        _, doc = store.current()
        museum = store.create("Museum roles", doc, origin="uploaded", what="Your file: museum.pdf", by="baseline")
        store.archive(museum)
        p = self.page("cv", "narrow", "dark")
        p.page.click("button:has-text('Archived CVs (1)')")
        delete = "button[aria-label='Delete Museum roles']"
        p.page.focus(delete)
        p.page.keyboard.press("Enter")
        box = p.page.wait_for_selector("[role=dialog]:has-text('Delete Museum roles?')")
        self.assertIn("all of its versions and Hal's suggestions for it", box.inner_text())
        self.assertIn("This cannot be undone.", box.inner_text())
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        p.page.keyboard.press("Escape")
        p.page.wait_for_selector("[role=dialog]", state="detached")
        self.assertEqual(p.focused(), "button:Delete Museum roles", "focus goes back to the button")
        self.assertTrue(store.doc(doc_id=museum)["archived"], "kept when the person says no")
        p.page.keyboard.press("Enter")
        p.page.locator("[role=dialog] button:has-text('Delete this CV')").press("Enter")
        p.page.wait_for_selector("text=Museum roles is deleted.")
        p.page.wait_for_selector("button:has-text('Archived CVs')", state="detached")
        with self.assertRaises(store.NoSuchCV):
            store.doc(doc_id=museum)
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.sideways(), 0)

    def test_adding_another_cv_makes_a_new_cv_at_version_one(self) -> None:
        self.enterContext(fake_ai({"cv_parse": LLMUnavailable("The made-up AI is switched off.")}))
        p = self.page("cv")
        p.page.wait_for_selector("text=At a glance")
        p.page.click("button:has-text('Add another CV')")
        p.page.locator(".cv-list input[aria-label='Choose your CV']").set_input_files(
            str(FOLDER / f"{NAMES['humanities']}.pdf"))
        p.page.wait_for_selector(f".cv-pick.on:has-text('{NAMES['humanities']}')")
        p.page.wait_for_selector(".cv-employers:has-text('version 1.')")
        self.assertEqual(p.page.inner_text("#main h1"), "My CVs")
        self.assertEqual([v["n"] for v in store.versions(doc_id=store.lead_id())], [1])
        self.assertEqual(store.versions(doc_id=store.DOC_ID)[0]["n"], 4, "the CV from before keeps its history")
        self.assertEqual(self.errors(p), [])

    def test_an_added_cv_is_listed_and_marked_while_hal_reads_it(self) -> None:
        """An uploaded CV was not on My CVs until the AI had read it (2026-11-19)."""
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.enterContext(fake_ai({"cv_parse": LLMUnavailable("The made-up AI is switched off.")}))
        self.enterContext(held_ai(entered, release))
        p = self.page("cv")
        p.page.wait_for_selector("text=At a glance")
        p.page.click("button:has-text('Add another CV')")
        p.page.locator(".cv-list input[aria-label='Choose your CV']").set_input_files(
            str(FOLDER / f"{NAMES['technical']}.pdf"))
        self.assertTrue(entered.wait(READ_S), "the AI is reading the file")
        p.page.wait_for_selector(f".cv-pick.on:has-text('{NAMES['technical']}') .cv-reading:has-text('{READING}')")
        self.assertEqual(p.page.locator(".cv-reading").count(), 1, "only the CV being read is marked")
        release.set()
        p.page.wait_for_selector(".cv-reading", state="detached")
        p.page.wait_for_selector(f".cv-pick.on:has-text('{NAMES['technical']}')")
        self.assertEqual(self.errors(p), [])


if __name__ == "__main__":
    unittest.main()

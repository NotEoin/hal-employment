"""Adding a CV and "Check what Hal read" in a real browser: the setup guide's CV step, the upload part and
the dialog, against a real server in a temporary home with the made-up CVs of `make_cv_fixtures.py`.

Needs `HAL_TEST_BROWSER=1` and Playwright. The AI is the fake one, switched off, so the rules read the CV.
The step is drawn on its own over the app, with the app's own modules, so the test does not walk the guide's
earlier steps; the dialog opens in the app's dialog host as it will from the guide.
"""
from __future__ import annotations

import contextlib
import json
import time
import unittest

from support.api import HalServer
from support.fake_ai import fake_ai
from support.make_cv_fixtures import FOLDER, NAMES
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.ai.base import LLMUnavailable

FAILED_LOAD = "Failed to load resource"
SETTLE_MS = 500   # a reply's re-draw takes a few milliseconds; this only gives a wrong one time to show
HELD_WAIT_S = 10  # the step asks for the saved route after it draws; under load that can take seconds
POLL_MS = 50
MOUNT_STEP = """async () => {
  const [{ html, render }, { Step }] = await Promise.all([import("/app/core/html.js"),
                                                         import("/app/setup/steps/cv.js")]);
  const host = document.createElement("div");
  host.className = "card";
  host.id = "test-step";
  document.querySelector("#main").before(host);
  render(html`<${Step} />`, host);
}"""


@skip_unless_browser
class CheckRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(fake_ai({"cv_parse": LLMUnavailable("The made-up AI is switched off.")}))

    def errors(self, p: AppPage) -> list[str]:
        """Console errors, with every failed load named by its address."""
        return [e for e in p.errors if p.missing or not e.startswith(FAILED_LOAD)] + p.missing

    def step(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open("home")
        p.page.evaluate(MOUNT_STEP)
        p.page.wait_for_selector("#test-step h1")
        return p

    def upload(self, p: AppPage, name: str) -> None:
        p.page.set_input_files("input[aria-label='Choose your CV']", str(FOLDER / name))

    def test_the_step_reads_a_pdf_and_says_what_hal_was_not_sure_of(self) -> None:
        p = self.step()
        self.assertEqual(p.page.inner_text("#test-step h1"), "How should we start your CV?")
        self.assertIn("Drop your CV here", p.page.inner_text("#test-step"))
        self.upload(p, f"{NAMES['humanities']}.pdf")
        card = p.page.wait_for_selector("[data-cv-read]")
        text = card.inner_text()
        self.assertIn("Read Marnie-Quillfeather-CV.pdf: 1 page", text)
        self.assertIn("Found your contact details, 1 degree, 2 jobs, 1 volunteering role and 14 skills.", text)
        self.assertIn("I was not sure about one thing: the dates for Student Ambassador (Sept 24 to Jun 25).", text)
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def test_check_what_hal_read_shows_the_file_and_keeps_a_correction(self) -> None:
        p = self.step()
        self.upload(p, f"{NAMES['humanities']}.pdf")
        p.page.click("button:has-text('Check what I read')")
        dialog = p.page.wait_for_selector("[role=dialog] img[alt='Page 1 of your CV']")
        self.assertTrue(dialog.evaluate("img => img.complete && img.naturalWidth > 0"))
        body = p.page.inner_text("[role=dialog]")
        self.assertIn("Check what Hal read", body)
        self.assertIn("Your file on the left, Hal's reading on the right.", body)
        self.assertEqual(p.page.locator("[role=dialog] .flag.check").count(), 1)
        self.assertEqual(p.serious_axe(), [])
        p.page.click("[role=dialog] button[aria-label='Edit Student Ambassador dates']")
        p.page.fill("[role=dialog] input.input", "Sep 2024 to Jun 2025")
        p.page.click("[role=dialog] button:has-text('Save')")
        p.page.wait_for_selector("text=Saved. Hal keeps your correction as a new version.")
        p.page.wait_for_selector("[role=dialog] b:has-text('Sep 2024 to Jun 2025')")
        self.assertEqual(p.page.locator("[role=dialog] .flag.check").count(), 0)
        con = db.connect()
        try:
            last = con.execute("SELECT what, by, data FROM cv_versions ORDER BY n DESC LIMIT 1").fetchone()
        finally:
            con.close()
        self.assertEqual((last["what"], last["by"]), ("You corrected 1 thing Hal misread", "you"))
        self.assertIn("Sep 2024", json.dumps(json.loads(last["data"])))
        p.page.click("[role=dialog] button:has-text('Looks right')")
        p.page.wait_for_selector("[role=dialog]", state="detached")
        self.assertEqual(self.errors(p), [])

    def test_a_word_file_shows_as_its_paragraphs_on_a_phone_in_the_dark(self) -> None:
        p = self.step("narrow", "dark")
        self.upload(p, f"{NAMES['changer']}.docx")
        p.page.wait_for_selector("[data-cv-read]")
        p.page.click("button:has-text('Check what I read')")
        p.page.wait_for_selector("[role=dialog] .doc h1:has-text('Priya Ashdown-Vell')")
        self.assertIn("EMPLOYMENT HISTORY", p.page.inner_text("[role=dialog] .doc").upper())
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(self.errors(p), [])

    def test_pasted_text_is_read_and_a_picture_of_a_cv_is_explained(self) -> None:
        p = self.step()
        self.upload(p, "Scanned-CV.pdf")
        alert = p.page.wait_for_selector("#test-step [role=alert]")
        self.assertEqual(alert.inner_text(), "This PDF is a picture of a CV, so Hal cannot read it. Upload the Word "
                                             "file, or paste the text.")
        p.page.click("button:has-text('paste the text')")
        p.page.fill("#cv-paste", (FOLDER / f"{NAMES['technical']}.txt").read_text(encoding="ascii"))
        p.page.click("button:has-text('Read this')")
        card = p.page.wait_for_selector("[data-cv-read]")
        self.assertIn("Read Pasted CV.txt", card.inner_text())
        self.assertIn("2 projects", card.inner_text())
        self.assertEqual([e for e in self.errors(p) if "/api/v2/cv/upload" not in e and FAILED_LOAD not in e], [])

    def test_a_choice_made_before_the_saved_one_arrives_is_kept(self) -> None:
        # regression: on a slow computer (Windows CI, 2026-10-23) the saved route came back after the click and
        # put "help me improve it" back, so the upload showed again under "Build me a fresh CV"
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}")
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url())
        p.open("home")
        held = []
        p.page.route("**/api/v2/cv", lambda route: held.append(route))
        p.page.evaluate(MOUNT_STEP)
        p.page.wait_for_selector("#test-step h1")
        deadline = time.monotonic() + HELD_WAIT_S
        while not held and time.monotonic() < deadline:   # the step's request reaches the route after it draws
            p.page.wait_for_timeout(POLL_MS)
        self.assertEqual(len(held), 1, "the step asked for the saved route once")
        saved = held[0].fetch()   # the server answers now, before the click; the page hears it after
        p.page.click("button:has-text('Build me a fresh CV')")
        p.page.wait_for_selector("text=Next, Hal asks about your education, jobs, volunteering and skills.")
        with p.page.expect_response("**/api/v2/cv"):
            held[0].fulfill(response=saved)
        p.page.wait_for_timeout(SETTLE_MS)
        self.assertEqual(p.page.locator("input[aria-label='Choose your CV']").count(), 0)
        self.assertEqual(p.page.get_attribute("button:has-text('Build me a fresh CV')", "aria-pressed"), "true")

    def test_choosing_build_me_a_fresh_cv_hides_the_upload(self) -> None:
        p = self.step()
        p.page.click("button:has-text('Build me a fresh CV')")
        p.page.wait_for_selector("text=Next, Hal asks about your education, jobs, volunteering and skills.")
        self.assertEqual(p.page.locator("input[aria-label='Choose your CV']").count(), 0)


if __name__ == "__main__":
    unittest.main()

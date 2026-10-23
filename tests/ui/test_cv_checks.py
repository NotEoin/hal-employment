"""My CV's Checks tab, type picker and Writing card in a real browser, against a real server in a temporary
home with a made-up CV.

Needs `HAL_TEST_BROWSER=1` and Playwright. Each part is drawn on its own: the test imports the part's module
into the signed-in app and renders it below the shell, the same component My CV composes (the page itself
is drawn by `ui.test_cv_page` and the shell smoke).
"""
from __future__ import annotations

import contextlib
import unittest

from support.api import HalServer
from support.people import HUMANITIES, as_person
from support.ui import AppPage, chromium, skip_unless_browser
from test_cv_types import humanities_cv

from jobhunt import db
from jobhunt.cvstudio import store

STAR = chr(0x2605)
FAILED_LOAD = "Failed to load resource"
NOT_YET = ("/api/v2/home",)
MOUNT = """async ([path, name]) => {
  const { html, render } = await import('/app/core/html.js');
  const mod = await import(path);
  let box = document.getElementById('part-under-test');
  if (!box) { box = document.createElement('div'); box.id = 'part-under-test'; box.style.padding = '16px';
              document.body.appendChild(box); }
  render(html`<${mod[name]} />`, box);
}"""


@skip_unless_browser
class CvChecksParts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(as_person(HUMANITIES))
        cv = humanities_cv()
        cv.sections[4].entries[0].items.append(f"{STAR} Cataloguing")
        store.new_version(cv, origin="imported", what="Your CV, made up for a test", by="baseline")
        con = db.connect()
        con.execute("UPDATE cv_documents SET type_id = 'humanities_grad' WHERE id = 'main'")
        con.commit()
        con.close()

    def errors(self, p: AppPage) -> list[str]:
        """Console errors, leaving out failed loads that are all of addresses still to come."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        return [e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours

    def part(self, module: str, name: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open("cv")
        p.page.evaluate(MOUNT, [f"/app/cv/{module}.js", name])
        return p

    def test_the_checks_tab_shows_the_score_and_the_types_checks_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.part("checks-tab", "ChecksTab", size, scheme)
                p.page.wait_for_selector("#part-under-test >> text=How job-site scanners read it")
                body = p.page.inner_text("#part-under-test")
                for words in ("Symbols scanners keep", "Checks for your CV type",
                              "Humanities and social science graduate.", "Dissertation and strong modules shown",
                              "A recruiter's read", "It is not part of any score."):
                    self.assertIn(words, body)
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_fix_it_replaces_the_symbols_in_a_new_version(self) -> None:
        p = self.part("checks-tab", "ChecksTab")
        before = store.current()[0]
        p.page.click("#part-under-test >> button:has-text('Fix it')")
        p.page.wait_for_selector("text=Fixed in a new version")
        p.page.wait_for_selector("#part-under-test .ring >> text=100")
        self.assertNotEqual(store.current()[0], before)
        self.assertEqual(self.errors(p), [])

    def test_the_type_picker_changes_the_type_and_shows_a_mistake_with_its_line(self) -> None:
        p = self.part("type-picker", "TypePicker")
        p.page.wait_for_selector("#part-under-test >> text=What kind of CV is this?")
        self.assertIn("Hal suggests: Humanities and social science graduate.", p.page.inner_text("#part-under-test"))
        p.page.click("#part-under-test button.choice:has-text('Career changer')")
        p.page.wait_for_selector("text=CV type changed to Career changer")
        p.page.click("#part-under-test >> button:has-text('Copy this type to change it')")
        box = p.page.wait_for_selector("#part-under-test textarea")
        box.fill(box.input_value() + "\n[checks\n")
        p.page.click("#part-under-test >> button:has-text('Save')")
        alert = p.page.wait_for_selector("#part-under-test [role=alert]")
        self.assertRegex(alert.inner_text(), r"^Line \d+:")
        self.assertEqual(p.serious_axe(), [])
        p.missing = [u for u in p.missing if not u.endswith("/source")]   # the 400 that reported the line
        self.assertEqual(self.errors(p), [])

    def test_the_writing_card_on_a_built_in_type_makes_a_copy_and_says_so(self) -> None:
        p = self.part("writing-settings", "WritingSettings")
        p.page.wait_for_selector("#part-under-test >> text=Never use")
        p.page.click("#part-under-test [aria-label=Tone] >> text=Formal")
        p.page.wait_for_selector("text=Hal made a copy")
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.serious_axe(), [])


if __name__ == "__main__":
    unittest.main()

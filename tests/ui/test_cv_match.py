""""Match my file's look" in a real browser against the real server: the button makes a look from the CV's own
made-up PDF, the file's page shows beside Hal's copy with both page counts, "Make it fit" takes one step, the
look can be renamed, and its gallery card asks before deleting it. Wide and narrow, light and dark, with the
axe check. A stand-in printer counts pages (`test_cv_pagelimit.stand_in`), so no PDF is printed for a count.
"""
from __future__ import annotations

import contextlib
import unittest

from support import layout_pdf, people
from support.ui import chromium, skip_unless_browser
from support.world import World
from test_cv_match_routes import FILE, keep
from test_cv_pagelimit import humanities_cv_in_use, stand_in

from jobhunt.cvstudio import mylooks, store
from ui.test_cv_looks import real_app

MOUNT = """async () => {
  const { html, render } = await import('/app/core/html.js');
  const { LookPicker } = await import('/app/cv/look-picker.js');
  const { LookMatch } = await import('/app/cv/look-match.js');
  const box = document.createElement('section');
  box.id = 'match-test';
  box.setAttribute('aria-label', 'Look');
  document.querySelector('#main').append(box);
  render(html`<${LookPicker} /><${LookMatch} />`, box);
}"""


@skip_unless_browser
class MatchMyFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.enterContext(World())
        self.enterContext(people.as_person(people.HUMANITIES))
        self.enterContext(stand_in())

    def test_a_cv_is_matched_to_its_own_file_and_compared_with_it(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                humanities_cv_in_use()
                keep(layout_pdf.one_line_cv()[0], FILE)
                p.open("home")
                pg = p.page
                pg.evaluate(MOUNT)
                pg.wait_for_selector("#match-test [data-part=match]:not([aria-busy])")
                pg.get_by_role("button", name="Match my file's look").click()
                pg.wait_for_selector("#match-test >> text=Your file is 1 page.")
                pg.wait_for_selector("#match-test img.file-page")
                pg.wait_for_selector("#match-test iframe[title='Your CV in the look Like Made-up-CV.pdf']")
                self.assertIn("Hal's copy in this look is 3 pages.", pg.inner_text("#match-test"))
                look_id = store.doc()["look_id"]
                self.assertTrue(look_id.startswith("my-"))
                pg.get_by_role("button", name="Make it fit").click()
                pg.wait_for_selector("text=Hal made the type a little tighter.")
                self.assertEqual(mylooks.get(look_id).steps, 1)
                field = pg.get_by_label("Name of this look")
                field.fill("My own look")
                pg.get_by_role("button", name="Rename").click()
                pg.wait_for_selector("text=Renamed the look My own look.")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])
                pg.get_by_role("button", name="Delete the look My own look").click()
                pg.get_by_role("button", name="Delete", exact=True).click()
                pg.wait_for_selector("text=uses the look My own look")
                self.assertIsNotNone(mylooks.get(look_id), "a look in use is kept")
                self.assertEqual([e for e in p.errors if "409 (Conflict)" not in e], [], "only the refusal")
                store.set_look("classic")
                mylooks.delete(look_id)


if __name__ == "__main__":
    unittest.main()

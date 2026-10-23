"""The Look gallery and the CV preview in a real browser against the real server: each look is a live preview
of the made-up person's CV with Hal's own fonts, choosing one saves it, and nothing breaks the page's
policy, the axe check or the width.

The Type and look tab that holds the gallery is the My CV page's, so the test draws `LookPicker` into the app page
itself. It needs `HAL_TEST_BROWSER=1` and Playwright, as every browser test does (docs/dev/testing.md).
"""
from __future__ import annotations

import contextlib
import threading
import unittest

from support import people
from support.stubs import cv_contracts
from support.ui import AppPage, chromium, skip_unless_browser
from support.world import World
from test_cv_render import made_up_cv

cv_contracts.install()

from jobhunt.core.settings import store as settings_store  # noqa: E402 - after the stand-ins are installed
from jobhunt.cvstudio import store  # noqa: E402
from jobhunt.web import app, auth  # noqa: E402

MOUNT = """async () => {
  const { html, render } = await import('/app/core/html.js');
  const { LookPicker } = await import('/app/cv/look-picker.js');
  const box = document.createElement('section');
  box.id = 'look-test';
  box.setAttribute('aria-label', 'Look');
  document.querySelector('#main').append(box);
  render(html`<${LookPicker} />`, box);
}"""


@contextlib.contextmanager
def real_app(browser, size: str = "wide", scheme: str = "light"):
    """The new server inside the made-up world, and a signed-in tab on it."""
    settings_store.drop_cache()
    httpd = app.make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="ui-test-server", daemon=True).start()
    p = AppPage(browser, f"http://127.0.0.1:{port}", size, scheme)
    try:
        p.page.goto(auth.mint_open_url(port=port))
        yield p
    finally:
        p.close()
        httpd.shutdown()
        httpd.server_close()


@skip_unless_browser
class LookGallery(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.enterContext(World())
        self.person = self.enterContext(people.as_person(people.HUMANITIES))
        store.new_version(made_up_cv(self.person.key), origin="imported", what="Made-up CV", by="baseline")

    def test_each_look_previews_your_cv_and_choosing_one_saves_it(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                p.open("home")
                pg = p.page
                pg.evaluate(MOUNT)
                pg.wait_for_selector("#look-test iframe >> nth=3")
                for name in ("Classic", "Modern", "Compact", "Two columns"):
                    pg.get_by_role("button", name=name, exact=True).wait_for()
                words = pg.inner_text("#look-test")
                self.assertEqual(words.count("Safe for job sites"), 3)
                self.assertEqual(words.count("May confuse job sites"), 1)
                for look in ("classic", "modern", "compact", "two"):
                    frame = next(f for f in pg.frames if f"look={look}" in f.url)
                    frame.wait_for_selector("h1.cv-name")
                    self.assertEqual(frame.inner_text("h1.cv-name"), "Marnie Quillfeather")
                    loaded = frame.evaluate("async () => { await document.fonts.ready;"
                                            " return document.fonts.check('12px \"Source Sans 3\"'); }")
                    self.assertTrue(loaded, look)
                pg.get_by_role("button", name="Compact", exact=True).click()
                pg.wait_for_selector("#look-test button[aria-pressed=true] >> text=Compact")
                self.assertEqual(store.doc()["look_id"], "compact")
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])
                pg.get_by_role("button", name="Classic", exact=True).click()
                pg.wait_for_selector("#look-test button[aria-pressed=true] >> text=Classic")


if __name__ == "__main__":
    unittest.main()

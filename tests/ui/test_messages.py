"""Messages in a real browser against the real server and the made-up world: opening a message marks it read,
the filters, Archive and Mark all read.

They need `HAL_TEST_BROWSER=1` and Playwright, as every browser test does (docs/dev/testing.md).
"""
from __future__ import annotations

import contextlib
import threading
import unittest

from support.ui import AppPage, chromium, skip_unless_browser
from support.world import World

from jobhunt.core.settings import store
from jobhunt.web import app, auth

SUBJECT = "Quick question about your application"


@contextlib.contextmanager
def real_app(browser, size: str = "wide", scheme: str = "light"):
    """The new server inside the made-up world, and a signed-in tab on it."""
    store.drop_cache()
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
class MessagesPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.w = self.enterContext(World())

    def unread(self) -> int:
        return self.w.con.execute("SELECT count(*) FROM emails WHERE read = 0").fetchone()[0]

    def test_opening_a_message_marks_it_read_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                self.w.con.execute("UPDATE emails SET read = 0, archived = 0")
                self.w.con.commit()
                p.open("inbox")
                pg = p.page
                pg.get_by_role("button", name="Open " + SUBJECT).wait_for()
                before = self.unread()
                pg.get_by_role("button", name="Open " + SUBJECT).click()
                pg.locator(".li.msg", has_text=SUBJECT).locator("b[style*='font-weight']").wait_for()
                self.assertEqual(self.unread(), before - 1)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_filters_archive_and_mark_all_read(self) -> None:
        with real_app(self.browser) as p:
            p.open("inbox")
            pg = p.page
            pg.get_by_role("button", name="From Hal").click()
            pg.get_by_text("Nothing here.").wait_for()
            pg.get_by_role("button", name="From employers").click()
            row = pg.locator(".li.msg", has_text=SUBJECT)
            row.wait_for()
            row.get_by_role("button", name="Archive").click()
            row.wait_for(state="detached")
            archived = self.w.con.execute("SELECT archived FROM emails WHERE subject = ?", (SUBJECT,)).fetchone()[0]
            self.assertEqual(archived, 1)
            pg.get_by_role("button", name="Mark all read").click()
            pg.get_by_text("All marked read").wait_for()
            self.assertEqual(self.unread(), 0)
            self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

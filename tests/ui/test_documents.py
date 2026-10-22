"""Documents in a real browser against the real server and the made-up world: one application's files, Copy
all, Download all, the folder's path and the copy-paste details.

They need `HAL_TEST_BROWSER=1` and Playwright, as every browser test does (docs/dev/testing.md).
"""
from __future__ import annotations

import contextlib
import threading
import unittest

from support.ui import AppPage, chromium, skip_unless_browser
from support.world import World, job_id

from jobhunt.core import settings
from jobhunt.core.settings import store
from jobhunt.docs import folders
from jobhunt.web import app, auth

DOCS = {"letter": "Dear team, a made-up letter for a made-up job.", "message": "A short made-up note.",
        "answers": [{"question": "Why us?", "answer": "A made-up answer."},
                    {"question": "When can you start?", "answer": "Next month."}]}


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


def fake_render(out):
    """Two made-up PDFs, as the CV and letter makers would leave them."""
    (out / "cv.pdf").write_bytes(b"%PDF-1.4 cv\n%%EOF\n")
    (out / "letter.pdf").write_bytes(b"%PDF-1.4 letter\n%%EOF\n")
    return {"cv": str(out / "cv.pdf"), "letter": str(out / "letter.pdf")}


@skip_unless_browser
class DocumentsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.w = self.enterContext(World())
        settings.set("you.name", "Robin Testwell")
        settings.set("you.email", "robin.testwell@example.org")
        con = self.w.con
        con.execute("UPDATE jobs SET status = 'applying' WHERE id = ?", (job_id("sorrel-ready"),))
        job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id("sorrel-ready"),)).fetchone()
        self.folder = folders.publish(con, job, DOCS, render=fake_render, approved_at="2026-10-22T10:44:00Z")
        con.commit()
        self.number = folders.number_for(con, job["id"], create=False)

    def test_one_applications_files_show_with_copy_and_download_all(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                p.ctx.grant_permissions(["clipboard-read", "clipboard-write"])
                p.open(f"docs-{self.number}")
                pg = p.page
                card = pg.locator("section.card", has_text="You approved these")
                card.wait_for()
                words = card.inner_text()
                for name in ("CV (PDF)", "Cover letter (PDF)", "Answers to the form's questions",
                             "Message to the hiring manager", "The advert, saved"):
                    self.assertIn(name, words)
                self.assertIn("2 answers", words)
                self.assertNotIn("pack.md", words)
                self.assertIn(self.folder.name, words)
                zip_link = card.get_by_role("link", name="Download all")
                self.assertEqual(zip_link.get_attribute("href"), f"/api/v2/documents/{self.number}/zip")
                card.get_by_role("button", name="Copy all").click()
                pg.wait_for_selector(".toast")
                pasted = pg.evaluate("() => navigator.clipboard.readText()")
                self.assertIn("**Why us?**", pasted)
                self.assertIn("A made-up answer.", pasted)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_copy_paste_details_list_your_profile(self) -> None:
        with real_app(self.browser) as p:
            p.open("docs-details")
            pg = p.page
            pg.get_by_role("button", name="Copy Email").wait_for()
            words = pg.inner_text("#main")
            self.assertIn("Robin Testwell", words)
            self.assertIn("robin.testwell@example.org", words)
            self.assertEqual(p.serious_axe(), [])
            self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()

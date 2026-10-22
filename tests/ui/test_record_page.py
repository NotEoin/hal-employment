"""The Your record page in a real browser, against a real server in a temporary home with a made-up record.

Needs `HAL_TEST_BROWSER=1` and Playwright. The AI is the fake one.
"""
from __future__ import annotations

import contextlib
import unittest
from pathlib import Path
from unittest import mock

from support.api import HalServer
from support.fake_ai import fake_ai
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import record
from jobhunt.core import paths
from jobhunt.evidence import routes

ANSWER = "On 5 March 2025 [Certificates/First Aid.md]."
# The shell asks for Home's summary, which the scheduler serves and this test does not start; its
# 404 is that part's, not this page's.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"


@skip_unless_browser
class RecordPage(unittest.TestCase):
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
        self.enterContext(mock.patch.object(record, "VAULT", Path(paths.record_default()), create=True))
        self.addCleanup(record._CACHE.clear)
        folder = routes.make_default()
        (folder / "Certificates" / "First Aid.md").write_text(
            "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n", encoding="utf-8")
        (folder / "Private" / "Health.md").write_text("# Health\n\nA private note.\n", encoding="utf-8")
        record.sync()

    def page(self, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open("record")
        return p

    def test_the_page_shows_the_groups_and_draws_cleanly_at_both_sizes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                p.page.wait_for_selector("text=What is in it")
                self.assertEqual(p.page.inner_text("#main h1"), "Your record")
                body = p.page.inner_text("#main")
                for words in ("Certificates", "Private", "Drop files here to add them",
                              "Private files you mark as private are never sent to the AI."):
                    self.assertIn(words, body)
                self.assertEqual(self.errors(p), [])
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])

    def test_asking_shows_the_answer_and_the_files_it_came_from(self) -> None:
        p = self.page()
        with fake_ai({"record_ask": ANSWER}):
            p.page.fill("input[aria-label=Question]", "When did I do first aid at work?")
            p.page.click("button:has-text('Ask')")
            p.page.wait_for_selector(".answer")
        self.assertIn("5 March 2025", p.page.inner_text(".answer"))
        self.assertIn("From: Certificates/First Aid.md", p.page.inner_text("#main"))
        self.assertEqual(self.errors(p), [])

    def test_a_dropped_file_is_added_to_the_inbox(self) -> None:
        p = self.page()
        upload = Path(paths.cache_dir()) / "Reference.txt"
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_text("A made-up reference from a made-up archive.", encoding="utf-8")
        p.page.set_input_files("input[type=file]", str(upload))
        p.page.wait_for_selector("text=Added Reference.txt")
        self.assertTrue((Path(record._live("VAULT")) / "Inbox" / "Reference.txt").exists())

    def test_a_group_opens_to_its_files_with_downloads(self) -> None:
        p = self.page()
        p.page.click("button:has-text('Certificates')")
        link = p.page.wait_for_selector("a:has-text('Download')")
        with p.page.expect_download() as got:
            link.click()
        self.assertEqual(got.value.suggested_filename, "First Aid.md")


"""The setup guide in a real browser: a made-up humanities graduate goes from Welcome to All set
at both sizes, against a real server in a temporary home, with fakes for the AI, Gmail, ntfy and the first
search.

Needs `HAL_TEST_BROWSER=1` and Playwright. Every step is checked: it draws
with no console error, the guide never scrolls sideways, and axe finds nothing serious. Steps other areas
own (goal, AI, helper, updates) are drawn by whatever those areas ship; the walk only presses the guide's
own buttons on them.
"""
from __future__ import annotations

import contextlib
import imaplib
import unittest
from unittest import mock

from support.api import HalServer
from support.fake_ai import fake_ai
from support.make_cv_fixtures import FOLDER, NAMES
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt.ai.base import LLMUnavailable
from jobhunt.setup import email_test, phone

GOOD_PASSWORD = "qrst uvwx yzab cdef"   # made up
ADDRESS = "marnie.quillfeather@example.com"
STEPS = ["welcome", "cv", "about", "goal", "record", "ai", "helper", "email", "phone", "updates", "done"]
FAILED_LOAD = "Failed to load resource"
MIN_LABEL_PX = 150                      # room for "Kinds of role" on one line


class FakeGmail:
    """Accepts only the made-up app password."""

    def __init__(self, host: str, timeout: float) -> None:
        self.host = host

    def login(self, address: str, password: str) -> None:
        if password != GOOD_PASSWORD.replace(" ", ""):
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    def logout(self) -> None:
        """Signs out."""


@skip_unless_browser
class SetupGuide(unittest.TestCase):
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
        self.enterContext(mock.patch.object(email_test, "IMAP", FakeGmail))
        self.sent = []
        self.enterContext(mock.patch.object(phone, "URLOPEN", lambda req, timeout: self.sent.append(req)
                                            or contextlib.nullcontext()))
        self.first_search = self.enterContext(mock.patch("jobhunt.setup.routes.start_first_search",
                                                         return_value=None))

    def tab(self, size: str, scheme: str) -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        return p

    def check(self, p: AppPage, where: str) -> None:
        """The checks of mock/check.py and gate G2 on the guide as it is now."""
        over = p.page.evaluate("(() => { const b = document.querySelector('.sbody');"
                               " return b ? b.scrollWidth - b.clientWidth : 0; })()")
        self.assertLessEqual(over, 1, f"{where}: the guide scrolls sideways")
        self.assertEqual(p.sideways(), 0, where)
        self.assertEqual(p.serious_axe(), [], where)
        errors = [e for e in p.errors if not e.startswith(FAILED_LOAD)] + p.missing
        self.assertEqual(errors, [], where)

    def at(self, p: AppPage, step: str) -> None:
        p.page.wait_for_selector(f".sinner[data-step='{step}'] :is(h1, h2)")
        p.page.wait_for_selector(".sinner .loading", state="detached")

    def primary(self, p: AppPage) -> None:
        p.page.click(".sfoot .btn.primary")

    def walk(self, size: str, scheme: str) -> None:
        p = self.tab(size, scheme)
        p.page.goto(f"http://127.0.0.1:{self.hal.port}/#setup")
        self.at(p, "welcome")
        self.assertIn("Hi, I'm Hal.", p.page.inner_text(".sinner"))
        self.assertIn("Closing this window does not stop Hal.", p.page.inner_text(".sinner"))
        self.check(p, f"{size} welcome")
        self.primary(p)

        self.at(p, "cv")
        if not p.page.query_selector("[data-cv-read]"):
            p.page.set_input_files("input[aria-label='Choose your CV']", str(FOLDER / f"{NAMES['humanities']}.pdf"))
        p.page.wait_for_selector("[data-cv-read]")
        self.check(p, f"{size} cv")
        self.primary(p)

        self.at(p, "about")
        p.page.wait_for_selector(".sinner .chip.why")
        self.assertEqual(p.page.input_value("#about-you-name"), "Marnie Quillfeather")
        p.page.fill("#about-goal-min_pay", "22,000")
        p.page.wait_for_selector("text=Hal reads this as 22,000 pounds a year")
        p.page.select_option("#about-you-right_to_work", "uk_visa")
        self.check(p, f"{size} about")
        self.primary(p)

        for step in ("goal", ):
            self.at(p, step)
            self.check(p, f"{size} {step}")
            self.primary(p)

        self.at(p, "record")
        p.page.wait_for_selector("[data-folder]")
        self.check(p, f"{size} record")
        self.primary(p)

        for step in ("ai", "helper"):
            self.at(p, step)
            self.check(p, f"{size} {step}")
            self.primary(p)

        self.at(p, "email")
        p.page.fill("#email-address", ADDRESS)
        p.page.fill("input[aria-label='App password']", "aaaa bbbb cccc dddd")
        p.page.click(".sinner button:has-text('Test')")
        p.page.wait_for_selector("text=did not accept that app password")
        p.page.fill("input[aria-label='App password']", GOOD_PASSWORD)
        p.page.click(".sinner button:has-text('Test')")
        p.page.wait_for_selector("text=Connected. Hal can read your Gmail (read only).")
        self.check(p, f"{size} email")
        self.primary(p)

        self.at(p, "phone")
        p.page.wait_for_selector("img.qr-img")
        # Windows 11 install test: the picture loaded but drew nothing, as its SVG had no namespace
        self.assertGreater(p.page.evaluate("document.querySelector('img.qr-img').naturalWidth"), 0)
        p.page.click(".sinner button:has-text('Send a test')")
        p.page.wait_for_selector("text=Sent. Did your phone buzz?")
        self.check(p, f"{size} phone")
        self.primary(p)

        self.at(p, "updates")
        self.check(p, f"{size} updates")
        p.page.click(".sfoot >> text=Skip for now")

        self.at(p, "done")
        text = p.page.inner_text(".sinner")
        self.assertIn("You're set up", text)
        self.assertIn("Skipped: it is on Home when you want it", text)
        self.check(p, f"{size} done")
        p.page.click(".sfoot >> text=Go to Home")
        p.page.wait_for_selector(".setup", state="detached")
        p.page.wait_for_selector("#setup-h")
        self.assertIn("Updates", p.page.inner_text("section[aria-labelledby='setup-h']"))
        self.assertEqual(self.sent[-1].full_url.startswith(phone.NTFY), True)
        self.first_search.assert_called_once()

    def test_a_humanities_graduate_goes_from_welcome_to_all_set_on_a_wide_screen(self) -> None:
        self.walk("wide", "light")

    def test_a_humanities_graduate_goes_from_welcome_to_all_set_on_a_phone_sized_screen(self) -> None:
        self.walk("narrow", "dark")

    def test_save_and_finish_later_closes_the_guide_and_opens_it_where_it_was(self) -> None:
        p = self.tab("wide", "light")
        p.page.goto(f"http://127.0.0.1:{self.hal.port}/#setup")
        self.at(p, "welcome")
        self.primary(p)
        self.at(p, "cv")
        p.page.click("aside >> text=Save and finish later")
        p.page.wait_for_selector(".setup", state="detached")
        p.page.wait_for_selector("#setup-h")
        p.page.click("section[aria-labelledby='setup-h'] button:has-text('Set up') >> nth=0")
        self.at(p, "cv")
        p.page.keyboard.press("Escape")
        p.page.wait_for_selector(".setup", state="detached")
        p.open("settings")
        p.page.goto(f"http://127.0.0.1:{self.hal.port}/#setup")
        self.at(p, "cv")
        self.check(p, "resumed")

    def test_labels_keep_room_for_their_words_in_the_laptop_window(self) -> None:
        # Windows 11 install test (2026-10-23): in the 1033-pixel app window the rows of choices squeezed "Kinds of
        # role" and "Start from a career area" into a column one word wide
        p = self.tab("laptop", "light")
        p.page.goto(f"http://127.0.0.1:{self.hal.port}/#setup-goal")
        self.at(p, "goal")
        widths = p.page.evaluate("[...document.querySelectorAll('.sinner .setting')].map(r => [r.querySelector('.lab')"
                                 ".textContent, r.firstElementChild.getBoundingClientRect().width])")
        narrow = [label for label, width in widths if width < MIN_LABEL_PX]
        self.assertEqual(narrow, [], widths)
        self.check(p, "laptop goal")

    def test_every_step_has_a_heading_and_the_list_names_all_eleven(self) -> None:
        p = self.tab("wide", "light")
        p.page.goto(f"http://127.0.0.1:{self.hal.port}/#setup")
        self.at(p, "welcome")
        names = p.page.locator("aside .sstep").all_inner_texts()
        self.assertEqual(len(names), len(STEPS))
        self.assertTrue(names[0].startswith("1"))
        for i, step in enumerate(STEPS):
            p.page.click(f"aside .sstep >> nth={i}")
            self.at(p, step)


if __name__ == "__main__":
    unittest.main()

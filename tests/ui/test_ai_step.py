"""The setup guide's "How should Hal think?" step and Settings, Hal's brain, in a real browser against a real
server, with the made-up Claude command line, a made-up installer and no window ever opened.

Needs `HAL_TEST_BROWSER=1` and Playwright. The setup guide's frame is still being built, so the step is
drawn on its own over the app with the app's own modules, as `test_check_read` draws the CV step.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import fake_claude
from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser
from test_claude_setup import ConsoleStandIn, FakeInstaller, _without_real_claude

from jobhunt import ai
from jobhunt.ai import claude_setup
from jobhunt.ai.base import ChatResult
from jobhunt.ai.claude_cli import ClaudeCLIBackend
from jobhunt.core import platform, settings

STEP_MS = 20000      # an install or sign-in with the made-up programs takes a second or two
MACHINE = {"memory_gb": 8, "gpu": None, "cpus": 4}
MOUNT_STEP = """async () => {
  const [{ html, render }, { Step }] = await Promise.all([import("/app/core/html.js"),
                                                         import("/app/setup/steps/ai.js")]);
  const host = document.createElement("div");
  host.className = "card";
  host.id = "test-step";
  document.querySelector("#main").before(host);
  render(html`<${Step} />`, host);
}"""


@skip_unless_browser
class AiStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="hal-ai-step-"))).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"HOME": str(self.tmp), "USERPROFILE": str(self.tmp),
                                                       "PATH": _without_real_claude(os.environ["PATH"])}))
        for name in ("FAKE_CLAUDE_MODE", "FAKE_CLAUDE_STATE", "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_PLAN"):
            os.environ.pop(name, None)
        claude_setup.forget()
        self.addCleanup(claude_setup.forget)
        self.enterContext(mock.patch.object(platform.current(), "machine", lambda: MACHINE))
        self.enterContext(mock.patch.object(claude_setup, "has_battery", lambda: True))
        self.enterContext(mock.patch.object(claude_setup, "POLL_S", 0.1))
        self.console = ConsoleStandIn()
        self.addCleanup(self.console.close)
        self.enterContext(mock.patch.object(claude_setup, "_open_console", self.console))
        self.enterContext(mock.patch.object(claude_setup, "_popen", FakeInstaller(self.tmp)))
        self.enterContext(mock.patch.object(claude_setup, "_windows", lambda: False))
        self.hal = self.enterContext(HalServer())
        self.enterContext(ai.use(ClaudeCLIBackend()))
        settings.set("ai.provider", "claude_cli")

    def page(self, size: str = "wide", scheme: str = "light", **session) -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url(**session))
        p.open("home")
        return p

    def step(self, size: str = "wide", scheme: str = "light", **session) -> AppPage:
        p = self.page(size, scheme, **session)
        p.page.evaluate(MOUNT_STEP)
        p.page.wait_for_selector("#test-step [data-ai-steps]")
        return p

    def row(self, p: AppPage, rid: str, state: str) -> str:
        found = p.page.wait_for_selector(f"#test-step [data-ai-row={rid}][data-state={state}]", timeout=STEP_MS)
        return found.inner_text()

    def press(self, p: AppPage, rid: str, words: str) -> None:
        p.page.click(f"#test-step [data-ai-row={rid}] button:has-text('{words}')")

    def clean(self, p: AppPage) -> None:
        self.assertEqual(p.errors, [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def test_a_made_up_friend_installs_signs_in_and_tests_claude(self):
        p = self.step()
        text = p.page.inner_text("#test-step")
        self.assertIn("How should Hal think?", text)
        self.assertIn("Best for this laptop", text)
        self.assertIn("Too slow here", text)
        self.assertIn("This laptop has 8 GB of memory and no suitable graphics card.", text)
        self.assertEqual(p.page.get_attribute("#test-step [data-provider=claude_cli]", "aria-pressed"), "true")
        self.clean(p)
        self.press(p, "install", "Install")
        self.assertIn("Installed", self.row(p, "install", "done"))
        os.environ["FAKE_CLAUDE_LOGIN_S"] = "0.3"
        self.assertIn("paste it into that small window", p.page.inner_text("#test-step"))
        self.press(p, "sign", "Sign in")
        self.assertIn("Signed in as robin.example@example.com (Claude Pro)", self.row(p, "sign", "done"))
        self.assertEqual([title for _, title in self.console.opened], ["Claude sign-in"])
        self.press(p, "test", "Test")
        self.assertRegex(self.row(p, "test", "done"), r"Claude answered in \d+\.\d seconds?")
        self.assertIn("It never uses more than 30 background checks a day", p.page.inner_text("#test-step"))
        self.clean(p)

    def test_a_free_plan_is_explained_on_a_narrow_dark_screen(self):
        self.enterContext(fake_claude.on_path(self.tmp, FAKE_CLAUDE_PLAN="free"))
        p = self.step("narrow", "dark")
        note = p.page.wait_for_selector("#test-step [data-free-plan]").inner_text()
        self.assertIn("Your Claude plan cannot run Claude Code. It needs Claude Pro or Max.", note)
        self.assertEqual(p.page.query_selector_all("#test-step [data-ai-row=sign] button"), [])
        self.clean(p)

    def test_a_window_from_another_computer_is_told_where_to_sign_in(self):
        self.enterContext(fake_claude.on_path(self.tmp, signed_in=False))
        p = self.step(remote=True, remote_host="desktop")
        words = p.page.wait_for_selector("#test-step [data-remote-sign-in]").inner_text()
        self.assertIn("Sign in on desktop, the computer Hal runs on.", words)
        self.assertEqual(p.page.query_selector_all("#test-step [data-ai-row=sign] button"), [])
        self.assertEqual(self.console.opened, [])
        self.clean(p)

    def test_settings_shows_the_account_and_the_allowance_in_counts(self):
        self.enterContext(fake_claude.on_path(self.tmp, FAKE_CLAUDE_PLAN="max"))
        from jobhunt.ai import budget

        for n in range(3):
            budget.record(ChatResult(text="ready", provider="claude_cli", model="haiku", ms=800),
                          priority=2 if n else 0, purpose="screen", chars_in=10, chars_out=5)
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page(size, scheme)
                p.go("settings-ai")
                panel = p.page.wait_for_selector("[data-ai-panel] [data-allowance]")
                self.assertEqual(panel.inner_text(),
                                 "Hal used Claude 3 times today. 2 were background checks (your limit is 30).")
                self.assertEqual(p.page.inner_text("[data-ai-panel] [data-account]"),
                                 "Signed in as robin.example@example.com (Claude Max)")
                self.assertTrue(p.page.is_visible("[data-ai-panel] button:has-text('Sign out')"))
                # the empty catalogue rows drew above the panel's, so each label showed twice
                for label in ("Claude account", "Your Claude allowance"):
                    self.assertEqual(p.page.locator(f"[data-group=ai] .lab:text-is('{label}')").count(), 1, label)
                self.clean(p)


if __name__ == "__main__":
    unittest.main()

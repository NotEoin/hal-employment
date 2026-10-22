"""The Claude subscription backend, driven through the made-up command line in `tests/support/fake_claude/`."""
import ast
import contextlib
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from support import fake_claude

from jobhunt import ai
from jobhunt.ai import claude_cli
from jobhunt.ai.base import ChatRequest, LLMUnavailable
from jobhunt.ai.claude_cli import ClaudeCLIBackend
from jobhunt.core import clock, paths
from jobhunt.core.settings import store

AI_FOLDER = Path(ai.__file__).resolve().parent
BANNED = ("command prompt", "terminal", "run:")
LONG_SYSTEM = "You write cover letters for a made-up applicant. " * 1500     # far past a Windows command line


class InHome(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-claude-")))
        stack.enter_context(paths.set_override(self.tmp))
        stack.enter_context(mock.patch.dict(os.environ, {clock.ENV_FAKE_NOW: "2026-10-22T10:00:00Z"}))
        store.drop_cache()
        stack.callback(store.drop_cache)
        self.stack = stack

    def fake(self, mode: str = "ok", **env):
        return self.stack.enter_context(fake_claude.on_path(self.tmp, mode=mode, **env))

    def ask(self, system: str = "Reply with the single word ready.", user: str = "A made-up advert.", **kw):
        return ClaudeCLIBackend().chat(ChatRequest(system=system, user=user, **kw), "haiku")


class Calls(InHome):
    def test_claude_runs_with_tools_off_and_the_prompt_on_stdin(self):
        fake = self.fake(FAKE_CLAUDE_REPLY="Dear made-up team")
        out = self.ask(system=LONG_SYSTEM, user="Write to the made-up team at Heron Freight.")
        self.assertEqual((out.text, out.provider, out.model), ("Dear made-up team", "claude_cli", "haiku"))
        call = fake.calls()[0]
        argv = call["argv"]
        self.assertEqual(argv[:5], ["-p", "--output-format", "json", "--model", "haiku"])
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(call["stdin"], "Write to the made-up team at Heron Freight.")
        self.assertNotIn("Heron Freight", " ".join(argv))
        self.assertNotIn("cover letters", " ".join(argv))
        self.assertEqual(Path(call["cwd"]).resolve(), paths.claude_work_dir().resolve())

    def test_the_system_prompt_goes_in_a_file_that_is_deleted_after_the_call(self):
        fake = self.fake()
        self.ask(system=LONG_SYSTEM)
        os.environ["FAKE_CLAUDE_MODE"] = "signed_out"
        os.unlink(fake.state)
        with self.assertRaises(LLMUnavailable):
            self.ask(system="A second made-up system prompt.")
        first, second = fake.calls()
        self.assertTrue(first["system_file_existed"] and second["system_file_existed"])
        self.assertEqual(first["system"], LONG_SYSTEM)
        self.assertEqual(second["system"], "A second made-up system prompt.")
        self.assertNotEqual(first["system_file"], second["system_file"])
        for call in (first, second):
            self.assertEqual(Path(call["system_file"]).parent.resolve(), paths.claude_work_dir().resolve())
            self.assertFalse(Path(call["system_file"]).exists())
        self.assertEqual(list(paths.claude_work_dir().iterdir()), [])

    def test_each_failure_is_said_in_plain_words(self):
        cases = {"signed_out": ("signed_out", "Hal is not signed in to Claude. Sign in again."),
                 "free_plan": ("free_plan", "Your Claude plan cannot run Claude Code. It needs Claude Pro or Max."),
                 "limited": ("limit", "Claude limit reached. Hal is still finding jobs; writing starts again when the "
                                      "limit resets."),
                 "garbage": ("unavailable", "Claude sent an answer Hal could not read.")}
        for mode, (kind, words) in cases.items():
            with self.subTest(mode), fake_claude.on_path(self.tmp, mode=mode, signed_in=False):
                with self.assertRaises(LLMUnavailable) as caught:
                    self.ask()
                self.assertEqual((caught.exception.kind, str(caught.exception)), (kind, words))

    def test_a_slow_answer_times_out_in_words(self):
        self.fake("slow", FAKE_CLAUDE_SLOW_S="5")
        with self.assertRaises(LLMUnavailable) as caught:
            self.ask(timeout=0.5)
        self.assertEqual((caught.exception.kind, str(caught.exception)),
                         ("timeout", "Claude did not answer within 1 second."))
        self.assertEqual(claude_cli._timeout_words(300), "Claude did not answer within 5 minutes.")

    def test_no_claude_means_not_installed(self):
        with mock.patch.object(claude_cli, "executable", return_value=None):
            with self.assertRaises(LLMUnavailable) as caught:
                self.ask()
            self.assertEqual(caught.exception.kind, "not_installed")
            self.assertEqual(ClaudeCLIBackend().status().fix, "install_claude")

    def test_a_test_never_runs_a_real_claude(self):
        real = self.tmp / "bin" / "claude"
        real.parent.mkdir()
        real.write_text("#!/bin/sh\necho should never run\n")
        real.chmod(0o755)
        with mock.patch.object(claude_cli, "executable", return_value=str(real)), \
                mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "ok"}):
            os.environ.pop(claude_cli.REAL_AI_ENV, None)
            with self.assertRaises(LLMUnavailable) as caught:
                self.ask()
        self.assertEqual(str(caught.exception), "Tests may run only the fake Claude command line.")

    def test_windows_gets_git_bash_from_the_install_record(self):
        bash = self.tmp / "tools" / "git" / "bin" / "bash.exe"
        bash.parent.mkdir(parents=True)
        bash.touch()
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        (paths.state_dir() / "install.json").write_text(
            '{"kind": "user", "git": "' + str(self.tmp / "tools" / "git" / "cmd" / "git.exe").replace("\\", "/") + '"}')
        self.assertEqual(Path(claude_cli._git_bash()), bash)
        with mock.patch.object(claude_cli.sys, "platform", "win32"), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(claude_cli.GIT_BASH_ENV, None)
            self.assertEqual(Path(ClaudeCLIBackend()._env()[claude_cli.GIT_BASH_ENV]), bash)


class Words(unittest.TestCase):
    def test_limit_reset_times_are_read_in_uk_time(self):
        now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)               # 11:00 in London
        self.assertEqual(claude_cli.reset_time("You've hit your limit - resets 3pm (Europe/London)", now),
                         datetime(2026, 10, 22, 14, 0, tzinfo=UTC))
        self.assertEqual(claude_cli.reset_time("usage limit; reset at 15:40", now),
                         datetime(2026, 10, 22, 14, 40, tzinfo=UTC))
        self.assertEqual(claude_cli.reset_time("limit reached, resets 9am", now),
                         datetime(2026, 10, 23, 8, 0, tzinfo=UTC))      # 9am has passed today: tomorrow
        self.assertIsNone(claude_cli.reset_time("Claude AI usage limit reached.", now))

    def test_no_message_says_command_prompt(self):
        found = []
        for file in sorted(AI_FOLDER.glob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                          and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    found += [f"{file.name}: {node.value!r}" for word in BANNED if word in node.value.lower()]
        self.assertEqual(found, [])


class RealClaude(InHome):
    @unittest.skipUnless(os.environ.get("HAL_TEST_REAL_AI") == "claude", "set HAL_TEST_REAL_AI=claude to call Claude")
    def test_real_claude_answers_a_made_up_prompt(self):
        """One real call with made-up text only; never in CI."""
        with ai.use(ClaudeCLIBackend()):
            out = ai.chat("Reply with the single word ready.", "This is a made-up check.", role="quick", priority=0)
        self.assertIn("ready", out.lower())


if __name__ == "__main__":
    unittest.main()

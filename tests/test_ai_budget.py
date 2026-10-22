"""The allowance: priorities, the daily cap, keep-free hours, Claude's limit and the never-online switch."""
import contextlib
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from support import fake_claude
from support.fake_ai import FakeBackend

from jobhunt import ai
from jobhunt.ai import budget
from jobhunt.ai.base import Limited, LLMUnavailable
from jobhunt.ai.claude_cli import ClaudeCLIBackend
from jobhunt.ai.openai_compat import OpenAICompatBackend
from jobhunt.core import clock, paths, settings
from jobhunt.core.settings import store

MORNING = "2026-10-22T10:00:00Z"        # 11:00 in London (summer time until 25 October)


def claude_like() -> FakeBackend:
    """A made-up AI that counts as the Claude subscription."""
    fake = FakeBackend(default="ok")
    fake.name, fake.online = "claude_cli", True
    return fake


class InHome(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-budget-")))
        stack.enter_context(paths.set_override(self.tmp))
        stack.enter_context(mock.patch.dict(os.environ, {clock.ENV_FAKE_NOW: MORNING}))
        store.drop_cache()
        stack.callback(store.drop_cache)

    def at(self, when: str) -> None:
        os.environ[clock.ENV_FAKE_NOW] = when


class Allowance(InHome):
    def test_background_checks_stop_at_the_daily_cap(self):
        settings.set("ai.daily_cap", 2)
        with ai.use(claude_like()) as fake:
            ai.chat("s", "u", priority=2)
            ai.chat("s", "u", priority=2)
            with self.assertRaises(LLMUnavailable) as caught:
                ai.chat("s", "u", priority=2, purpose="screen")
            self.assertEqual((caught.exception.kind, str(caught.exception)),
                             ("cap", "Hal has used today's 2 background checks. It carries on tomorrow."))
            self.assertEqual(ai.chat("s", "u", priority=0), "ok")
            self.assertEqual(ai.ready(2), "Hal has used today's 2 background checks. It carries on tomorrow.")
        self.assertEqual(len(fake.log), 3)
        self.assertEqual(budget.words(), "Hal used Claude 3 times today. 2 were background checks (your limit is 2).")
        self.at("2026-10-22T23:30:00Z")             # half past midnight in London: a new day
        with ai.use(claude_like()):
            self.assertEqual(ai.chat("s", "u", priority=2), "ok")

    def test_the_cap_does_not_apply_to_a_local_model(self):
        settings.set("ai.daily_cap", 0)
        with ai.use(FakeBackend(default="ok")):
            self.assertEqual(ai.chat("s", "u", priority=2), "ok")

    def test_keep_free_hours_hold_back_only_background_work(self):
        settings.set("ai.keep_free", "09:00 to 17:00")
        with ai.use(claude_like()) as fake:
            with self.assertRaises(LLMUnavailable) as caught:
                ai.chat("s", "u", priority=2)
            self.assertEqual(caught.exception.kind, "keep_free")
            self.assertEqual(ai.chat("s", "u", priority=1), "ok")
            self.assertEqual(ai.chat("s", "u", priority=0), "ok")
            self.at("2026-10-22T17:30:00Z")         # 18:30 in London
            self.assertEqual(ai.chat("s", "u", priority=2), "ok")
        self.assertEqual([r.priority for r in fake.log], [1, 0, 2])

    def test_keep_free_hours_can_run_past_midnight(self):
        settings.set("ai.keep_free", "22:00 to 07:30")
        self.at("2026-10-22T23:00:00Z")
        with ai.use(claude_like()), self.assertRaises(LLMUnavailable):
            ai.chat("s", "u", priority=2)

    def test_never_use_online_ai_refuses_claude(self):
        settings.set("ai.local_only", True)
        with fake_claude.on_path(self.tmp) as fake:
            with ai.use(ClaudeCLIBackend()), self.assertRaises(LLMUnavailable) as caught:
                ai.chat("s", "u", priority=0)
            self.assertEqual((caught.exception.kind, str(caught.exception)),
                             ("local_only", "Hal is set to never use online AI."))
            self.assertEqual(fake.calls(), [])
        with ai.use(OpenAICompatBackend("https://models.example.com/v1")), self.assertRaises(LLMUnavailable):
            ai.chat("s", "u", priority=0)
        with ai.use(FakeBackend(default="ok")):
            self.assertEqual(ai.chat("s", "u", priority=0), "ok")

    def test_lower_priority_numbers_go_first_when_calls_queue(self):
        order, turns = [], ai._Turns()

        def call(priority):
            with turns.turn(priority, 1, 5):
                order.append(priority)

        with turns.turn(2, 1, 5):
            threads = []
            for priority in (2, 1, 0):
                threads.append(threading.Thread(target=call, args=(priority,)))
                threads[-1].start()
                time.sleep(0.05)            # each waiter is queued before the next arrives
        for t in threads:
            t.join(5)
        self.assertEqual(order, [0, 1, 2])


class ClaudeLimit(InHome):
    def test_a_usage_limit_pauses_background_work_until_the_reset_time(self):
        with fake_claude.on_path(self.tmp, mode="limited_at") as fake, ai.use(ClaudeCLIBackend()):
            with self.assertRaises(Limited) as caught:
                ai.chat("s", "u", priority=1)
            self.assertEqual(str(caught.exception),
                             "Claude limit reached. Hal is still finding jobs; writing starts again at 15:00.")
            self.assertEqual(clock.iso(caught.exception.until), "2026-10-22T14:00:00Z")
            os.environ["FAKE_CLAUDE_MODE"] = "ok"
            with self.assertRaises(Limited):
                ai.chat("s", "u", priority=2)
            self.assertEqual(len(fake.calls()), 1)            # the background call was never sent
            self.assertIn("paused until 15:00", budget.words())
            self.at("2026-10-22T14:01:00Z")
            self.assertEqual(ai.chat("s", "u", priority=2), "ready")
        self.assertEqual(len(fake.calls()), 2)

    def test_you_can_still_ask_during_a_limit_and_a_success_lifts_it(self):
        with fake_claude.on_path(self.tmp, mode="limited") as fake, ai.use(ClaudeCLIBackend()):
            with self.assertRaises(Limited) as caught:
                ai.chat("s", "u", priority=0)
            self.assertIsNone(caught.exception.until)                # no time named: an hour from now
            self.assertEqual(clock.iso(budget.limited_until()), "2026-10-22T11:00:00Z")
            os.environ["FAKE_CLAUDE_MODE"] = "ok"
            self.assertEqual(ai.chat("s", "u", priority=0), "ready")
            self.assertIsNone(budget.limited_until())
            self.assertEqual(ai.chat("s", "u", priority=2), "ready")
        self.assertEqual(len(fake.calls()), 3)


if __name__ == "__main__":
    unittest.main()

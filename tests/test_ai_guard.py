"""The private-answer guard: a saved free-text private answer never leaves in a prompt (gate G5)."""
import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from support.fake_ai import fake_ai
from support.stubs import settings_private

from jobhunt import ai, db, llm
from jobhunt.ai import guard
from jobhunt.ai.base import PrivateLeak
from jobhunt.core import paths
from jobhunt.core.settings import store

CANARY = "SYNTH-PRIVATE-7731 needs a quiet room"


class InHome(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-guard-")))
        stack.enter_context(paths.set_override(tmp))
        store.drop_cache()
        stack.callback(store.drop_cache)
        self.stack = stack

    def saved(self, answers: dict) -> None:
        self.stack.enter_context(settings_private.saved(answers))

    def rows(self, sql: str) -> list[dict]:
        con = db.connect()
        try:
            return [dict(r) for r in con.execute(sql)]
        finally:
            con.close()


class Guard(InHome):
    def test_a_private_answer_in_a_prompt_is_never_sent(self):
        self.saved({"adjustments": CANARY, "disability": "Yes"})
        hidden = "Facts:  synth-private-7731   NEEDS a\nquiet room, and more."      # case and spacing differ
        with fake_ai(default="ok") as fake:
            for system, user in (("You write.", hidden), (f"You write. {CANARY}", "A made-up advert.")):
                with self.assertRaises(PrivateLeak):
                    ai.chat(system, user, purpose="letter", job_id="job-3")
            with self.assertRaises(PrivateLeak):
                llm.chat("You write.", hidden, role="writer")
        self.assertEqual(fake.log, [])
        stopped = self.rows("SELECT * FROM activity WHERE kind = 'ai.private_stopped'")
        self.assertEqual(len(stopped), 3)
        self.assertEqual(stopped[0]["text"], guard.STOPPED)
        self.assertTrue(stopped[0]["why"])
        everything = json.dumps(self.rows("SELECT * FROM activity") + self.rows("SELECT * FROM ai_usage")).lower()
        self.assertNotIn("synth-private-7731", everything)
        self.assertNotIn("quiet room", everything)

    def test_option_answers_are_not_searched_for(self):
        self.saved({"disability": "Yes", "disability_type": "Prefer not to say", "carer": "No",
                    "guaranteed_interview": "Yes", "adjustments": "None."})
        text = "Yes. No. Prefer not to say is offered on every equality form. None."
        with fake_ai(default="ok") as fake:
            self.assertEqual(ai.chat("You fill forms.", text, purpose="form_fill"), "ok")
        self.assertEqual(len(fake.log), 1)

    def test_short_free_text_is_left_to_the_field_exclusion(self):
        self.saved({"disability_type": "ADHD"})
        self.assertFalse(guard.contains_private("A made-up advert that mentions ADHD support."))

    def test_nothing_saved_means_nothing_to_find(self):
        self.saved({})
        self.assertFalse(guard.contains_private("Any made-up text at all."))


if __name__ == "__main__":
    unittest.main()

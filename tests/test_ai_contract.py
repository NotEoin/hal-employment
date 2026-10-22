"""The AI contract: roles, the fake backend and prompt files."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from support.fake_ai import fake_ai

from jobhunt.ai import base, prompts
from jobhunt.ai.fake import FakeBackend


def req(purpose: str = "", user: str = "A made-up advert.") -> base.ChatRequest:
    return base.ChatRequest(system="You read adverts.", user=user, purpose=purpose)


class Roles(unittest.TestCase):
    """Old role names still work."""

    def test_old_role_names_map_onto_the_new_ones(self):
        self.assertEqual([base.role_for(n) for n in ("main", "writer", "fast", "embed")],
                         ["checker", "writer", "quick", "embed"])
        with self.assertRaises(ValueError):
            base.role_for("poet")

    def test_unavailable_carries_a_known_kind(self):
        self.assertEqual(base.LLMUnavailable("away", kind="signed_out").kind, "signed_out")
        self.assertEqual(base.Limited("busy").kind, "limit")
        with self.assertRaises(ValueError):
            base.LLMUnavailable("away", kind="sulking")


class Fake(unittest.TestCase):
    """The fake answers by purpose or pattern and keeps every request."""

    def test_replies_by_purpose_then_pattern_then_default(self):
        fake = FakeBackend({"letter": "Dear made-up team", "re:made-up advert": lambda r: "seen " + r.purpose},
                           default="nothing")
        self.assertEqual(fake.chat(req("letter"), "m").text, "Dear made-up team")
        self.assertEqual(fake.chat(req("brief"), "m").text, "seen brief")
        self.assertEqual(fake.chat(req("other", user="no match"), "m").text, "nothing")
        self.assertEqual([r.purpose for r in fake.requests()], ["letter", "brief", "other"])

    def test_it_can_be_unavailable_on_demand(self):
        fake = FakeBackend({"letter": base.Limited("Claude is at its limit")})
        with self.assertRaises(base.Limited):
            fake.chat(req("letter"), "m")
        self.assertEqual(fake.requests("letter")[0].purpose, "letter")

    def test_fake_ai_hands_over_a_backend(self):
        with fake_ai({"letter": "Hello"}) as fake:
            self.assertEqual(fake.chat(req("letter"), "m").text, "Hello")


class Prompts(unittest.TestCase):
    """One file per prompt, with header lines and placeholders."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        (self.folder / "letter.txt").write_text(textwrap.dedent("""\
            # name: Cover letter
            # used: Documents, for each application
            # needs: first_name, target_roles
            Write a letter for {first_name}, who wants {target_roles}.
            Reply as JSON: {"letter": "..."}
        """), encoding="utf-8")

    def test_placeholders_are_filled_and_json_braces_kept(self):
        out = prompts.text("letter", folder=self.folder, first_name="Robin", target_roles="museum work")
        self.assertIn("Write a letter for Robin, who wants museum work.", out)
        self.assertIn('{"letter": "..."}', out)

    def test_a_missing_placeholder_raises(self):
        with self.assertRaises(prompts.PromptError):
            prompts.text("letter", folder=self.folder, first_name="Robin")

    def test_an_edit_that_drops_a_placeholder_is_refused_in_plain_words(self):
        prompt = prompts.get("letter", self.folder)
        self.assertEqual(prompt.needs, ("first_name", "target_roles"))
        with self.assertRaises(prompts.PromptError) as caught:
            prompts.check_edit(prompt, "Write a letter for {first_name}.")
        self.assertEqual(str(caught.exception),
                         "This instruction needs {target_roles}; Hal fills it in from your settings.")

    def test_the_headers_are_read(self):
        found = prompts.all_prompts(self.folder)
        self.assertEqual(found["letter"].name, "Cover letter")
        self.assertEqual(len(found["letter"].sha), 64)


if __name__ == "__main__":
    unittest.main()

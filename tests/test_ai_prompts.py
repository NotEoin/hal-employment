"""Hal's instructions to the AI: the person's edits, Reset, "Hal's original changed" and the prompts routes."""
import contextlib
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from support.api import HalServer

from jobhunt import db
from jobhunt.ai import prompts
from jobhunt.core import paths
from jobhunt.core.settings import store

LETTER = textwrap.dedent("""\
    # name: Write a cover letter
    # used: Every application with a letter
    # needs: first_name, target_roles
    You write cover letters for {first_name}, who wants {target_roles}.
""")
SCREEN = textwrap.dedent("""\
    # name: Check whether a job fits
    # used: Screening each new match
    Read the advert. Reply as JSON: {"fit": true}
""")
EXTRA = textwrap.dedent("""\
    # name: Another made-up instruction
    # used: Nothing real
    Say hello.
""")


class Store(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-prompts-")))
        stack.enter_context(paths.set_override(tmp / "home"))
        store.drop_cache()
        self.folder = tmp / "prompts"
        self.folder.mkdir()
        for name, text in (("letter", LETTER), ("screen", SCREEN), ("aaa_extra", EXTRA)):
            (self.folder / f"{name}.txt").write_text(text, encoding="utf-8")
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def text(self) -> str:
        return prompts.text("letter", con=self.con, folder=self.folder, first_name="Robin", target_roles="museum work")

    def test_an_edited_prompt_is_used_and_reset_puts_back_the_original(self):
        self.assertIn("You write cover letters for Robin", self.text())
        prompts.save("letter", "Write warmly for {first_name}, aiming at {target_roles}.", self.con, self.folder)
        self.assertEqual(self.text(), "Write warmly for Robin, aiming at museum work.")
        shown = prompts.detail("letter", self.con, self.folder)
        self.assertEqual((shown["changed"], shown["placeholders"]), (True, ["first_name", "target_roles"]))
        self.assertIn("You write cover letters", shown["default_text"])
        prompts.reset("letter", self.con, self.folder)
        self.assertIn("You write cover letters for Robin", self.text())
        self.assertFalse(prompts.detail("letter", self.con, self.folder)["changed"])

    def test_an_edit_that_drops_a_placeholder_is_refused_and_nothing_is_kept(self):
        with self.assertRaises(prompts.PromptError) as caught:
            prompts.save("letter", "Write warmly for {first_name}.", self.con, self.folder)
        self.assertEqual(str(caught.exception),
                         "This instruction needs {target_roles}; Hal fills it in from your settings.")
        self.assertFalse(prompts.detail("letter", self.con, self.folder)["changed"])

    def test_an_update_to_an_edited_prompt_says_hals_original_changed(self):
        prompts.save("letter", "Write for {first_name}: {target_roles}.", self.con, self.folder)
        self.assertFalse(prompts.listing(self.con, self.folder)[0]["default_changed"])
        (self.folder / "letter.txt").write_text(LETTER + "Keep it short.\n", encoding="utf-8")
        row = next(p for p in prompts.listing(self.con, self.folder) if p["id"] == "letter")
        self.assertEqual((row["changed"], row["default_changed"]), (True, True))

    def test_the_named_prompts_come_first(self):
        self.assertEqual([p["id"] for p in prompts.listing(self.con, self.folder)], ["letter", "screen", "aaa_extra"])


class Routes(unittest.TestCase):
    def test_the_prompt_editor_routes_change_and_reset_an_instruction(self):
        with tempfile.TemporaryDirectory() as tmp, HalServer() as hal:
            folder = Path(tmp)
            (folder / "letter.txt").write_text(LETTER, encoding="utf-8")
            with mock.patch.object(prompts, "FOLDER", folder):
                c = hal.signed_in()
                self.assertEqual([p["id"] for p in c.get("/api/v2/prompts").json()], ["letter"])
                bad = c.request("PUT", "/api/v2/prompts/letter", {"text": "Only {first_name}."})
                self.assertEqual(bad.status, 400)
                self.assertEqual(bad.json()["error"]["field"], "text")
                self.assertIn("needs {target_roles}", bad.json()["error"]["message"])
                ok = c.request("PUT", "/api/v2/prompts/letter", {"text": "{first_name} wants {target_roles}."})
                self.assertEqual((ok.status, ok.json()), (200, {"changed": True}))
                self.assertEqual(c.get("/api/v2/prompts/letter").json()["text"], "{first_name} wants {target_roles}.")
                self.assertEqual(c.request("DELETE", "/api/v2/prompts/letter").json(), {"changed": False})
                self.assertEqual(c.get("/api/v2/prompts/nothing-here").status, 404)
                self.assertEqual(hal.client().get("/api/v2/prompts").status, 401)


if __name__ == "__main__":
    unittest.main()

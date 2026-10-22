"""The private answers store: shown only as saved, kept in the old `[applicant]` shape, read by
exactly two callers, and never written anywhere else.
"""
import ast
import json
import logging
import os
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import db
from jobhunt.core import paths
from jobhunt.core.settings import private, store

CANARY = "SYNTH-PRIVATE-7731 made-up adjustment"
JOBHUNT = Path(paths.code_dir()) / "jobhunt"
# Spec 3.6: only autofill's fixed rules and the AI guard ever see the values.
READERS = {"value_for_form": {"jobhunt/autofill.py"}, "values_for_guard": {"jobhunt/ai/guard.py"}}


class Private(unittest.TestCase):
    """Each test in a fresh temporary home."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hal-private-")
        self.addCleanup(tmp.cleanup)
        override = paths.set_override(Path(tmp.name))
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)
        store.drop_cache()
        self.addCleanup(store.drop_cache)

    def test_private_answers_are_only_ever_shown_as_saved(self):
        with self.assertLogs("jobhunt", logging.DEBUG) as logs:
            logging.getLogger("jobhunt").debug("start")
            saved = private.save({"adjustments": CANARY, "disability": "yes"})
        self.assertEqual(saved, {"disability": True, "disability_type": False, "guaranteed_interview": False,
                                 "adjustments": True, "carer": False})
        shown = json.dumps(private.questions())
        self.assertNotIn("SYNTH-PRIVATE", shown)
        self.assertNotIn("SYNTH-PRIVATE", "\n".join(logs.output))
        con = db.connect()
        try:
            dump = "\n".join(con.iterdump())
        finally:
            con.close()
        self.assertNotIn("SYNTH-PRIVATE", dump)
        self.assertIn("Saved your private answers", dump)
        self.assertEqual(private.value_for_form("disability"), "Yes")
        self.assertEqual(private.value_for_form("adjustments"), CANARY)
        self.assertIsNone(private.value_for_form("carer"))

    def test_the_old_applicant_file_is_read_as_it_is_and_kept_when_saving(self):
        old = ('# made-up answers\n[applicant]\ndisability = "No"\nadjustments = "Extra time in made-up tests"\n'
               'legacy_note = "kept"\n\n[other]\nn = 3\n')
        file = paths.private_path()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(old, encoding="utf-8")
        self.assertTrue(private.status()["disability"])
        private.save({"carer": "No", "disability": ""})
        data = tomllib.loads(file.read_text(encoding="utf-8"))
        self.assertEqual(data["applicant"], {"adjustments": "Extra time in made-up tests", "legacy_note": "kept",
                                             "carer": "No"})
        self.assertEqual(data["other"], {"n": 3})
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(os.stat(file).st_mode), 0o600)

    def test_answers_with_other_characters_are_written_as_escapes(self):
        answer = "Caf\u00e9 \"quiet\" room\nand a made-up chair"
        private.save({"adjustments": answer})
        raw = paths.private_path().read_bytes()
        self.assertTrue(raw.isascii())
        self.assertEqual(private.value_for_form("adjustments"), answer)

    def test_an_option_answer_must_be_one_of_its_options(self):
        with self.assertRaises(private.PrivateAnswerError) as caught:
            private.save({"disability": "Maybe SYNTH-PRIVATE"})
        self.assertNotIn("SYNTH", str(caught.exception))
        self.assertFalse(paths.private_path().exists())
        with self.assertRaises(private.PrivateAnswerError):
            private.save({"shoe_size": "9"})

    def test_the_guard_gets_only_long_free_text_answers(self):
        private.save({"disability": "Yes", "disability_type": "Mild", "adjustments": CANARY, "carer": "No"})
        self.assertEqual(private.values_for_guard(), [CANARY])

    def test_clearing_an_answer_forgets_it(self):
        private.save({"carer": "Yes"})
        private.clear("carer")
        self.assertFalse(private.status()["carer"])

    def test_only_autofill_and_the_ai_guard_read_private_values(self):
        for path in sorted(JOBHUNT.rglob("*.py")):
            rel = path.relative_to(JOBHUNT.parent).as_posix()
            if rel == "jobhunt/core/settings/private.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), rel)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.Attribute):
                    names = [node.attr]
                for name in names:
                    if name in READERS:
                        self.assertIn(rel, READERS[name], f"{rel}:{node.lineno} reads {name}")


class Routes(unittest.TestCase):
    """The private answer routes are write only."""

    def setUp(self):
        from support.api import HalServer

        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()

    def test_answers_go_in_and_only_saved_or_not_comes_out(self):
        reply = self.client.request("PUT", "/api/v2/private", {"answers": {"adjustments": CANARY, "carer": "No"}})
        self.assertEqual(reply.status, 200, reply.text)
        self.assertTrue(reply.json()["saved"]["adjustments"])
        listing = self.client.get("/api/v2/private")
        self.assertNotIn("SYNTH-PRIVATE", listing.text)
        saved = {q["key"]: q["saved"] for q in listing.json()["questions"]}
        self.assertEqual(saved["carer"], True)
        for path in ("/api/v2/settings", "/api/v2/settings/schema"):
            self.assertNotIn("SYNTH-PRIVATE", self.client.get(path).text)
        self.assertEqual(self.client.request("DELETE", "/api/v2/private/carer").json(), {"saved": False})

    def test_a_bad_option_names_its_question_and_not_the_answer(self):
        reply = self.client.request("PUT", "/api/v2/private", {"answers": {"disability": "SYNTH-PRIVATE"}})
        self.assertEqual((reply.status, reply.json()["error"]["field"]), (400, "disability"))
        self.assertNotIn("SYNTH-PRIVATE", reply.text)


if __name__ == "__main__":
    unittest.main()

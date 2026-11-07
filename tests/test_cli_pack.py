"""`jobhunt pack` names the AI that is not ready instead of blaming Ollama, and never shows a traceback."""
import argparse
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import FakeBackend, fake_ai

from jobhunt import __main__ as cli_main
from jobhunt.ai.base import LLMUnavailable, Status
from jobhunt.cli import docs


class PackWithoutAi(unittest.TestCase):
    def test_a_pack_with_the_ai_down_says_why_it_is_down(self):
        args = argparse.Namespace(cv_only=False, shortlisted=True, id=None, no_web=True)
        with fake_ai() as be, mock.patch.object(
                FakeBackend, "status", return_value=Status(ready=False, why="Hal's gateway does not answer.")):
            self.assertIs(be.status().ready, False)
            with self.assertRaises(SystemExit) as stop:
                docs.cmd_pack(args)
        self.assertEqual(str(stop.exception), "Packs need the AI: Hal's gateway does not answer.")

    def test_an_ai_that_refuses_mid_pack_is_one_plain_line(self):
        # the gateway answered its status but refused each request, and `pack` printed a traceback (2026-11-06)
        refused = LLMUnavailable("The local server refused the request (503): Hal is off; run `hal on`")
        with mock.patch.object(cli_main, "cmd_pack", side_effect=refused), \
                mock.patch("sys.argv", ["jobhunt", "pack", "made-up-id", "--no-web"]), \
                self.assertRaises(SystemExit) as stop:
            cli_main.main()
        self.assertEqual(str(stop.exception), f"jobhunt pack: {refused}")


if __name__ == "__main__":
    unittest.main()

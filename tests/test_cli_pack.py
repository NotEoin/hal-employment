"""`jobhunt pack` names the AI that is not ready instead of blaming Ollama."""
import argparse
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import FakeBackend, fake_ai

from jobhunt.ai.base import Status
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


if __name__ == "__main__":
    unittest.main()

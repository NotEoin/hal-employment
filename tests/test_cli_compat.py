"""The command line prints exactly what it printed at ef53088 (gate G4), so the voice bridge keeps working.

The goldens in tests/goldens/cli were recorded from the untouched code on a made-up database
(`scripts/dev/golden_cli.py --record`). A commit that means to change an output re-records only that golden.
"""

import json
import re
import unittest

from support import cli
from support.world import jobs

from jobhunt.score import score


class CommandLine(unittest.TestCase):
    """Each recorded command, run in a fresh made-up world, against its golden."""

    def compare(self, cases):
        for case in cases:
            with self.subTest(case.name):
                want = cli.golden_path(case).read_text(encoding="utf-8")
                self.assertEqual(cli.output(case), want, f"`jobhunt {' '.join(case.argv)}` changed")

    def test_cli_output_matches_the_goldens_recorded_before_any_change(self):
        self.compare([c for c in cli.CASES if not c.name.startswith("help")])

    def test_help_text_is_unchanged(self):
        self.compare([c for c in cli.CASES if c.name.startswith("help")])

    def test_every_command_has_a_help_golden(self):
        listed = re.search(r"\{([a-z0-9,-]+)\}", cli.output(cli.Case("help", ["--help"]))).group(1).split(",")
        self.assertEqual({c.argv[0] for c in cli.CASES if c.name.startswith("help-")}, set(listed))

    def test_the_made_up_jobs_score_as_recorded(self):
        recorded = json.loads((cli.GOLDENS.parent / "score.json").read_text(encoding="utf-8"))
        self.assertEqual({j["id"]: list(score(j)) for j in jobs()}, recorded)


if __name__ == "__main__":
    unittest.main()

"""The command line prints exactly what it printed at ef53088 (gate G4), so the voice bridge keeps working.

The goldens in tests/goldens/cli were recorded from the untouched code on a made-up database
(`scripts/dev/golden_cli.py --record`). A commit that means to change an output re-records only that golden.
Help follows G4's rule: old commands keep their help apart from options added by name, and the
top-level help keeps every old line and only gains new command names (`cli.comparable`).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import cli
from support.world import jobs

from jobhunt.score import score


class CommandLine(unittest.TestCase):
    """Each recorded command, run in a fresh made-up world, against its golden."""

    def compare(self, cases):
        for case in cases:
            with self.subTest(case.name):
                want = cli.comparable(case, cli.golden_path(case).read_text(encoding="utf-8"))
                self.assertEqual(cli.comparable(case, cli.output(case)), want,
                                 f"`jobhunt {' '.join(case.argv)}` changed")

    def test_cli_output_matches_the_goldens_recorded_before_any_change(self):
        self.compare([c for c in cli.CASES if not c.name.startswith("help")])

    # argparse changed its option layout in 3.13 ("-m, --min MIN"); the help goldens are 3.13's, and no caller
    # parses help text (G4)
    @unittest.skipIf(sys.version_info < (3, 13), "the help goldens hold Python 3.13's argparse layout")
    def test_help_of_every_existing_command_is_unchanged(self):
        self.compare([c for c in cli.CASES if c.name.startswith("help")])

    def test_the_help_rule_allows_only_the_named_additions(self):
        top = next(c for c in cli.CASES if c.name == "help")
        serve = next(c for c in cli.CASES if c.name == "help-serve")
        old_top, old_serve = (cli.golden_path(c).read_text(encoding="utf-8") for c in (top, serve))
        self.assertNotEqual(cli.comparable(top, old_top.replace("probe}", "probe,purge}")), old_top)
        self.assertNotEqual(cli.comparable(top, old_top.replace("    record ", "    records ")), old_top)
        self.assertNotEqual(cli.comparable(serve, old_serve.replace(" [--open]", "")),
                            cli.comparable(serve, old_serve))
        self.assertNotEqual(cli.comparable(serve, old_serve + "  --quiet\n"), cli.comparable(serve, old_serve))

    def test_every_command_has_a_help_golden(self):
        listed = re.search(r"\{([a-z0-9,-]+)\}", cli.output(cli.Case("help", ["--help"]))).group(1).split(",")
        self.assertEqual({c.argv[0] for c in cli.CASES if c.name.startswith("help-")}, set(listed))

    def test_the_made_up_jobs_score_as_recorded(self):
        recorded = json.loads((cli.GOLDENS.parent / "score.json").read_text(encoding="utf-8"))
        self.assertEqual({j["id"]: list(score(j)) for j in jobs()}, recorded)


SYSTEM_PYTHON = Path("/usr/bin/python3")
PACKAGE = Path(__file__).resolve().parent.parent / "jobhunt"
RUN_TIMEOUT = 60  # seconds; the stand-in venv answers at once


@unittest.skipUnless(SYSTEM_PYTHON.exists(), "needs /usr/bin/python3, as the Hal tools use")
class Trampoline(unittest.TestCase):
    """`/usr/bin/python3 -m jobhunt` from a checkout re-runs itself with the checkout's `.venv` Python."""

    def checkout(self) -> Path:
        """A copy of the package's entry point beside a stand-in `.venv/bin/python` that reports how it ran."""
        root = Path(tempfile.mkdtemp(prefix="hal-trampoline-"))
        self.addCleanup(shutil.rmtree, root)
        (root / "jobhunt").mkdir()
        for name in ("__init__.py", "__main__.py"):
            shutil.copy(PACKAGE / name, root / "jobhunt" / name)
        venv = root / ".venv" / "bin"
        venv.mkdir(parents=True)
        report = 'echo "venv python: $* (no trampoline: $HAL_EMPLOYMENT_NO_TRAMPOLINE)"'
        (venv / "python").write_text(f"#!/bin/sh\n{report}\n")
        (venv / "python").chmod(0o755)
        return root

    def run_system_python(self, root: Path, **env) -> subprocess.CompletedProcess:
        clean = {k: v for k, v in os.environ.items() if k != "HAL_EMPLOYMENT_NO_TRAMPOLINE"}
        return subprocess.run([str(SYSTEM_PYTHON), "-m", "jobhunt", "list", "-n", "3"], cwd=root, env=clean | env,
                              capture_output=True, text=True, timeout=RUN_TIMEOUT)

    def test_system_python_runs_the_command_line_inside_the_venv(self):
        done = self.run_system_python(self.checkout())
        self.assertEqual(done.stdout, "venv python: -X utf8 -m jobhunt list -n 3 (no trampoline: 1)\n")
        self.assertEqual(done.returncode, 0)

    def test_the_venv_python_is_not_run_again_once_the_trampoline_has_run(self):
        done = self.run_system_python(self.checkout(), HAL_EMPLOYMENT_NO_TRAMPOLINE="1")
        self.assertNotIn("venv python", done.stdout)


if __name__ == "__main__":
    unittest.main()

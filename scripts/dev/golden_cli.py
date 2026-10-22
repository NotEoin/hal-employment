"""Records and compares the command-line goldens (gate G4) and today's scores of the made-up jobs.

The goldens were recorded from the code at ef53088, before any change, so `--record` is only for a commit
whose purpose is to change the output (the commit says so, and changes only the goldens it means to).

    python scripts/dev/golden_cli.py --check            compare every case; print a diff for each change
    python scripts/dev/golden_cli.py --record [NAME..]  write the goldens (all, or the named cases)
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]
os.environ.setdefault("HAL_TEST_MODE", "1")

from support import cli  # noqa: E402 - needs the tests folder on the path first
from support.world import jobs  # noqa: E402 - same

SCORES = ROOT / "tests" / "goldens" / "score.json"


def scores() -> dict[str, list]:
    """Today's `score()` and score notes for every made-up job, by job id."""
    from jobhunt.score import score

    return {job["id"]: list(score(job)) for job in jobs()}


def check() -> int:
    """Prints a diff for each case whose output changed; returns how many changed."""
    changed = 0
    for case in cli.CASES:
        path = cli.golden_path(case)
        want = path.read_text(encoding="utf-8") if path.exists() else ""
        got = cli.output(case)
        if got != want:
            changed += 1
            sys.stdout.writelines(difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                                       f"golden/{case.name}", f"now/{case.name}"))
    if json.loads(SCORES.read_text(encoding="utf-8")) != scores():
        changed += 1
        print("scores of the made-up jobs changed; see tests/goldens/score.json")
    print(f"{len(cli.CASES) + 1 - changed} of {len(cli.CASES) + 1} goldens match")
    return changed


def record(names: list[str]) -> None:
    """Writes the goldens of the named cases, or of every case and the scores."""
    cli.GOLDENS.mkdir(parents=True, exist_ok=True)
    for case in cli.CASES:
        if not names or case.name in names:
            cli.golden_path(case).write_text(cli.output(case), encoding="utf-8")
    if not names or "score" in names:
        SCORES.write_text(json.dumps(scores(), indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Runs the chosen mode; returns 1 when `--check` finds a change."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="compare the output with the goldens")
    g.add_argument("--record", nargs="*", metavar="NAME", help="write the goldens of these cases, or all")
    args = p.parse_args(argv)
    if args.check:
        return 1 if check() else 0
    record(args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())

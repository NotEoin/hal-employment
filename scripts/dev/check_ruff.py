"""Runs ruff and reports only the findings on added lines (staged, or since a base commit).

The code at ef53088 has about 230 ruff findings, so a whole-repo `ruff check .` cannot be a gate until a
clean-up commit lands. New code is held to the full rule set from its first line, and an edited legacy line
must be clean too.

    python scripts/dev/check_ruff.py              added lines in the staged diff
    python scripts/dev/check_ruff.py --base REF   added lines between REF and the working tree
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from check_ascii import ROOT, added_lines

RUFF_TIMEOUT = 120  # seconds; ruff checks the whole repo in under one


def ruff_findings(paths: list[str]) -> list[dict]:
    """Ruff's JSON findings for `paths`."""
    if not paths:
        return []
    done = subprocess.run([sys.executable, "-m", "ruff", "check", "--output-format", "json", "--no-fix", *paths],
                          cwd=ROOT, capture_output=True, text=True, timeout=RUFF_TIMEOUT)
    if done.returncode not in (0, 1):
        raise RuntimeError(f"ruff failed: {done.stderr.strip()}")
    return json.loads(done.stdout or "[]")


def main(argv: list[str] | None = None) -> int:
    """Prints each finding on an added line and returns 1 when there is one."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", help="check lines added since this commit, including uncommitted changes")
    args = p.parse_args(argv)
    added = {path: {n for n, _ in rows} for path, rows in added_lines(args.base).items()
             if path.endswith(".py") and (ROOT / path).is_file()}
    shown = 0
    for f in ruff_findings(sorted(added)):
        path = str(f["filename"]).removeprefix(str(ROOT) + "/")
        row = f["location"]["row"]
        # Unsorted imports are reported on the block's first line, which may predate the change.
        if row in added.get(path, ()) or (f["code"] == "I001" and any(
                row <= n <= f["end_location"]["row"] for n in added.get(path, ()))):
            print(f"{path}:{row}:{f['location']['column']}: {f['code']} {f['message']}")
            shown += 1
    return 1 if shown else 0


if __name__ == "__main__":
    sys.exit(main())

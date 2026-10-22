"""Finds the owner's personal strings in the tree or the staged diff, printing only where, never what.

The patterns live outside the repo, one per line (`#` starts a comment), in
`~/.config/hal-employment/personal-strings.txt` or the file named by `--list` or `HAL_PERSONAL_STRINGS`.
Matching ignores case. When the list is absent the check is skipped, so the script works on any clone.

    python scripts/dev/check_personal.py            every tracked file
    python scripts/dev/check_personal.py --staged   added lines in the staged diff
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from check_ascii import GIT_TIMEOUT, ROOT, added_lines

DEFAULT_LIST = Path.home() / ".config" / "hal-employment" / "personal-strings.txt"


def patterns(path: Path) -> re.Pattern | None:
    """One case-insensitive pattern matching any listed string, or None when the list is empty."""
    words = [w.strip() for w in path.read_text(encoding="utf-8").splitlines()]
    words = [w for w in words if w and not w.startswith("#")]
    return re.compile("|".join(re.escape(w) for w in words), re.I) if words else None


def tree_lines() -> dict[str, list[tuple[int, str]]]:
    """Every line of every tracked text file."""
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True,
                           timeout=GIT_TIMEOUT).stdout.splitlines()
    out = {}
    for name in names:
        file = ROOT / name
        if not file.is_file():
            continue
        data = file.read_bytes()
        if b"\0" not in data[:8000]:
            out[name] = list(enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1))
    return out


def main(argv: list[str] | None = None) -> int:
    """Prints file:line for each match and returns 1 when there is one."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--staged", action="store_true", help="check added lines in the staged diff only")
    p.add_argument("--list", type=Path, help="the file of personal strings")
    args = p.parse_args(argv)
    listed = args.list or Path(os.environ.get("HAL_PERSONAL_STRINGS") or DEFAULT_LIST)
    if not listed.is_file():
        print(f"personal-string scan skipped: no list at {listed}", file=sys.stderr)
        return 0
    pattern = patterns(listed)
    if pattern is None:
        return 0
    lines = added_lines() if args.staged else tree_lines()
    hits = [f"{path}:{n}" for path, rows in sorted(lines.items()) for n, text in rows if pattern.search(text)]
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())

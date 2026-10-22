"""Finds non-ASCII characters in the repo's text: in added lines (staged, or since a base commit) or the whole tree.

Repo text is ASCII. Fonts, vendored libraries, the lock file and the Unicode test fixtures are
exempt. The whole-tree mode also skips the files that held non-ASCII before the rebuild (`LEGACY`), because a
clean-up is its own change; added lines in those files are still checked. Matches are printed with the
characters escaped, so the report itself is ASCII.

    python scripts/dev/check_ascii.py              added lines in the staged diff
    python scripts/dev/check_ascii.py --base REF   added lines between REF and the working tree
    python scripts/dev/check_ascii.py --tree       every tracked text file
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GIT_TIMEOUT = 60  # seconds; a diff of the whole repo takes well under one
NON_ASCII = re.compile(r"[^\x00-\x7f]")
EXEMPT_PREFIXES = ("jobhunt/web/static/app/lib/", "jobhunt/web/static/app/fonts/", "tests/vendor/",
                   "tests/fixtures/unicode/")
EXEMPT_SUFFIXES = (".woff2", ".woff", ".ttf", ".otf", ".png", ".jpg", ".pdf", ".ico")
EXEMPT_FILES = {"uv.lock"}
# Non-ASCII already present at ef53088; each line is fixed by the change that next edits it.
LEGACY = {
    "extension/background.js", "extension/content.js", "jobhunt/adopt.py", "jobhunt/autofill.py",
    "jobhunt/browser.py", "jobhunt/cvscore.py", "jobhunt/salary.py", "jobhunt/web/static/app.js",
    "jobhunt/web/static/classic/app.js", "tests/test_salary.py", "tests/test_autofill.py",
    "tests/test_cvbuild.py",
}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def git(*args: str) -> str:
    """Output of a git command run at the repo root."""
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="surrogateescape", timeout=GIT_TIMEOUT).stdout


def exempt(path: str) -> bool:
    """True when `path` may hold non-ASCII text."""
    return path in EXEMPT_FILES or path.startswith(EXEMPT_PREFIXES) or path.lower().endswith(EXEMPT_SUFFIXES)


def added_lines(base: str | None = None) -> dict[str, list[tuple[int, str]]]:
    """Lines added per file, as (line number, text): staged when `base` is None, else from `base` to the tree.

    A line identical to one the same diff removes counts as moved, not added, so moving legacy code into a
    new module does not make its old lines new.
    """
    args = ["diff", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=AMRD"]
    args += [base] if base else ["--cached"]
    added: dict[str, list[tuple[int, str]]] = {}
    removed: set[str] = set()
    path, line = None, 0
    for raw in git(*args).splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("--- "):
            continue
        elif raw.startswith("@@"):
            m = HUNK.match(raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("-"):
            removed.add(raw[1:].strip())
        elif raw.startswith("+") and path:
            added.setdefault(path, []).append((line, raw[1:]))
            line += 1
    return {p: kept for p, rows in added.items() if (kept := [r for r in rows if r[1].strip() not in removed])}


def tree_lines() -> dict[str, list[tuple[int, str]]]:
    """Every line of every tracked text file outside `LEGACY`."""
    out: dict[str, list[tuple[int, str]]] = {}
    for path in git("ls-files").splitlines():
        if path in LEGACY or exempt(path) or not (ROOT / path).is_file():
            continue
        data = (ROOT / path).read_bytes()
        if b"\0" in data[:8000]:
            continue
        text = data.decode("utf-8", errors="replace")
        out[path] = list(enumerate(text.splitlines(), start=1))
    return out


def escaped(text: str) -> str:
    """`text` with each non-ASCII character written as its code point."""
    return NON_ASCII.sub(lambda m: f"<U+{ord(m.group()):04X}>", text)


def findings(lines: dict[str, list[tuple[int, str]]]) -> list[str]:
    """One "path:line: text" entry per line holding non-ASCII, outside exempt files."""
    return [f"{path}:{n}: {escaped(text).strip()[:160]}"
            for path, rows in sorted(lines.items()) if not exempt(path)
            for n, text in rows if NON_ASCII.search(text)]


def main(argv: list[str] | None = None) -> int:
    """Prints each finding and returns 1 when there is one."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--base", help="check lines added since this commit, including uncommitted changes")
    g.add_argument("--tree", action="store_true", help="check every tracked text file")
    args = p.parse_args(argv)
    found = findings(tree_lines() if args.tree else added_lines(args.base))
    for f in found:
        print(f)
    if found:
        print(f"{len(found)} line(s) with non-ASCII characters; write escapes such as \\u00a3 instead.",
              file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

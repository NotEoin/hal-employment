"""The project documents hold their shape: the index, the how-tos, the README, the licence, the notices and CI.

Docs are checked as text, so the checks run the same on Linux and Windows: files are read with universal
newlines (a Windows checkout has CRLF), and paths are compared as POSIX strings relative to the repo.
The README's commands are checked against the files and the command line they name, so a renamed script or
option fails here rather than in the hands of someone following the README.
"""
import os
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

import support  # noqa: F401 - must come before jobhunt

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INDEX = DOCS / "README.md"
README = ROOT / "README.md"
CI = ROOT / ".github" / "workflows"
MAX_PICTURE_BYTES = 250_000   # a README picture loads on every visit to the repository page
HELP_TIMEOUT_S = 60      # `python -m jobhunt <cmd> --help` imports the command modules; a second or two is usual
# Root documents every reader may need; the index links each of them too.
ROOT_DOCS = ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md", "LICENSE")

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^\s*```(\S*)\s*$")
STEP = re.compile(r"^(\d+)\. \S")
SCRIPT = re.compile(r"(?<![\w./-])((?:scripts|tests)/[\w./-]+\.(?:sh|cmd|py))")
JOBHUNT = re.compile(r"-m jobhunt ([a-z][\w-]*)((?: +-[-\w]+(?: +[^\s#-][^\s#]*)?)*)")
OPTION = re.compile(r"(?<![\w-])(--?[a-zA-Z][\w-]*)")
EXTRA = re.compile(r"--extra +([\w-]+)")
SHELL_LANGS = {"sh", "bash", "shell", "console", "powershell", "ps1", "pwsh", "bat", "cmd"}
SHA_PIN = re.compile(r"^\s*-?\s*uses:\s*[\w./-]+@[0-9a-f]{40}\b")


def text(path: Path) -> str:
    """A document's text with `\\n` line ends, whatever the checkout did to them."""
    return path.read_text(encoding="utf-8")


def fenced_blocks(body: str) -> list[tuple[str, list[str]]]:
    """Each fenced block of a Markdown text as (language, lines)."""
    blocks, lang, lines = [], None, []
    for line in body.splitlines():
        m = FENCE.match(line)
        if m and lang is None:
            lang, lines = m.group(1).lower(), []
        elif m:
            blocks.append((lang, lines))
            lang = None
        elif lang is not None:
            lines.append(line)
    return blocks


def outside_fences(body: str) -> list[str]:
    """The lines of a Markdown text that are not inside a fenced block."""
    out, inside = [], False
    for line in body.splitlines():
        if FENCE.match(line):
            inside = not inside
        elif not inside:
            out.append(line)
    return out


def index_targets() -> dict[str, str]:
    """The index's rows: each linked file, relative to docs/ as a POSIX path, with its "What it is" cell."""
    rows = {}
    for line in text(INDEX).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        m = LINK.search(cells[0])
        if m:
            target = (DOCS / m.group(1)).resolve()
            base = DOCS.resolve() if DOCS.resolve() in target.parents else ROOT.resolve()
            rows[target.relative_to(base).as_posix()] = cells[1]
    return rows


def doc_files() -> list[Path]:
    """Every Markdown file under docs/, the index left out."""
    return sorted(p for p in DOCS.rglob("*.md") if p != INDEX)


def comment_of(line: str, lang: str) -> bool:
    """True when a command line in a fenced block ends with a comment in its shell's own form."""
    if lang in ("bat", "cmd"):
        return bool(re.search(r"(&\s*rem\b|^\s*rem\b|^\s*::)", line, re.I))
    return bool(re.search(r"(^|\s)#", line))


def takes(source: str, option: str) -> bool:
    """True when a script's text or a command's help names `option` as a whole word."""
    return re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", source) is not None


def last_table(body: str) -> list[str]:
    """The lines of the table that ends the text, or [] when the text does not end with a table."""
    lines = body.rstrip().splitlines()
    table = []
    while lines and lines[-1].lstrip().startswith("|"):
        table.insert(0, lines.pop().strip())
    return table


class TheIndex(unittest.TestCase):
    def test_every_doc_is_in_the_index(self):
        rows = index_targets()
        missing = [p.relative_to(DOCS).as_posix() for p in doc_files()
                   if p.relative_to(DOCS).as_posix() not in rows]
        self.assertEqual(missing, [], "add a | Document | What it is | row to docs/README.md for each")

    def test_the_index_is_a_document_and_what_it_is_table(self):
        self.assertIn("| Document | What it is |", text(INDEX))
        for target, what in index_targets().items():
            with self.subTest(target):
                self.assertTrue(what and what != "---", "each row says what the document is")

    def test_every_row_of_the_index_points_at_a_file(self):
        gone = [t for t in index_targets() if not (DOCS / t).exists() and not (ROOT / t).exists()]
        self.assertEqual(gone, [])

    def test_the_index_links_the_root_documents(self):
        rows = index_targets()
        self.assertEqual([d for d in ROOT_DOCS if d not in rows], [])
        for d in ROOT_DOCS:
            self.assertTrue((ROOT / d).exists(), d)

    def test_every_relative_link_in_the_docs_points_at_a_file(self):
        broken = []
        pages = [*doc_files(), INDEX, README, *(ROOT / d for d in ROOT_DOCS if d.endswith(".md"))]
        for page in pages:
            for line in outside_fences(text(page)):
                for target in LINK.findall(line):
                    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                        continue
                    rel = target.split("#", 1)[0]
                    if (page.parent / rel).exists():
                        continue
                    broken.append(f"{page.relative_to(ROOT).as_posix()}: {target}")
        self.assertEqual(broken, [])


class HowTos(unittest.TestCase):
    def test_every_howto_has_numbered_steps_and_a_symptom_table(self):
        pages = sorted((DOCS / "howto").glob("*.md"))
        self.assertTrue(pages)
        for page in pages:
            body = text(page)
            with self.subTest(page.name):
                first = body.splitlines()[0]
                self.assertRegex(first, r"^# \S", "titled by its task")
                self.assertNotRegex(first, r"^# (How|What|Why)\b", "a task, not a question")
                steps = [int(STEP.match(line.strip()).group(1)) for line in outside_fences(body)
                         if STEP.match(line.strip())]
                self.assertGreaterEqual(len(steps), 2, "numbered steps")
                self.assertEqual(steps[0], 1)
                table = last_table(body)
                self.assertTrue(table, "ends with a table")
                self.assertRegex(table[0], r"^\| *Symptom *\| *Fix *\|$")
                self.assertGreaterEqual(len(table), 3, "the table has at least one row")

    def test_every_command_in_a_howto_says_what_it_does(self):
        bare = []
        for page in sorted((DOCS / "howto").glob("*.md")):
            for lang, lines in fenced_blocks(text(page)):
                if lang not in SHELL_LANGS:
                    continue
                for line in lines:
                    s = line.strip()
                    if s and not s.startswith(("#", "rem ", "::")) and not comment_of(s, lang):
                        bare.append(f"{page.name}: {s}")
        self.assertEqual(bare, [], "end each command with a trailing comment")


class TheReadme(unittest.TestCase):
    def setUp(self):
        self.body = text(README)
        self.commands = [line for lang, lines in fenced_blocks(self.body) for line in lines
                         if lang in SHELL_LANGS or lang == ""]

    def test_the_readme_says_what_hal_is_and_how_to_install_use_and_develop_it(self):
        headings = [h.lower() for h in re.findall(r"^## (.+)$", self.body, re.M)]
        for word in ("why it exists", "install", "use", "configuration", "development", "licence"):
            with self.subTest(word):
                self.assertTrue(any(word in h for h in headings), f"a heading about {word}")
        intro = self.body.split("\n## ", 1)[0]
        self.assertIn("never presses Submit", intro)
        for name in ("install-hal-windows.cmd", "install-hal-linux.sh"):
            self.assertIn(name, self.body)

    def test_every_picture_in_the_readme_is_in_the_repo_and_small(self):
        pictures = re.findall(r"!\[[^\]]+\]\(([^)\s]+)\)", self.body)
        self.assertTrue(pictures)
        for rel in pictures:
            with self.subTest(rel):
                path = ROOT / rel
                self.assertTrue(path.is_file())
                self.assertLessEqual(path.stat().st_size, MAX_PICTURE_BYTES)

    def test_the_readme_commands_match_the_scripts(self):
        named = set()
        for line in self.commands:
            code = line.split(" #", 1)[0]
            for m in SCRIPT.finditer(code):
                path = ROOT / m.group(1)
                named.add(m.group(1))
                with self.subTest(script=m.group(1)):
                    self.assertTrue(path.is_file(), f"{m.group(1)} is not in the repo")
                    after = code[m.end():].split("&&", 1)[0].split("|", 1)[0]
                    for option in OPTION.findall(after):
                        self.assertTrue(takes(text(path), option), f"{m.group(1)} does not take {option}")
        self.assertIn("scripts/dev/checkpoint.sh", named)
        for line in outside_fences(self.body):
            for m in re.finditer(r"`((?:scripts|tests)/[\w./-]+)`", line):
                self.assertTrue((ROOT / m.group(1)).exists(), m.group(1))

    def test_the_readme_extras_are_the_projects_extras(self):
        project = tomllib.loads(text(ROOT / "pyproject.toml"))["project"]
        extras = set(project.get("optional-dependencies", {}))
        used = {m for line in self.commands for m in EXTRA.findall(line)}
        self.assertTrue(used)
        self.assertEqual(used - extras, set())

    def test_the_readme_commands_name_real_hal_commands_and_options(self):
        wanted: dict[str, set[str]] = {}
        for line in self.commands:
            for m in JOBHUNT.finditer(line.split(" #", 1)[0]):
                wanted.setdefault(m.group(1), set()).update(OPTION.findall(m.group(2)))
        self.assertIn("serve", wanted)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "COLUMNS": "200"}
        for command, options in sorted(wanted.items()):
            with self.subTest(command):
                done = subprocess.run([sys.executable, "-m", "jobhunt", command, "--help"], capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT,
                                      timeout=HELP_TIMEOUT_S, check=False)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                for option in options:
                    self.assertTrue(takes(done.stdout, option), f"`jobhunt {command}` has no {option}")


class Notices(unittest.TestCase):
    def test_every_dependency_and_vendored_file_has_a_notice(self):
        notices = text(ROOT / "THIRD_PARTY_NOTICES.md").lower()
        project = tomllib.loads(text(ROOT / "pyproject.toml"))["project"]
        specs = list(project["dependencies"])
        for extra in project.get("optional-dependencies", {}).values():
            specs += extra
        names = {re.match(r"[A-Za-z0-9_.-]+", s).group(0).lower() for s in specs}
        vendored = set(re.findall(r'Package\("([^"]+)"', text(ROOT / "scripts" / "dev" / "vendor_web.py")))
        self.assertTrue(vendored)
        missing = sorted(n for n in names | {v.lower() for v in vendored} if n not in notices)
        self.assertEqual(missing, [])

    def test_the_licence_is_mit(self):
        body = text(ROOT / "LICENSE")
        self.assertTrue(body.startswith("MIT License\n"))
        self.assertRegex(body, r"(?m)^Copyright \(c\) 2026 \S")

    def test_security_covers_the_threat_model_storage_what_leaves_and_reporting(self):
        headings = " ".join(re.findall(r"^## (.+)$", text(ROOT / "SECURITY.md"), re.M)).lower()
        for part in ("protects against", "cannot protect", "stored", "leaves", "report"):
            self.assertIn(part, headings)


class Ci(unittest.TestCase):
    def setUp(self):
        self.body = text(CI / "ci.yml")

    def test_ci_runs_linux_on_two_pythons_and_windows(self):
        for row in ('os: ubuntu-24.04, python: "3.11"', 'os: ubuntu-24.04, python: "3.13"',
                    'os: windows-2025, python: "3.13"'):
            self.assertIn(row, self.body)
        self.assertIn("unittest discover tests", self.body)
        self.assertIn('HAL_TEST_BROWSER: "1"', self.body)

    def test_every_action_is_pinned_to_a_commit(self):
        loose = []
        for workflow in sorted(CI.glob("*.yml")):
            for line in text(workflow).splitlines():
                if re.match(r"^\s*-?\s*uses:", line) and not SHA_PIN.match(line):
                    loose.append(f"{workflow.name}: {line.strip()}")
        self.assertEqual(loose, [])

    # Regression (windows-2025 runner): a font load failed with net::ERR_NO_BUFFER_SPACE late in the suite.
    def test_the_windows_job_has_room_for_the_browser_tests_connections(self):
        self.assertIn("netsh int ipv4 set dynamicport tcp start=32768 num=32768", self.body)

    def test_the_windows_job_proves_its_own_checks_ran(self):
        named = re.search(r"for t in ([\w ]+); do", self.body)
        self.assertIsNotNone(named, "the Windows step lists the tests that must say ok")
        tests = named.group(1).split()
        self.assertIn("test_the_powershell_part_parses", tests)
        self.assertIn("test_accented_names_print_in_every_look", tests)
        sources = "\n".join(text(p) for p in (ROOT / "tests").glob("test_*.py"))
        for name in tests:
            self.assertIn(f"def {name}(", sources, f"{name} was renamed; update ci.yml")


if __name__ == "__main__":
    unittest.main()

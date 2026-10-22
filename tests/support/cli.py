"""Runs `python -m jobhunt` in-process inside a made-up `World` and records what it prints, for the goldens.

The command line is a contract: the voice bridge and the /jobs command parse its output.
`CASES` lists the commands gate G4 holds to the output recorded at ef53088, before any change. Each case
runs in a fresh world, so a command that writes (`set`, `note`, `todo add`) cannot change another's output.

Output is normalised before it is stored or compared: the world's folder becomes `<home>`, timestamps and
dates become `<time>`, and the greeting's part of the day is dropped, because those change with every run.
Python 3.13 prints `-s, --status STATUS` where 3.11 and 3.12 print `-s STATUS, --status STATUS`; the older
form is rewritten to the newer, so the help goldens hold on every supported Python.
"""

from __future__ import annotations

import contextlib
import gc
import importlib
import io
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from support.world import World, jobs

GOLDENS = Path(__file__).resolve().parent.parent / "goldens" / "cli"
HELP_WIDTH = "100"  # argparse wraps help to $COLUMNS; fixed so the goldens do not follow the terminal
TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?")
GREETING = re.compile(r"\bGood (morning|afternoon|evening)\b")
OLD_OPTION = re.compile(r"(?<![\w-])(-\w) ([A-Z][A-Z_]*), (--[\w-]+) \2\b")

SUBCOMMANDS = ["discover", "rescore", "mail", "digest", "vault", "record", "queue", "contacts", "todo", "serve",
               "add", "screen", "letter", "check", "pack", "inbox", "stats", "list", "show", "set", "note", "probe"]


@dataclass
class Case:
    """One command to run, by the golden file it is stored in."""

    name: str
    argv: list[str]
    world: dict = field(default_factory=dict)
    fake_sources: bool = False


CASES = [
    Case("queue", ["queue"]),
    Case("list-all", ["list", "-s", "all", "-n", "40"]),
    Case("list-m60-n8", ["list", "-m", "60", "-n", "8"]),
    Case("list-m60-n15", ["list", "-m", "60", "-n", "15"]),
    Case("list-internships", ["list", "--internships"]),
    Case("show", ["show", "cobalt-interview"]),
    Case("set", ["set", "fernhill-grad", "shortlisted", "worth", "a", "look"]),
    Case("note", ["note", "tidewater-grad", "Called", "the", "recruiter"]),
    Case("todo", ["todo"]),
    Case("todo-add", ["todo", "add", "Write", "to", "the", "referee"]),
    Case("stats", ["stats"]),
    Case("inbox", ["inbox", "-n", "15"]),
    Case("digest-dry-run", ["digest", "--dry-run"]),
    Case("digest-spoken", ["digest", "--spoken"]),
    Case("record-status", ["record", "status"]),
    Case("mail", ["mail", "-q"]),
    Case("mail-no-password", ["mail", "-q"], world={"imap": False}),
    Case("pack", ["pack", "sorrel-ready", "--no-web"]),
    Case("discover", ["discover", "-q"], fake_sources=True),
    Case("help", ["--help"]),
    *(Case(f"help-{cmd}", [cmd, "--help"]) for cmd in SUBCOMMANDS),
]


def normalise(text: str, root: Path | None = None) -> str:
    """`text` with the run-to-run differences of the module docstring replaced."""
    if root is not None:
        text = text.replace(str(root), "<home>")
    text = TIME.sub("<time>", text)
    text = GREETING.sub("Good <part of day>", text)
    return OLD_OPTION.sub(r"\1, \3 \2", text)


def _command_module(attr: str):
    """The module holding a CLI table such as `STANDALONE`: `jobhunt.cli.find` once split, else `__main__`."""
    with contextlib.suppress(ImportError):
        module = importlib.import_module("jobhunt.cli.find")
        if hasattr(module, attr):
            return module
    return importlib.import_module("jobhunt.__main__")


@contextlib.contextmanager
def _fake_sources(world: World):
    """One ATS board and one standalone source, each with a new posting and some already tracked."""
    from jobhunt.sources import ats

    world.root.joinpath("boards.toml").write_text(
        '[[board]]\nname = "Fernhill Analytics"\nats = "greenhouse"\nslug = "fernhill"\n', encoding="utf-8")
    tracked = [j for j in jobs() if j["company"] == "Fernhill Analytics"]
    fresh = {**tracked[0], "id": "greenhouse:fernhill-platform", "title": "Graduate Platform Engineer"}
    hn_job = {**jobs()[0], "id": "hn:willowmere-1", "source": "hn", "company": "Willowmere Bio",
              "title": "Junior Software Engineer", "location": "Cambridge", "board_size": None}

    def board(slug, name):
        return [dict(j) for j in (*tracked, fresh)], 3

    with mock.patch.dict(ats.FETCHERS, {"greenhouse": board}), \
            mock.patch.dict(_command_module("STANDALONE").STANDALONE, {"hn": lambda: [dict(hn_job)]}, clear=True):
        yield


def run(argv: list[str], world: World) -> str:
    """What `python -m jobhunt <argv>` prints in `world`: stdout, then stderr and the exit code when present."""
    main = importlib.import_module("jobhunt.__main__").main
    out, err = io.StringIO(), io.StringIO()
    code = 0
    env = {"COLUMNS": HELP_WIDTH, "NO_COLOR": "1", "PYTHON_COLORS": "0"}
    with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["jobhunt", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), warnings.catch_warnings():
        # The commands leave their connection for the process exit to close, which a CLI run never reports.
        warnings.simplefilter("ignore", ResourceWarning)
        try:
            main()
        except SystemExit as e:
            # As the interpreter does: a message is printed to stderr and exits 1.
            if isinstance(e.code, str):
                print(e.code, file=sys.stderr)
                code = 1
            else:
                code = e.code or 0
        gc.collect()
    text = f"$ jobhunt {' '.join(argv)}\n{out.getvalue()}"
    if err.getvalue():
        text += f"--- stderr\n{err.getvalue()}"
    if code:
        text += f"--- exit {code}\n"
    return normalise(text, world.root)


def output(case: Case) -> str:
    """The normalised output of `case`, run in a fresh made-up world."""
    with World(**case.world) as world:
        if case.fake_sources:
            with _fake_sources(world):
                return run(case.argv, world)
        return run(case.argv, world)


def golden_path(case: Case) -> Path:
    """Where the golden output of `case` is stored."""
    return GOLDENS / f"{case.name}.txt"

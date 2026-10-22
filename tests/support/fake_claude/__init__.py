"""Puts the made-up Claude Code command line (`claude`, `claude.cmd` in this folder) first on PATH for a test.

`with on_path(tmp, mode="limited_at") as fake:` sets `FAKE_CLAUDE_MODE`, a log file and a sign-in state
file inside `tmp`; `fake.calls()` returns each logged call. Only this folder's command line may run while
`HAL_TEST_MODE=1` (`jobhunt.ai.claude_cli` refuses any other), so a test can never spend a real allowance.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

FOLDER = Path(__file__).resolve().parent


@dataclass
class FakeClaude:
    """The fake's files for one test."""

    log: Path
    state: Path

    def calls(self) -> list[dict]:
        """Every call the fake answered, oldest first."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]


@contextlib.contextmanager
def on_path(tmp: Path, mode: str = "ok", *, signed_in: bool = True, **env: str) -> Iterator[FakeClaude]:
    """The fake first on PATH, in `mode`, with its log and state under `tmp`."""
    fake = FakeClaude(log=Path(tmp) / "fake-claude.log", state=Path(tmp) / "fake-claude-signed-in")
    if signed_in:
        fake.state.touch()
    values = {"PATH": f"{FOLDER}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_CLAUDE_MODE": mode,
              "FAKE_CLAUDE_LOG": str(fake.log), "FAKE_CLAUDE_STATE": str(fake.state),
              "FAKE_CLAUDE_PYTHON": sys.executable, **env}
    with mock.patch.dict(os.environ, values):
        yield fake

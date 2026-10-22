"""The "Claude subscription" backend: the Claude Code command line, signed in with the person's own Pro or Max plan.

Each call runs `claude -p --output-format json` with tools off, no settings files and no saved session. The
prompt goes on stdin and the system prompt in a file, because Windows limits a command line to about 32,000
characters and prompts are editable and can grow. The file has a random name in `claude-work/` and is deleted
after the call, also when it fails. Claude runs from that empty folder, so it never sees Hal's files.

The command line's failures become `LLMUnavailable` kinds with words for the person; none of them
tells the person to open a command prompt. A limit message that names its reset time ("resets at 3pm",
"reset at 15:40", UK time) pauses background work until then, else for an hour (`budget`).

In a test run (`HAL_TEST_MODE=1`) only the fake command line in `tests/support/fake_claude/` may run, unless
`HAL_TEST_REAL_AI=claude` asks for a real call: a test must never spend the person's allowance by accident.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..core import clock, paths
from .base import ChatRequest, ChatResult, Limited, LLMUnavailable, Status

log = logging.getLogger(__name__)

MODELS = ("opus", "sonnet", "haiku")
DEFAULT_TIMEOUT_S = 300
FAKE_MARKER = "FAKE_CLAUDE_MODE"        # set by tests that put the fake command line on PATH
REAL_AI_ENV = "HAL_TEST_REAL_AI"
GIT_BASH_ENV = "CLAUDE_CODE_GIT_BASH_PATH"
MIDDAY = 12

SIGNED_OUT = "Hal is not signed in to Claude. Sign in again."
FREE_PLAN = "Your Claude plan cannot run Claude Code. It needs Claude Pro or Max."
NOT_INSTALLED = "Claude Code is not installed yet."
UNREADABLE = "Claude sent an answer Hal could not read."

# Checked in this order, case-insensitively, against everything the command line printed.
_SIGNED_OUT = re.compile(r"not logged in|log ?in|/login|\bauth|credential", re.I)
_FREE_PLAN = re.compile(r"free plan|not available (on|with|for) your (plan|subscription)", re.I)
_LIMIT = re.compile(r"limit|usage|quota|\b429\b", re.I)
_SUBSCRIPTION = re.compile(r"subscription", re.I)
_TIMED_OUT = re.compile(r"timed out", re.I)
_NOT_FOUND = re.compile(r"command not found|not recognized as an internal", re.I)
_RESET = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", re.I)


def executable() -> str | None:
    """The `claude` command: on PATH, else in `~/.local/bin` where its installer puts it."""
    found = shutil.which("claude")
    if found:
        return found
    name = "claude.exe" if sys.platform == "win32" else "claude"
    local = Path.home() / ".local" / "bin" / name
    return str(local) if local.is_file() else None


def _allowed_in_tests(exe: str) -> None:
    if os.environ.get(paths.ENV_TEST) != "1" or os.environ.get(REAL_AI_ENV) == "claude":
        return
    if not os.environ.get(FAKE_MARKER) or Path(exe).parent.name != "fake_claude":
        raise LLMUnavailable("Tests may run only the fake Claude command line.", kind="not_installed")


def reset_time(text: str, now: datetime | None = None) -> datetime | None:
    """The UTC time a limit message says it resets ("resets at 3pm", "reset at 15:40"), next after `now`."""
    m = _RESET.search(text or "")
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if half == "pm" and hour < MIDDAY:
        hour += MIDDAY
    elif half == "am" and hour == MIDDAY:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    from .budget import _london

    zone = _london()
    local = (now or clock.now_utc()).astimezone(zone)
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return at.astimezone(UTC)


def classify(text: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> LLMUnavailable:
    """The error for what the command line said when it failed."""
    if _SIGNED_OUT.search(text) and not _LIMIT.search(text):
        return LLMUnavailable(SIGNED_OUT, kind="signed_out")
    if _FREE_PLAN.search(text):
        return LLMUnavailable(FREE_PLAN, kind="free_plan")
    if _LIMIT.search(text):
        until = reset_time(text)
        from .budget import limit_words

        return Limited(limit_words(until), until=until)
    if _SUBSCRIPTION.search(text):
        return LLMUnavailable(FREE_PLAN, kind="free_plan")
    if _TIMED_OUT.search(text):
        return LLMUnavailable(_timeout_words(timeout_s), kind="timeout")
    if _NOT_FOUND.search(text):
        return LLMUnavailable(NOT_INSTALLED, kind="not_installed")
    return LLMUnavailable("Claude could not answer just now.")


def _timeout_words(timeout_s: float) -> str:
    minutes = round(timeout_s / 60)
    if minutes >= 1:
        return f"Claude did not answer within {minutes} minute{'s' if minutes != 1 else ''}."
    seconds = max(1, round(timeout_s))
    return f"Claude did not answer within {seconds} second{'s' if seconds != 1 else ''}."


def _git_bash() -> str | None:
    """Git Bash from the bundled PortableGit that install.json names, which some Windows versions need."""
    try:
        info = json.loads((paths.state_dir() / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    git = info.get("git") if isinstance(info, dict) else None
    if not git:
        return None
    for folder in Path(git).parents:
        bash = folder / "bin" / "bash.exe"
        if bash.is_file():
            return str(bash)
    return None


class ClaudeCLIBackend:
    """Claude through the person's own subscription."""

    name = "claude_cli"
    online = True
    local_gpu = False

    def command(self, exe: str, model: str, system_file: Path) -> list[str]:
        """The command line for one call."""
        return [exe, "-p", "--output-format", "json", "--model", model, "--system-prompt-file", str(system_file),
                "--tools", "", "--setting-sources", "", "--no-session-persistence"]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if sys.platform == "win32" and GIT_BASH_ENV not in env:
            bash = _git_bash()
            if bash:
                env[GIT_BASH_ENV] = bash
        return env

    def chat(self, req: ChatRequest, model: str) -> ChatResult:
        """Runs one call and returns Claude's reply."""
        exe = executable()
        if exe is None:
            raise LLMUnavailable(NOT_INSTALLED, kind="not_installed")
        _allowed_in_tests(exe)
        work = paths.claude_work_dir()
        work.mkdir(parents=True, exist_ok=True)
        system_file = work / f"system-{secrets.token_hex(8)}.txt"
        timeout = req.timeout or DEFAULT_TIMEOUT_S
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        start = time.monotonic()
        try:
            system_file.write_text(req.system, encoding="utf-8")
            done = subprocess.run(self.command(exe, model, system_file), input=req.user, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", cwd=work, env=self._env(),
                                  timeout=timeout, creationflags=flags)
        except subprocess.TimeoutExpired as e:
            raise LLMUnavailable(_timeout_words(timeout), kind="timeout") from e
        except OSError as e:
            raise LLMUnavailable(NOT_INSTALLED, kind="not_installed") from e
        finally:
            system_file.unlink(missing_ok=True)
        ms = int((time.monotonic() - start) * 1000)
        return ChatResult(text=self._reply(done, timeout), provider=self.name, model=model, ms=ms)

    def _reply(self, done: subprocess.CompletedProcess, timeout: float) -> str:
        said = f"{done.stdout}\n{done.stderr}"
        try:
            out = json.loads(done.stdout)
        except ValueError:
            if done.returncode != 0:
                raise classify(said, timeout_s=timeout) from None
            raise LLMUnavailable(UNREADABLE) from None
        if not isinstance(out, dict):
            raise LLMUnavailable(UNREADABLE)
        if out.get("is_error") or out.get("subtype") != "success" or done.returncode != 0:
            raise classify(f"{out.get('result') or ''}\n{out.get('subtype') or ''}\n{done.stderr}", timeout_s=timeout)
        result = out.get("result")
        if not isinstance(result, str):
            raise LLMUnavailable(UNREADABLE)
        return result

    def status(self) -> Status:
        """Ready when the command line is installed and no limit holds; sign-in is checked by its setup step."""
        if executable() is None:
            return Status(ready=False, why=NOT_INSTALLED, fix="install_claude")
        from .budget import limit_words, limited_until

        try:
            until = limited_until(self.name)
        except Exception:
            log.exception("could not read Claude's limit")
            until = None
        if until is not None:
            return Status(ready=True, why=limit_words(until), limited_until=until)
        return Status(ready=True)

    def models(self) -> list[str]:
        """Claude's model families, best first."""
        return list(MODELS)

    def default_model(self, role: str) -> str:
        """Haiku for quick jobs, Sonnet for the rest."""
        return "haiku" if role == "quick" else "sonnet"

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Not offered: record search by meaning needs a local model."""
        raise LLMUnavailable("Searching by meaning needs the AI on this computer.")

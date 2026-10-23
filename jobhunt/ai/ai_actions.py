"""The AI area's actions: install Claude Code, sign in to Claude, and test the AI Hal uses.

Each is slow (a download, a person signing in, an AI answering), so the routes in `ai/routes.py` only start
them and the page follows their steps. A failed test is still a finished action whose result says what went
wrong in plain words, because the page shows that sentence beside the Test button rather than an error.

G9 runs every kind from its example in the made-up world: the examples put the made-up Claude Code command
line first on PATH for the life of that world, and stand a made-up installer in for Anthropic's script.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

from ..core import paths, runner
from . import claude_setup
from .base import LLMUnavailable

INSTALL = "ai.claude_install"
SIGN_IN = "ai.claude_sign_in"
TEST = "ai.test"
TEST_SYSTEM = "Reply with the single word ready."
TEST_USER = "Are you ready?"
TEST_PURPOSE = "ai_test"
FAKE_FOLDER = ("tests", "support", "fake_claude")


def _fake_claude(world: Any, *, signed_in: bool = True) -> None:
    """Puts the made-up Claude Code first on PATH, signed in or not, until the made-up world closes."""
    from unittest import mock

    folder = paths.code_dir().joinpath(*FAKE_FOLDER)
    state = world.root / "fake-claude-signed-in"
    if signed_in:
        state.touch()
    world._stack.enter_context(mock.patch.dict(os.environ, {
        "PATH": f"{folder}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_CLAUDE_MODE": "ok",
        "FAKE_CLAUDE_STATE": str(state), "FAKE_CLAUDE_PYTHON": sys.executable, "FAKE_CLAUDE_LOGIN_S": "0"}))
    world._stack.callback(claude_setup.forget)
    claude_setup.forget()


def _install_example(world: Any) -> dict:
    from unittest import mock

    fake = paths.code_dir().joinpath(*FAKE_FOLDER, "claude")

    def installer(args, **kw):
        _fake_claude(world)
        return subprocess.Popen([sys.executable, str(fake), "install"], **kw)

    world._stack.enter_context(mock.patch.object(claude_setup, "_popen", installer))
    world._stack.enter_context(mock.patch.object(claude_setup, "_windows", lambda: False))
    return {"button": "Install"}


def _sign_in_example(world: Any) -> dict:
    _fake_claude(world, signed_in=True)
    return {"button": "Sign in"}


@runner.action(INSTALL, area="settings", label="Installing Claude", why="You pressed {button}", priority=0,
               exclusive="kind", example=_install_example)
def install_claude(ctx: runner.Ctx, button: str = "Install") -> dict:
    """Installs Claude Code for this person, with no admin prompt; one step per installer line."""
    ctx.step("Installing Claude (this usually takes about a minute)")
    found = claude_setup.version()
    if found:
        ctx.step(f"Claude is already installed (version {found})")
        return {"ok": True, "version": found}
    found = claude_setup.install(ctx.step, ctx.checkpoint)
    ctx.changed(f"Installed Claude Code {found} for you.")
    return {"ok": True, "version": found}


@runner.action(SIGN_IN, area="settings", label="Signing in to Claude", why="You pressed {button}", priority=0,
               exclusive="kind", example=_sign_in_example)
def sign_in_claude(ctx: runner.Ctx, button: str = "Sign in") -> dict:
    """Opens Claude's own sign-in and waits until Claude Code says this person is signed in."""
    if ctx.remote:
        raise RuntimeError("Sign in on the computer Hal runs on: the sign-in window opens there.")
    acct = claude_setup.sign_in(ctx.step, ctx.checkpoint)
    ctx.changed(claude_setup.account_words(acct) + ".")
    return {"ok": True, "account": claude_setup.account_words(acct), "plan": acct.plan,
            "plan_ok": not claude_setup.free_plan(acct.plan)}


def _seconds_words(seconds: float) -> str:
    return f"{seconds:.1f} second{'' if seconds == 1 else 's'}"


def _claude_ready() -> str | None:
    """Plain words for why Claude cannot be tested yet, or None when it can."""
    acct = claude_setup.auth_status(fresh=True)
    if acct is None:
        return claude_setup.NOT_INSTALLED
    if not acct.signed_in:
        return claude_setup.SIGNED_OUT
    if claude_setup.free_plan(acct.plan):
        return claude_setup.FREE_PLAN
    return None


@runner.action(TEST, area="settings", label="Testing Hal's AI", why="You pressed {button}", priority=0,
               exclusive="kind", example=lambda world: {"button": "Test now"})
def test_ai(ctx: runner.Ctx, button: str = "Test") -> dict:
    """Asks the AI Hal uses one short question: {ok, seconds, words}."""
    from .. import ai

    ctx.step("Checking which AI Hal uses")
    be = ai.backend()
    claude = be.name == "claude_cli"
    if claude:
        why_not = _claude_ready()
        if why_not:
            return {"ok": False, "seconds": None, "words": why_not}
    ctx.step("Asking the AI a short question")
    start = time.monotonic()
    try:
        ai.chat(TEST_SYSTEM, TEST_USER, role="quick", purpose=TEST_PURPOSE, priority=0)
    except LLMUnavailable as e:
        return {"ok": False, "seconds": None, "words": str(e), "kind": e.kind}
    seconds = round(time.monotonic() - start, 1)
    who = "Claude" if claude else "The AI"
    words = f"{who} answered in {_seconds_words(seconds)}"
    ctx.changed(words + ".")
    return {"ok": True, "seconds": seconds, "words": words}

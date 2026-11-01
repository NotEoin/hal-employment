"""Routes for Hal's AI: which AI fits, whether it is ready, Claude's install and sign-in, Test, model lists.

Install, sign-in and Test start actions (`ai/ai_actions.py`) and return their id at once. Sign-in and sign-out
are refused in a window opened from another computer, because the sign-in window would open on the computer
Hal runs on, where nobody is looking. The allowance is counts Hal can know (`budget.today`), never a share of
Claude's limit, which Claude Code does not report.
"""
from __future__ import annotations

from typing import Any

from ..core import clock, runner
from ..web.router import Conflict, Request, route
from . import ai_actions, budget, claude_setup, health
from .base import LLMUnavailable

CLAUDE_MODELS = {"writer": ["opus", "sonnet", "haiku"], "checker": ["opus", "sonnet", "haiku"],
                 "quick": ["sonnet", "haiku"]}   # quick jobs never need Opus


def _provider() -> str:
    from .health import chosen

    return chosen()


def _start(req: Request, kind: str, button: str, busy: str) -> dict:
    try:
        aid = runner.start(kind, subject=kind, params={"button": button},
                           session_id=getattr(req.session, "id_hash", None), remote=req.remote)
    except RuntimeError as e:
        raise Conflict(busy) from e
    return {"action_id": aid}


def _host(req: Request) -> str | None:
    if not req.remote:
        return None
    return (req.session.remote_host if req.session else None) or "the computer Hal runs on"


def _hal_available(name: str) -> bool:
    """True when Hal's model gateway answers, so setup can offer it; false without probing when it is in use."""
    from ..core import settings
    from ..core.settings.catalog.ai import hal_answers

    return name == "hal" or hal_answers(settings.get)


def claude_status(fresh: bool = False) -> dict:
    """Claude Code's part of the status: installed, version, signed in, account words, plan and why not."""
    acct = claude_setup.auth_status(fresh=fresh)
    installed = acct is not None
    out: dict[str, Any] = {"installed": installed, "version": claude_setup.version() if installed else None,
                           "signed_in": bool(acct and acct.signed_in), "account": claude_setup.account_words(acct),
                           "email": acct.email if acct else None,
                           "plan": claude_setup.plan_words(acct.plan if acct else None),
                           "plan_ok": not (acct and claude_setup.free_plan(acct.plan))}
    if not installed:
        out.update(why=claude_setup.NOT_INSTALLED, fix="install_claude")
    elif not out["signed_in"]:
        out.update(why=claude_setup.SIGNED_OUT, fix="claude_sign_in")
    elif not out["plan_ok"]:
        out.update(why=claude_setup.FREE_PLAN, fix="")
    return out


@route("GET", "/api/v2/ai/status", slow=True)
def status(req: Request) -> dict:
    """Is the AI ready: {provider, installed, version, signed_in, account, plan_ok, ready, why, fix,
    used_today, background_today, cap, limited_until, allowance, remote_host, mode, hal_available}."""
    from .. import ai

    name = _provider()
    fresh = req.query.get("fresh") == "1"
    out: dict[str, Any] = {"provider": name, "installed": None, "version": None, "signed_in": None,
                           "account": None, "plan": None, "plan_ok": None, "why": "", "fix": ""}
    if name == "claude_cli":
        out.update(claude_status(fresh))
        out["ready"] = not out["why"]
    else:
        st = ai.backend().status()
        out.update(ready=st.ready, why="" if st.ready else st.why, fix="" if st.ready else st.fix)
    t = budget.today(name)
    if out["ready"] and t["limited_until"]:
        out["why"] = budget.limit_words(clock.parse(t["limited_until"]))
    out.update(used_today=t["calls"], background_today=t["background"], cap=t["cap"],
               limited_until=t["limited_until"], allowance=budget.words(name) if name == "claude_cli" else "",
               remote_host=_host(req), mode=health.mode_words(name), hal_available=_hal_available(name))
    return out


@route("GET", "/api/v2/ai/recommend", slow=True)
def recommend(req: Request) -> dict:
    """Which AI fits this computer: {pick, why, machine: {memory_gb, gpu, laptop}}."""
    from ..core import platform, settings
    from ..core.settings.catalog.ai import ollama_answers

    found = platform.current().machine()
    machine = {"memory_gb": found.get("memory_gb"), "gpu": found.get("gpu"), "laptop": claude_setup.has_battery()}
    running = bool(machine["gpu"]) and ollama_answers(settings.get("ai.ollama_url"))
    return claude_setup.recommend(machine, local_running=running)


@route("POST", "/api/v2/ai/claude/install")
def install(req: Request) -> dict:
    """Starts installing Claude Code (no admin prompt)."""
    return _start(req, ai_actions.INSTALL, "Install", "Hal is already installing Claude.")


@route("POST", "/api/v2/ai/claude/sign-in", local_only=True)
def sign_in(req: Request) -> dict:
    """Opens Claude's own sign-in window and follows it until Claude Code says signed in."""
    return _start(req, ai_actions.SIGN_IN, "Sign in", "The Claude sign-in window is already open.")


@route("POST", "/api/v2/ai/claude/sign-out", local_only=True)
def sign_out(req: Request) -> dict:
    """Signs Claude Code out on this computer."""
    try:
        claude_setup.sign_out()
    except RuntimeError as e:
        raise Conflict(str(e)) from e
    return {"ok": True}


@route("POST", "/api/v2/ai/test")
def test(req: Request) -> dict:
    """Starts the AI test: one short question; the action's result is {ok, seconds, words}."""
    return _start(req, ai_actions.TEST, "Test now", "Hal is already testing its AI.")


@route("GET", "/api/v2/ai/models", slow=True)
def models(req: Request) -> dict:
    """The model choices for each role of the AI in use: {writer, checker, quick}."""
    from .. import ai

    if _provider() == "claude_cli":
        return {role: list(names) for role, names in CLAUDE_MODELS.items()}
    try:
        names = [m for m in ai.backend().models() if m]
    except (LLMUnavailable, OSError, ValueError):   # a local AI that is not running has no list yet
        names = []
    return {"writer": names, "checker": names, "quick": names}

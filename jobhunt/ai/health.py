"""The AI's line on "Is Hal working?": ready, signed in, limited until.

The check's name says which AI is in use ("AI (Claude subscription)"), so `CHECKS` is built on
each read through the module's `__getattr__` (PEP 562) rather than once at import. The choice is read without
storing it, so looking at the health page never fixes `ai.provider` by itself, and without the
suggestion's look for a local AI, so the page never waits on that probe.
"""
from __future__ import annotations

import sqlite3

from .. import db
from ..core.health import Check, CheckResult, Fix, bad, ok, warn
from . import budget, claude_setup

SETUP_ROUTE = "setup-ai"
NAMES = {"claude_cli": "AI (Claude subscription)", "local": "AI (on this computer)",
         "openai_compat": "AI (another local server)"}


def chosen() -> str:
    """The AI chosen, else the catalogue's plain default; never stores anything and never probes the network."""
    from ..core import settings
    from ..core.settings import store
    from . import provider

    return store.stored(provider.KEY) or settings.setting(provider.KEY).default


def last_answer_ms(provider_name: str) -> int | None:
    """How long the newest good answer from this AI took, in milliseconds."""
    con = db.connect()
    try:
        row = con.execute("SELECT ms FROM ai_usage WHERE provider = ? AND ok = 1 AND ms IS NOT NULL "
                          "ORDER BY id DESC LIMIT 1", (provider_name,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return int(row["ms"]) if row else None


def _answered(name: str) -> str:
    ms = last_answer_ms(name)
    return f" Last answer took {ms / 1000:.1f} seconds." if ms is not None else ""


def claude() -> CheckResult:
    """Installed, signed in, a plan that can run Claude Code, and no limit holding."""
    acct = claude_setup.auth_status()
    if acct is None:
        return bad(claude_setup.NOT_INSTALLED, Fix("Install Claude", route=SETUP_ROUTE))
    if not acct.signed_in:
        return bad(claude_setup.SIGNED_OUT, Fix("Sign in to Claude again", route=SETUP_ROUTE))
    if claude_setup.free_plan(acct.plan):
        return bad(claude_setup.FREE_PLAN)
    until = budget.limited_until("claude_cli")
    if until is not None:
        return warn(budget.limit_words(until))
    return ok("Signed in." + _answered("claude_cli"))


def other(name: str) -> CheckResult:
    """The local AI or another local server: whether it answers."""
    from .. import ai

    st = ai.backend().status()
    if not st.ready:
        return bad(st.why or "Hal's AI is not ready.", Fix("Choose another AI", route="settings-ai"))
    return ok("Ready." + _answered(name))


def ai_check() -> CheckResult:
    """The check for whichever AI is chosen."""
    name = chosen()
    return claude() if name == "claude_cli" else other(name)


def __getattr__(attr: str):
    if attr == "CHECKS":
        return [Check("ai", NAMES.get(chosen(), "AI"), ai_check)]
    raise AttributeError(attr)

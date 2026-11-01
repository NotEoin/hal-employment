"""Settings group "Hal's brain (AI)": which AI Hal uses and how much.

`ai.provider` is the one setting whose default is written once and then never follows the default again:
which AI sees the person's data must not change by itself. Its `default_fn` only suggests a
choice for setup, so it may look for Ollama on this computer, with a short timeout.

Model choices are empty because the list comes from the chosen backend at run time. An empty
model name means "the first local model" (writer and checker) or "the smallest" (quick).
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

from jobhunt.core.settings.catalog._shared import choices, safe, uses_local_ai
from jobhunt.core.settings.registry import Setting, register

OLLAMA_URL = "http://localhost:11434"
OLLAMA_PROBE_TIMEOUT = 2   # seconds; setup waits on this, and a running Ollama answers in milliseconds
PROVIDER_HELP = ("This computer needs a strong graphics card. Claude uses your own subscription: the advert, "
                 "your CV and the evidence Hal picks are sent to Claude when it writes.")
ALLOWANCE_HELP = ("Hal shares your Claude Pro allowance with your own chats. It spends it in this order: things "
                  "you just asked for, documents for jobs you chose, then background checks. Background checks "
                  "pause when Claude says you have reached a limit, and start again when it resets.")
MAX_DAILY_CAP = 100
MAX_PARALLEL = 4
AI_TIMEOUT = 300   # seconds per answer; a local model writing a letter can take minutes


def ollama_answers(url: str = OLLAMA_URL, timeout: float = OLLAMA_PROBE_TIMEOUT) -> bool:
    """True when an Ollama server answers at `url`."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as reply:
            return reply.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def hal_answers(get) -> bool:
    """True when Hal's gateway answers with the stored token; never true in a test run, so no test meets a live Hal."""
    from jobhunt.ai.hal_gateway import DEFAULT_TOKEN_FILE, DEFAULT_URL, gateway_answers

    if os.environ.get("HAL_TEST_MODE") == "1" and not os.environ.get("HAL_TEST_REAL_AI"):
        return False
    return gateway_answers(safe(get, "ai.hal_url") or DEFAULT_URL, safe(get, "ai.hal_token_file") or DEFAULT_TOKEN_FILE)


def suggested_provider(get) -> str:
    """Hal when its gateway answers; else Claude on Windows; on Linux this computer if Ollama answers, else Claude."""
    if hal_answers(get):
        return "hal"
    if sys.platform == "win32":
        return "claude_cli"
    return "local" if ollama_answers(safe(get, "ai.ollama_url") or OLLAMA_URL) else "claude_cli"


def _model(claude: str):
    return lambda get: "" if uses_local_ai(get) else claude


register(
    Setting("ai.provider", "seg", "claude_cli", "How Hal thinks", help=PROVIDER_HELP, order=10,
            default_fn=suggested_provider, choices=choices(
                ("hal", "Hal's local model"), ("local", "This computer"), ("claude_cli", "Claude subscription"),
                ("openai_compat", "Another local server"))),
    Setting("ai.claude_account", "info", None, "Claude account", remote="local_only",
            visible_if="ai.provider == 'claude_cli'", order=20),
    Setting("ai.model.writer", "choice", "sonnet", "For writing letters and CVs", default_fn=_model("sonnet"),
            order=30),
    Setting("ai.model.checker", "choice", "sonnet", "For checking jobs and facts", default_fn=_model("sonnet"),
            order=40),
    Setting("ai.model.quick", "choice", "haiku", "For quick jobs like sorting email", default_fn=_model("haiku"),
            order=50),
    Setting("ai.model.embed", "text", "nomic-embed-text", "For searching your record by meaning", advanced=True,
            visible_if="ai.provider == 'local'", order=60),
    Setting("ai.ollama_url", "url", OLLAMA_URL, "Address of the AI on this computer", advanced=True, order=70),
    # where the owner token is read from and sent to: only someone at this computer may change either
    Setting("ai.hal_url", "url", "http://127.0.0.1:8741/v1", "Address of Hal's model gateway", advanced=True,
            visible_if="ai.provider == 'hal'", remote="local_only", order=75),
    Setting("ai.hal_token_file", "text", "~/.config/hal/gateway-token", "File holding Hal's gateway token",
            advanced=True, visible_if="ai.provider == 'hal'", remote="local_only", order=76),
    Setting("ai.openai_url", "url", "http://localhost:8000/v1", "Address of the other local server", advanced=True,
            visible_if="ai.provider == 'openai_compat'", order=80),
    Setting("ai.openai_key", "secret", None, "Key for the other local server", secret_name="ai.openai_key",
            advanced=True, visible_if="ai.provider == 'openai_compat'", order=90),
    Setting("ai.allowance", "info", None, "Your Claude allowance", help=ALLOWANCE_HELP,
            visible_if="ai.provider == 'claude_cli'", order=100),
    Setting("ai.daily_cap", "range", 30, "Most background AI checks a day", min=0, max=MAX_DAILY_CAP, unit="checks",
            order=110),
    Setting("ai.keep_free", "hours", None, "Keep Claude free for me during", order=120,
            help="Hal does no background AI work in these hours."),
    Setting("ai.local_only", "bool", False, "Never use online AI", order=130,
            help="Keeps everything on this computer. Needs \"Hal's local model\" or \"This computer\"."),
    Setting("ai.parallel", "int", 2, "AI calls at once", advanced=True, min=1, max=MAX_PARALLEL, order=140,
            default_fn=lambda get: 1 if uses_local_ai(get) else 2),
    Setting("ai.timeout", "int", AI_TIMEOUT, "Seconds to wait for an answer", advanced=True, min=10, order=150),
    Setting("ai.gpu_gate", "bool", True, "Wait while the graphics card is busy", advanced=True,
            visible_if="ai.provider == 'local'", order=160),
    Setting("ai.gpu_ignore", "tags", [], "Programs that do not count as busy", advanced=True,
            visible_if="ai.provider == 'local'", order=170),
    Setting("ai.test", "button", None, "Check that Hal's AI works", action="ai.test", help="Test now", order=180),
)

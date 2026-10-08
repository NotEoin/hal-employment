"""Local model access through Ollama, configured by Hal's hal.conf.

Models are chosen by role (main, fast, writer), so a change to Hal's model
lineup applies here too. If Ollama is unreachable, calls raise LLMUnavailable
and callers fall back to rules.
"""
import json
import re
import shlex
import urllib.request
from pathlib import Path

from .config import CONFIG

HAL_ROOT = Path(CONFIG.get("hal_root", "~/hal")).expanduser()


class LLMUnavailable(Exception):
    pass


def hal_conf() -> dict:
    conf = {}
    p = HAL_ROOT / "hal.conf"
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"^\s*(HAL_\w+)=(.*)$", line)
            if m:
                conf[m.group(1)] = (shlex.split(m.group(2)) or [""])[0]
    return conf


# hal.conf keys for each role; old-home/models/build-roles.sh sets them.
ROLES = {"main": "HAL_MODEL", "fast": "HAL_FAST_MODEL", "writer": "HAL_WRITER_MODEL"}


def _settings(role: str = "main"):
    c = hal_conf()
    url = CONFIG.get("ollama_url") or c.get("HAL_OLLAMA_URL") or "http://localhost:11434"
    model = CONFIG.get(f"{role}_model") or c.get(ROLES[role]) or c.get("HAL_MODEL")
    if not model:
        raise LLMUnavailable("no local model configured (Hal cloud profile, and no ollama_model in config.toml)")
    return url.rstrip("/"), model


def chat(system: str, user: str, *, role: str = "main", json_out: bool = False, timeout: float = 180) -> str:
    url, model = _settings(role)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "think": False,
    }
    if json_out:                    # extraction wants determinism; prose keeps the model's own settings
        payload["format"] = "json"
        payload["options"] = {"temperature": 0.1}
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["message"]["content"]
    except OSError as e:
        raise LLMUnavailable(f"Ollama at {url}: {e}") from e


def chat_json(system: str, user: str, **kw) -> dict:
    out = chat(system, user, json_out=True, **kw)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", out, re.S)
        return json.loads(m.group(0)) if m else {}


def available() -> bool:
    try:
        url, _ = _settings()
        urllib.request.urlopen(f"{url}/api/tags", timeout=3)
        return True
    except (OSError, LLMUnavailable):
        return False

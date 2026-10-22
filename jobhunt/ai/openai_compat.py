"""The "Another local server" backend: any server that speaks the OpenAI chat format.

vLLM, LM Studio and llama.cpp all answer `POST /chat/completions` and `GET /models`. Whether text leaves the
computer is decided by the address, not by a setting the person might forget: a loopback or private address
is local (and on loopback this computer's graphics card is the one in use, so the GPU gate applies);
anything else is online, so `ai.local_only` refuses it. Some servers reject `response_format`; a refused
JSON request is tried once more without it.
"""
from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import ChatRequest, ChatResult, LLMUnavailable, Status

STATUS_TIMEOUT_S = 3
DEFAULT_TIMEOUT_S = 300
JSON_TEMPERATURE = 0.1
PROSE_TEMPERATURE = 0.7
REFUSED_FORMAT = (400, 422)     # what servers without JSON mode answer to `response_format`
LOOPBACK_NAMES = ("localhost",)


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def classify(url: str) -> tuple[bool, bool]:
    """(online, local_gpu) for a server address: loopback (False, True), private (False, False), else online."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in LOOPBACK_NAMES or host.endswith(".localhost"):
        return False, True
    addr = _address(host)
    if addr is None and host.endswith(".local"):
        return False, False
    if addr is None:
        return True, False
    if addr.is_loopback:
        return False, True
    return (not (addr.is_private or addr.is_link_local)), False


class OpenAICompatBackend:
    """A local server with the OpenAI chat API at `ai.openai_url`."""

    name = "openai_compat"

    def __init__(self, url: str, key: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.key = key
        self.online, self.local_gpu = classify(self.url)

    def _request(self, method: str, path: str, payload: dict | None, timeout: float) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as reply:
                return json.loads(reply.read())
        except TimeoutError as e:
            raise LLMUnavailable(f"The local server did not answer within {int(timeout)} seconds.",
                                 kind="timeout") from e
        except urllib.error.HTTPError:
            raise
        except (OSError, ValueError) as e:
            raise LLMUnavailable(f"Hal cannot reach the local server at {self.url}.") from e

    def chat(self, req: ChatRequest, model: str) -> ChatResult:
        """Asks `model` through /chat/completions."""
        payload: dict = {"model": model, "temperature": JSON_TEMPERATURE if req.json_out else PROSE_TEMPERATURE,
                         "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}]}
        if req.json_out:
            payload["response_format"] = {"type": "json_object"}
        timeout = req.timeout or DEFAULT_TIMEOUT_S
        start = time.monotonic()
        try:
            out = self._request("POST", "/chat/completions", payload, timeout)
        except urllib.error.HTTPError as e:
            if not (req.json_out and e.code in REFUSED_FORMAT):
                raise LLMUnavailable(f"The local server refused the request ({e.code}).") from e
            payload.pop("response_format")
            try:
                out = self._request("POST", "/chat/completions", payload, timeout)
            except urllib.error.HTTPError as again:
                raise LLMUnavailable(f"The local server refused the request ({again.code}).") from again
        try:
            text = out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMUnavailable("The local server sent an answer Hal could not read.") from e
        return ChatResult(text=text or "", provider=self.name, model=model, ms=int((time.monotonic() - start) * 1000))

    def models(self) -> list[str]:
        """The server's models; empty when it does not answer."""
        try:
            out = self._request("GET", "/models", None, STATUS_TIMEOUT_S)
        except (LLMUnavailable, urllib.error.HTTPError):
            return []
        return [m["id"] for m in out.get("data", []) if isinstance(m, dict) and m.get("id")]

    def default_model(self, role: str) -> str:
        """The server's first model; such servers usually hold one."""
        found = self.models()
        if not found:
            raise LLMUnavailable(f"Hal cannot reach the local server at {self.url}.")
        return found[0]

    def status(self) -> Status:
        """Ready when /models answers."""
        try:
            self._request("GET", "/models", None, STATUS_TIMEOUT_S)
        except urllib.error.HTTPError as e:
            return Status(ready=False, why=f"The local server answered {e.code}.")
        except LLMUnavailable as e:
            return Status(ready=False, why=str(e))
        return Status(ready=True)

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Not offered: record search by meaning uses Ollama."""
        raise LLMUnavailable("Searching by meaning needs the AI on this computer (Ollama).")

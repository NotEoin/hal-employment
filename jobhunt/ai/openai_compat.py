"""The "Another local server" backend: any server that speaks the OpenAI chat format.

vLLM, LM Studio and llama.cpp all answer `POST /chat/completions` and `GET /models`. Whether text leaves the
computer is decided by the address, not by a setting the person might forget: a loopback or private address
is local (and on loopback this computer's graphics card is the one in use, so the GPU gate applies);
anything else is online, so `ai.local_only` refuses it. Some servers reject `response_format` or a backend's
`EXTRA` fields; a refused request is tried again without the extras, then without `response_format`.

Every request caps its answer at `MAX_TOKENS`: one CV pack prompt made Gemma 4 write until its 48K context was
full, 508 s and 594 s, so the pack timed out (2026-11-12). An answer cut off at the cap (`finish_reason`
"length") is never used: it is asked once more at `RETRY_TEMPERATURE`, as a loop at a low temperature tends
to repeat, then refused in plain words.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, ClassVar

from .base import ChatRequest, ChatResult, LLMUnavailable, Status

log = logging.getLogger(__name__)

STATUS_TIMEOUT_S = 3
DEFAULT_TIMEOUT_S = 300
JSON_TEMPERATURE = 0.1
PROSE_TEMPERATURE = 0.7
REFUSED_FORMAT = (400, 422)     # what servers without JSON mode answer to `response_format`
LOOPBACK_NAMES = ("localhost",)
REASON_CHARS = 200              # the server's own reason, shown to the person, stays one line
# Thinking plus answer: the longest normal reply measured was about 3,000 thinking tokens and a 3,000-token CV;
# at the 78-93 tok/s Hal's writer gives, the cap ends a runaway in about 3 minutes, inside the 300 s wait.
MAX_TOKENS = 16384
RETRY_TEMPERATURE = 0.7         # a second try at an answer that ran on
TRIES = 2                       # an answer that ran on is asked once more, never more
RAN_ON = ("The AI's answer ran on without finishing, twice. Try again; if it keeps happening, choose another "
          "model in Settings, Hal's brain.")


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
    EXTRA: ClassVar[dict[str, int | str]] = {}    # fields a backend adds to every request, dropped when refused

    def __init__(self, url: str, key: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.key = key
        self.online, self.local_gpu = classify(self.url)

    def _open(self, req: urllib.request.Request, timeout: float) -> Any:
        return urllib.request.urlopen(req, timeout=timeout)

    def _request(self, method: str, path: str, payload: dict | None, timeout: float) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with self._open(req, timeout) as reply:
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
        default = JSON_TEMPERATURE if req.json_out else PROSE_TEMPERATURE
        payload: dict = {"model": model, "temperature": default if req.temperature is None else req.temperature,
                         "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}]}
        if req.json_out:
            payload["response_format"] = {"type": "json_object"}
        payload.update({"max_tokens": MAX_TOKENS, **self.EXTRA})
        # what a refusing server is asked again without, one step at a time: the backend's extras first
        optional = [keys for keys in (tuple(self.EXTRA), ("response_format",)) if keys and keys[0] in payload]
        timeout = req.timeout or DEFAULT_TIMEOUT_S
        start = time.monotonic()
        for attempt in range(TRIES):
            out = self._post_chat(payload, optional, timeout)
            try:
                choice = out["choices"][0]
                text = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise LLMUnavailable("The local server sent an answer Hal could not read.") from e
            if choice.get("finish_reason") != "length":
                return ChatResult(text=text or "", provider=self.name, model=model,
                                  ms=int((time.monotonic() - start) * 1000))
            log.warning("%s ran on to %d tokens (try %d)", model, MAX_TOKENS, attempt + 1)
            payload["temperature"] = max(payload["temperature"], RETRY_TEMPERATURE)
        raise LLMUnavailable(RAN_ON)

    def _post_chat(self, payload: dict, optional: list[tuple[str, ...]], timeout: float) -> dict:
        """The server's reply to `payload`; a 400 or 422 is asked again without each `optional` step in turn
        (the steps are used up, so a retry of the whole chat keeps what was dropped)."""
        while True:
            try:
                return self._request("POST", "/chat/completions", payload, timeout)
            except urllib.error.HTTPError as e:
                if not (optional and e.code in REFUSED_FORMAT):
                    raise _refused(e) from e
                for k in optional.pop(0):
                    payload.pop(k)

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


def _refused(e: urllib.error.HTTPError) -> LLMUnavailable:
    """The error for a refused request, with the server's own reason when it gives one.

    Hal's gateway answers 503 with "Hal is off; run `hal on`" after a restart, which a bare code hid
    (2026-11-06).
    """
    try:
        error = json.loads(e.read() or b"{}").get("error")
        why = str(error.get("message") if isinstance(error, dict) else error or "")
    except (OSError, ValueError, AttributeError):
        why = ""
    why = " ".join(why.split())[:REASON_CHARS]
    return LLMUnavailable(f"The local server refused the request ({e.code})" + (f": {why}" if why else "."))

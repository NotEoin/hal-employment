"""The "This computer" backend: Ollama's HTTP API on `ai.ollama_url`.

From the old `llm.chat`: `think` is off, and a JSON request asks for `format: "json"` at temperature 0.1
(extraction wants determinism; prose keeps the model's own settings unless the request names a temperature).
`num_ctx` is sent only when a caller asks for a wider window. Nothing leaves this computer, so `online` is
false and the GPU gate applies.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import ChatRequest, ChatResult, LLMUnavailable, Status

STATUS_TIMEOUT_S = 3            # a running Ollama answers /api/tags in milliseconds
DEFAULT_TIMEOUT_S = 300
JSON_TEMPERATURE = 0.1
EMBED_HINT = "embed"            # embedding models cannot chat, so they are never picked for a role
NOT_RUNNING = "Hal cannot reach the AI on this computer. Start Ollama, or choose another AI in Settings."


class OllamaBackend:
    """Ollama on this computer, or on the address in `ai.ollama_url`."""

    name = "local"
    online = False
    local_gpu = True

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def _post(self, path: str, payload: dict, timeout: float) -> dict:
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as reply:
                return json.loads(reply.read())
        except TimeoutError as e:
            raise LLMUnavailable(f"The AI on this computer did not answer within {int(timeout)} seconds.",
                                 kind="timeout") from e
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise LLMUnavailable(f"The AI on this computer refused the request: {detail or e.reason}") from e
        except (OSError, ValueError) as e:
            raise LLMUnavailable(NOT_RUNNING) from e

    def _tags(self, timeout: float = STATUS_TIMEOUT_S) -> list[dict]:
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=timeout) as reply:
                return json.loads(reply.read()).get("models", [])
        except (OSError, ValueError) as e:
            raise LLMUnavailable(NOT_RUNNING) from e

    def chat(self, req: ChatRequest, model: str) -> ChatResult:
        """Asks `model` through /api/chat."""
        payload: dict = {"model": model, "stream": False, "think": False,
                         "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}]}
        if req.json_out:
            payload["format"] = "json"
            payload["options"] = {"temperature": JSON_TEMPERATURE}
        if req.temperature is not None:
            payload["options"] = {**payload.get("options", {}), "temperature": req.temperature}
        if req.ctx:
            payload["options"] = {**payload.get("options", {}), "num_ctx": req.ctx}
        start = time.monotonic()
        out = self._post("/api/chat", payload, req.timeout or DEFAULT_TIMEOUT_S)
        try:
            text = out["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMUnavailable("The AI on this computer sent an answer Hal could not read.") from e
        return ChatResult(text=text, provider=self.name, model=model, ms=int((time.monotonic() - start) * 1000))

    def status(self) -> Status:
        """Ready when /api/tags answers within three seconds."""
        try:
            self._tags()
        except LLMUnavailable as e:
            return Status(ready=False, why=str(e), fix="start_ollama")
        return Status(ready=True)

    def models(self) -> list[str]:
        """The installed models, largest first; empty when Ollama is not running."""
        try:
            tags = self._tags()
        except LLMUnavailable:
            return []
        return [t["name"] for t in sorted(tags, key=lambda t: -int(t.get("size") or 0)) if t.get("name")]

    def default_model(self, role: str) -> str:
        """The model for a role left empty in Settings: the largest chat model, or the smallest for `quick`."""
        chat_models = [m for m in self.models() if EMBED_HINT not in m.lower()]
        if not chat_models:
            raise LLMUnavailable("There is no model in the AI on this computer yet.", kind="unavailable")
        return chat_models[-1] if role == "quick" else chat_models[0]

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Vectors from /api/embed."""
        out = self._post("/api/embed", {"model": model, "input": texts}, DEFAULT_TIMEOUT_S)
        vectors = out.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise LLMUnavailable("The AI on this computer sent no vectors.")
        return vectors

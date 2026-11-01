"""The "Hal's local model" backend: the Hal suite's model gateway, spoken to in the OpenAI chat format.

The gateway (`hal-gateway`, in the Hal tools) is the one local endpoint for model requests. It owns the
GPU claims and loads or unloads models itself, so this backend never asks gpu-light for the card
(`local_gpu` is false): a second claim from here would only queue behind the gateway's own.

The gateway wants a bearer token on every request but `/health`. The owner token is one line in
`ai.hal_token_file` (default `~/.config/hal/gateway-token`, mode 0600), read at each call so a rotated token
is picked up without a restart. An address that is not on this computer or the home network still counts as
online, so `ai.local_only` refuses it just as for any other server.
"""
from __future__ import annotations

import logging
import urllib.error
from pathlib import Path

from .base import LLMUnavailable, Status
from .openai_compat import STATUS_TIMEOUT_S, OpenAICompatBackend

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8741/v1"        # the gateway's loopback listener when `[gateway].tcp = true`
DEFAULT_TOKEN_FILE = "~/.config/hal/gateway-token"
PROBE_TIMEOUT_S = 2        # setup waits on this; a running gateway answers in milliseconds
UNAUTHORIZED = (401, 403)


def read_token(path: str | Path) -> str | None:
    """The token on the first line of `path`, or None when the file is missing, empty or unreadable."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.strip().splitlines()
    return lines[0].strip() or None if lines else None


class GatewayBackend(OpenAICompatBackend):
    """Hal's model gateway at `ai.hal_url`, with the owner token read from `token_file`."""

    name = "hal"

    def __init__(self, url: str, token_file: str | Path) -> None:
        super().__init__(url, None)
        self.token_file = token_file
        self.local_gpu = False

    @property
    def key(self) -> str | None:  # type: ignore[override]  # the parent stores a plain attribute; here it is live
        return read_token(self.token_file)

    @key.setter
    def key(self, value: str | None) -> None:
        """Ignores the parent's assignment: the token always comes from the file."""

    def status(self) -> Status:
        """Ready when the gateway lists its models; says what to fix when it is down or the token is wrong."""
        if self.key is None:
            return Status(ready=False, why=f"Hal's gateway token was not found in {self.token_file}.",
                          fix="Make one with hal-gateway token issue --name hal-employment")
        try:
            self._request("GET", "/models", None, STATUS_TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code in UNAUTHORIZED:
                return Status(ready=False, why="Hal's gateway did not accept the token.",
                              fix="Make a new token with hal-gateway token rotate, then try again")
            return Status(ready=False, why=f"Hal's gateway answered {e.code}.")
        except LLMUnavailable:
            return Status(ready=False, why=f"Hal's gateway does not answer at {self.url}.",
                          fix="Start Hal's gateway with hal-gateway serve, with [gateway] tcp = true")
        return Status(ready=True)

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Vectors from the gateway's /embeddings."""
        out = self._request("POST", "/embeddings", {"model": model, "input": texts}, STATUS_TIMEOUT_S * 10)
        try:
            return [row["embedding"] for row in out["data"]]
        except (KeyError, TypeError) as e:
            raise LLMUnavailable("Hal's gateway sent vectors Hal could not read.") from e


def gateway_answers(url: str = DEFAULT_URL, token_file: str | Path = DEFAULT_TOKEN_FILE,
                    timeout: float = PROBE_TIMEOUT_S) -> bool:
    """True when the gateway at `url` accepts the token in `token_file` and lists its models."""
    be = GatewayBackend(url, token_file)
    if be.key is None:
        return False
    try:
        be._request("GET", "/models", None, timeout)
    except (LLMUnavailable, urllib.error.HTTPError):
        return False
    return True

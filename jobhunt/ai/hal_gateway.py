"""The "Hal's local model" backend: the Hal suite's model gateway, spoken to in the OpenAI chat format.

The gateway (`hal-gateway`, in the Hal tools) is the one local endpoint for model requests. It owns the
GPU claims and loads or unloads models itself, so this backend never asks gpu-light for the card
(`local_gpu` is false): a second claim from here would only queue behind the gateway's own.

The gateway wants a bearer token on every request but `/health`. The owner token is one line in
`ai.hal_token_file` (default `~/.config/hal/gateway-token`, mode 0600), read at each call so a rotated token
is picked up without a restart. `status()` warns, but still works, when group or others can access the file.
The gateway only listens on loopback (another computer reaches it through
an SSH tunnel, which is loopback again), so the token is only ever sent to a loopback address, straight
there: never through a proxy from the environment and never on to where a redirect points.

The gateway can also front an online model (a backend with `allow_remote = true`; it lists such models as
`owned_by: "remote:<host>"` and marks each reply with `X-Hal-Remote`). Hal mode promises a local model and
never Claude, so this backend leaves those models out of its list, refuses to ask one by name, and refuses
any reply that still carries the header.
"""
from __future__ import annotations

import logging
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import ChatRequest, ChatResult, LLMUnavailable, Status
from .openai_compat import STATUS_TIMEOUT_S, OpenAICompatBackend, classify

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8741/v1"        # the gateway's loopback listener when `[gateway].tcp = true`
DEFAULT_TOKEN_FILE = "~/.config/hal/gateway-token"
PROBE_TIMEOUT_S = 2        # setup waits on this; a running gateway answers in milliseconds
UNAUTHORIZED = (401, 403)
REMOTE_HEADER = "X-Hal-Remote"      # set by the gateway on every reply from an online backend
REMOTE_OWNER = "remote:"            # the `owned_by` prefix of an online backend's models in /models


LOOSE_MODE = stat.S_IRWXG | stat.S_IRWXO     # any group or world permission on a token file is too much
POSIX_MODES = os.name != "nt"  # Windows reports every file as 0666 or 0444; access there is set by ACLs


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None     # urllib then raises the 3xx as an HTTPError instead of resending the token elsewhere


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def read_token(path: str | Path) -> str | None:
    """The token on the first line of `path`, or None when the file is missing, empty or unreadable."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.strip().splitlines()
    return lines[0].strip() or None if lines else None


def loose_mode_warning(path: str | Path) -> str | None:
    """Words to warn with when group or others can access the token file at `path`, else None.

    Always None on Windows, whose modes say nothing about other users.
    """
    if not POSIX_MODES:
        return None
    try:
        mode = Path(path).expanduser().stat().st_mode
    except OSError:
        return None
    if not mode & LOOSE_MODE:
        return None
    return (f"Hal's gateway token file {path} can be read by other users on this computer "
            f"(mode {stat.S_IMODE(mode):04o}).")


class GatewayBackend(OpenAICompatBackend):
    """Hal's model gateway at `ai.hal_url`, with the owner token read from `token_file`."""

    name = "hal"

    def __init__(self, url: str, token_file: str | Path) -> None:
        super().__init__(url, None)
        self.token_file = token_file
        self.loopback = classify(self.url)[1]     # (online, local_gpu): only loopback gives local_gpu
        self.local_gpu = False

    @property
    def key(self) -> str | None:  # type: ignore[override]  # the parent stores a plain attribute; here it is live
        """Returns the gateway token, read from the token file on each use."""
        return read_token(self.token_file)

    @key.setter
    def key(self, value: str | None) -> None:
        """Ignores the parent's assignment: the token always comes from the file."""

    def _open(self, req: urllib.request.Request, timeout: float) -> Any:
        if not self.loopback:
            raise LLMUnavailable(f"Hal's gateway must be on this computer, not at {self.url}, so the token "
                                 "stays here.")
        reply = _OPENER.open(req, timeout=timeout)
        host = reply.headers.get(REMOTE_HEADER)
        if host:
            reply.close()
            raise LLMUnavailable(f"Hal's gateway sent this to an online model at {host}; Hal mode only uses "
                                 "the local model.", kind="local_only")
        return reply

    def _listed(self) -> list[dict]:
        out = self._request("GET", "/models", None, STATUS_TIMEOUT_S)
        return [m for m in out.get("data", []) if isinstance(m, dict) and m.get("id")]

    def _remote(self, entry: dict) -> bool:
        return str(entry.get("owned_by", "")).startswith(REMOTE_OWNER)

    def models(self) -> list[str]:
        """The gateway's local models; online models it fronts are left out, and empty when it does not answer."""
        try:
            return [m["id"] for m in self._listed() if not self._remote(m)]
        except (LLMUnavailable, urllib.error.HTTPError):
            return []

    def chat(self, req: ChatRequest, model: str) -> ChatResult:
        """Asks `model` through the gateway; refuses before sending when the gateway lists it as online."""
        try:
            remote = {m["id"] for m in self._listed() if self._remote(m)}
        except urllib.error.HTTPError as e:
            raise LLMUnavailable(f"Hal's gateway refused the request ({e.code}).") from e
        if model in remote:
            raise LLMUnavailable(f"{model} is an online model behind Hal's gateway; Hal mode only uses the local "
                                 "model. Choose a local one in Settings, Hal's brain.", kind="local_only")
        return super().chat(req, model)

    def status(self) -> Status:
        """Ready when the gateway lists its models; says what to fix when it is down or the token is wrong."""
        if not self.loopback:
            return Status(ready=False, why=f"Hal's gateway must be on this computer, not at {self.url}.",
                          fix="Set the gateway address to http://127.0.0.1:8741/v1, through an SSH tunnel if needed")
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
        warning = loose_mode_warning(self.token_file)
        if warning:
            log.warning("%s Set its mode to 600.", warning)
            return Status(ready=True, why=warning, fix=f"Run chmod 600 on {self.token_file}",
                          detail={"warning": warning})
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

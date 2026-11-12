"""The "Hal's local model" backend: the Hal suite's model gateway, spoken to in the OpenAI chat format.

The gateway (`hal-gateway`, in the Hal tools) is the one local endpoint for model requests. It owns the
GPU claims and loads or unloads models itself, so this backend never asks gpu-light for the card
(`local_gpu` is false): a second claim from here would only queue behind the gateway's own.

The gateway wants a bearer token on every request but `/health`. The owner token is one line in
`ai.hal_token_file` (default `~/.config/hal/gateway-token`, mode 0600), read at each call so a rotated token
is picked up without a restart. `status()` warns, but still works, when group or others can access the file.

Reading the record is bulk work that must give the card up whenever the person uses Hal, so calls made inside
`as_batch()` send the batch token in `ai.hal_batch_token_file` instead (`hal-gateway token issue --class
batch`). The gateway runs those as gpu-light batch work: it answers 503 `gpu_busy`, which the read waits out,
while gpu-light has not given it the card or a chat or voice request is in flight. Without a batch token the
calls go out with the owner token, as a `service` claim that keeps gpu-light's batch jobs waiting.
The gateway only listens on loopback (another computer reaches it through
an SSH tunnel, which is loopback again), so the token is only ever sent to a loopback address, straight
there: never through a proxy from the environment and never on to where a redirect points.

The gateway says how many requests a model serves at once (`meta.slots` in its list, from the model's
`parallel`), and `slots` passes that on, so Hal sends that many at once (`ai.at_once`).

The gateway always listens on a Unix socket in Hal's runtime directory, and on TCP only when
`[gateway].tcp = true`. With `ai.hal_url` left at its default, this backend talks to that socket when it
is there, so Hal mode works without turning the TCP listener on. A test run only looks in
`HAL_RUNTIME_DIR`, never the real runtime directory, so no test meets a live Hal.

Every job asks for `hal-writer`, the model Hal's tuning picked for prose (`ROLE_ALIASES`), and the checker
and quick jobs fall back to `hal-chat` on a gateway with no writer; a gateway that lists neither gets its
first model, as any OpenAI-compatible server does. Checking went to `hal-chat` once, but on a 24 GB card
beside the resident writer the gateway handed it to Ollama with no room on the card, every layer went to
system RAM, and each CV read timed out after 300 s (2026-11-12). One model also keeps both writer slots busy.

The gateway can also front an online model (a backend with `allow_remote = true`; it lists such models as
`owned_by: "remote:<host>"` and marks each reply with `X-Hal-Remote`). Hal mode promises a local model and
never Claude, so this backend leaves those models out of its list, refuses to ask one by name, and refuses
any reply that still carries the header.
"""
from __future__ import annotations

import contextlib
import contextvars
import http.client
import io
import json
import logging
import os
import socket
import stat
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from .base import ChatRequest, ChatResult, LLMUnavailable, Status
from .openai_compat import STATUS_TIMEOUT_S, OpenAICompatBackend, classify

log = logging.getLogger(__name__)
THINKING_BUDGET_TOKENS = 8192   # Gemma 4 has no named levels of thinking, only on or off; this caps it

DEFAULT_URL = "http://127.0.0.1:8741/v1"        # the gateway's loopback listener when `[gateway].tcp = true`
DEFAULT_TOKEN_FILE = "~/.config/hal/gateway-token"
BUSY_STATUS = 503
BUSY_CODE = "gpu_busy"     # the gateway's error code while batch work must wait (halgateway/gpu/manager.py)
SOCKET_NAME = "gateway.sock"        # the gateway's Unix listener in Hal's runtime directory (halgateway/server.py)
RUN_USER_ROOT = Path("/run/user")   # Hal's runtime directory without XDG_RUNTIME_DIR (halkit/paths.py)
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


class _SocketConnection(http.client.HTTPConnection):
    """HTTP to the gateway over its Unix socket; the host name in the request is only a label."""

    def __init__(self, path: Path, timeout: float | None) -> None:
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]  # POSIX only
        sock.settimeout(self.timeout)
        sock.connect(str(self.path))
        self.sock = sock


class _SocketHandler(urllib.request.HTTPHandler):
    """Sends every http request to the gateway's socket instead of a TCP address."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(lambda _host, timeout=None, **_kw: _SocketConnection(self.path, timeout), req)


def default_socket() -> Path | None:
    """The gateway's Unix socket in Hal's runtime directory when one is there; None on Windows.

    The directory follows Hal's own rule: `HAL_RUNTIME_DIR`, else `$XDG_RUNTIME_DIR/hal`, else
    `/run/user/<uid>/hal`. A test run only honours `HAL_RUNTIME_DIR`.
    """
    if not POSIX_MODES:
        return None
    override = os.environ.get("HAL_RUNTIME_DIR")
    if override:
        base = Path(override).expanduser()
    elif os.environ.get("HAL_TEST_MODE") == "1" and not os.environ.get("HAL_TEST_REAL_AI"):
        return None
    elif os.environ.get("XDG_RUNTIME_DIR"):
        base = Path(os.environ["XDG_RUNTIME_DIR"]) / "hal"
    else:
        base = RUN_USER_ROOT / str(os.getuid()) / "hal"  # type: ignore[attr-defined]  # POSIX only
    path = base / SOCKET_NAME
    return path if path.is_socket() else None


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


# Hal's role -> the gateway aliases it asks for, first listed first
ROLE_ALIASES = {"writer": ("hal-writer",), "checker": ("hal-writer", "hal-chat"), "quick": ("hal-writer", "hal-chat")}

_batch: contextvars.ContextVar[bool] = contextvars.ContextVar("hal_gateway_batch", default=False)


@contextlib.contextmanager
def as_batch() -> Iterator[None]:
    """Runs the block's calls to Hal's gateway as batch work, with the batch token when there is one."""
    token = _batch.set(True)
    try:
        yield
    finally:
        _batch.reset(token)


class GatewayBackend(OpenAICompatBackend):
    """Hal's model gateway at `ai.hal_url`, with the owner token read from `token_file`.

    Inside `as_batch()` it sends the token in `batch_token_file` when that file holds one.
    """

    name = "hal"
    # llama.cpp ends thinking at this many tokens and has the model answer (b12121, checked 2026-11-12); normal
    # calls think for up to about 3,000 tokens, so only a runaway meets it
    EXTRA: ClassVar[dict[str, int | str]] = {"thinking_budget_tokens": THINKING_BUDGET_TOKENS}

    def __init__(self, url: str, token_file: str | Path, batch_token_file: str | Path | None = None) -> None:
        super().__init__(url, None)
        self.token_file = token_file
        self.batch_token_file = batch_token_file
        self._warned_no_batch = False
        self.socket = default_socket() if self.url == DEFAULT_URL.rstrip("/") else None
        self.loopback = classify(self.url)[1]     # (online, local_gpu): only loopback gives local_gpu
        self._opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect,
                                                    _SocketHandler(self.socket)) if self.socket else _OPENER)
        self.local_gpu = False

    @property
    def key(self) -> str | None:  # type: ignore[override]  # the parent stores a plain attribute; here it is live
        """Returns the gateway token, read from its file on each use: the batch token inside `as_batch()`."""
        if _batch.get() and self.batch_token_file:
            batch = read_token(self.batch_token_file)
            if batch:
                return batch
            if not self._warned_no_batch:
                self._warned_no_batch = True
                log.warning("no batch token in %s: reading the record holds a service claim, so gpu-light's batch "
                            "jobs wait for it", self.batch_token_file)
        return read_token(self.token_file)

    @key.setter
    def key(self, value: str | None) -> None:
        """Ignores the parent's assignment: the token always comes from the file."""

    def _open(self, req: urllib.request.Request, timeout: float) -> Any:
        if not self.loopback:
            raise LLMUnavailable(f"Hal's gateway must be on this computer, not at {self.url}, so the token "
                                 "stays here.")
        try:
            reply = self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != BUSY_STATUS:
                raise
            body = e.read()     # read once, so it is handed on for `openai_compat._refused` to read again
            if _error_code(body) == BUSY_CODE:
                raise LLMUnavailable("Hal's gateway is giving the graphics card to someone else; the read waits.",
                                     kind="gpu_busy") from e
            raise urllib.error.HTTPError(e.url, e.code, e.msg, e.hdrs, io.BytesIO(body)) from None
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

    def slots(self, model: str) -> int:
        """How many requests the gateway serves `model` at once: its `meta.slots`, else 1, as the gateway names
        slots only past one and a second request only waits for the first inside its timeout; 0 when the
        gateway does not list the model or does not answer."""
        try:
            listed = self._listed()
        except (LLMUnavailable, urllib.error.HTTPError):
            return 0
        for entry in listed:
            if entry["id"] == model:
                meta = entry.get("meta")
                slots = meta.get("slots") if isinstance(meta, dict) else None
                return slots if isinstance(slots, int) and slots > 0 else 1
        return 0

    def default_model(self, role: str) -> str:
        """The first of `role`'s aliases (`ROLE_ALIASES`) the gateway lists, else its first model."""
        listed = self.models()
        alias = next((a for a in ROLE_ALIASES.get(role, ()) if a in listed), None)
        return alias or super().default_model(role)

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
            where = self.socket or self.url
            return Status(ready=False, why=f"Hal's gateway does not answer at {where}.",
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


def _error_code(body: bytes) -> str:
    """The gateway's `error.code` in a refused request's body, or "" when it has none."""
    try:
        error = json.loads(body or b"{}").get("error")
    except (ValueError, AttributeError):
        return ""
    return str(error.get("code") or "") if isinstance(error, dict) else ""


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

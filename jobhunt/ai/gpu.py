"""The graphics-card gate for AI that runs on this computer.

Another program holding the GPU (a training run, a llama.cpp server) means a local model must wait, since
loading one beside it can fail or slow both. Hal's own GPU users and the programs named in `ai.gpu_ignore`
do not count. Only backends with `local_gpu` consult the gate, and only while `ai.gpu_gate` is on: Claude
and remote servers never wait for this computer's card.

Background calls wait until the card is free (up to `BACKGROUND_WAIT_S`, the scheduler's own job limit);
calls the person is waiting for wait up to `FOREGROUND_WAIT_S`, then fail with words and a Retry.

When the `gpu_light` client is installed, a local call also holds a gpu-light claim (class `interactive` for a
call the person waits for, `batch` for background work) so the card scheduler can make room. Without it, or in a
test run, the nvidia-smi gate above is all there is.
"""
from __future__ import annotations

import contextlib
import importlib
import logging
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

from ..core.platform import wsl2
from .base import LLMUnavailable

log = logging.getLogger(__name__)

# Hal's own GPU users: Ollama, the voice server and speech recognition.
OWN = ("ollama", "GPT-SoVITS", "api_v2.py", "hal-voice", "halvoice", "faster_whisper")
NVIDIA_SMI_TIMEOUT_S = 10
FOREGROUND_WAIT_S = 60          # a request the person is waiting for gives up after a minute
BACKGROUND_WAIT_S = 3600        # a scheduled job runs at most an hour
POLL_S = 5
BACKGROUND = 2
CLAIM_NAME = "hal-employment"
CLAIM_VRAM_MIB = 8000           # a mid-size local model; gpu-light only uses it to plan room

waiting: str | None = None      # the status line while a background call waits; None when nothing waits


def _ignored() -> tuple[str, ...]:
    from ..core import settings

    try:
        extra = tuple(settings.get("ai.gpu_ignore") or ())
    except (KeyError, LookupError, RuntimeError):
        extra = ()
    return extra + OWN


def holders() -> list[tuple[str, int]]:
    """(program name, MiB) for each other program using the GPU; empty without an NVIDIA card."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT_S, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ignore = _ignored()
    found = []
    on_wsl2 = wsl2.is_wsl2()
    for line in out.strip().splitlines():
        try:
            pid, path, mib = (x.strip() for x in line.split(",", 2))
        except ValueError:
            continue
        if on_wsl2 and path in wsl2.UNNAMED_PROCESS:
            continue    # a Windows process has no name here, so it cannot be told apart from Hal's own
        try:
            # The command line names the program; the process path is often only "python".
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            cmd = path
        if not any(i in cmd or i in path for i in ignore):
            found.append((path.replace("\\", "/").rsplit("/", 1)[-1], int(mib or 0) if mib.isdigit() else 0))
    return found


def _names(found: list[tuple[str, int]]) -> str:
    return ", ".join(name for name, _ in found)


def gate_on() -> bool:
    """True when `ai.gpu_gate` asks local models to wait for a busy card."""
    from ..core import settings

    return bool(settings.get("ai.gpu_gate"))


def wait(priority: int) -> None:
    """Returns when the card is free; raises `LLMUnavailable(kind="gpu_busy")` when the wait runs out."""
    global waiting
    limit = BACKGROUND_WAIT_S if priority >= BACKGROUND else FOREGROUND_WAIT_S
    deadline = time.monotonic() + limit
    try:
        while True:
            found = holders()
            if not found:
                return
            names = _names(found)
            if time.monotonic() >= deadline:
                raise LLMUnavailable(f"The graphics card is busy ({names}). Try again when it finishes.",
                                     kind="gpu_busy")
            if priority >= BACKGROUND:
                waiting = f"Waiting for the graphics card: {names} is using it"
                log.info("%s", waiting)
            time.sleep(min(POLL_S, max(0.0, deadline - time.monotonic())))
    finally:
        waiting = None


@contextlib.contextmanager
def claim(priority: int) -> Iterator[None]:
    """Holds a gpu-light claim for one local call when the client is installed; otherwise does nothing.

    A claim that cannot be made (no daemon, a timeout) is logged and the call goes on, since the
    nvidia-smi gate has already run and an unreachable scheduler must not stop the person's work.
    """
    if os.environ.get("HAL_TEST_MODE") == "1" and not os.environ.get("HAL_TEST_REAL_GPU_LIGHT"):
        yield
        return
    try:
        client = importlib.import_module("gpu_light")
    except ImportError:
        yield
        return
    background = priority >= BACKGROUND
    limit = BACKGROUND_WAIT_S if background else FOREGROUND_WAIT_S
    try:
        held = client.claim(CLAIM_NAME, cls="batch" if background else "interactive",
                            vram_mib=CLAIM_VRAM_MIB, wait=True, timeout=limit)
        held.__enter__()
    except Exception as e:
        log.warning("no gpu-light claim, going on without one: %s", e)
        yield
        return
    try:
        yield
    finally:
        held.__exit__(None, None, None)


def busy() -> str | None:
    """Why the AI cannot run now, or None when it can (the old `jobhunt.gpu.busy`)."""
    from . import backend

    be = backend()
    if be.local_gpu and gate_on():
        found = holders()
        if found:
            return f"waiting for the GPU: {', '.join(f'{n} ({mib} MiB)' for n, mib in found)}"
    status = be.status()
    return None if status.ready else (status.why or "the AI is not available")

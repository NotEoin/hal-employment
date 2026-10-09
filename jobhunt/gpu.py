"""Decides whether the local models may run: Ollama must be up and the GPU free.

Another program holding the GPU (a training run, a llama.cpp server) means the
models must wait, since loading one beside it can fail or slow both. Processes
named in config.toml's `gpu_ignore`, and Hal's own voice services, do not count.
"""
import subprocess
from pathlib import Path

from . import llm
from .config import CONFIG

# Hal's own GPU users: Ollama, the voice server and speech recognition.
OWN = ("ollama", "GPT-SoVITS", "api_v2.py", "hal-voice", "halvoice", "faster_whisper")


def holders() -> list[tuple[str, int]]:
    """Returns (process name, MiB) for each other program using the GPU."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ignore = tuple(CONFIG.get("gpu_ignore", [])) + OWN
    found = []
    for line in out.strip().splitlines():
        pid, path, mib = (x.strip() for x in line.split(",", 2))
        try:
            # The command line names the program; the process path is often only "python".
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            cmd = path
        if not any(i in cmd or i in path for i in ignore):
            found.append((path.rsplit("/", 1)[-1], int(mib or 0)))
    return found


def busy() -> str | None:
    """Returns why the local models cannot run now, or None when they can."""
    others = holders()
    if others:
        names = ", ".join(f"{n} ({mib} MiB)" for n, mib in others)
        return f"waiting for the GPU: {names}"
    if not llm.available():
        return "Ollama is not running"
    return None

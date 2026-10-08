"""Decides whether the local models may run: Ollama must be up and the GPU free.

Another program holding the GPU (a training run, a llama.cpp server) means the
models must wait, since loading one beside it can fail or slow both. Processes
named in config.toml's `gpu_ignore` do not count as holding the GPU.
"""
import subprocess

from . import llm
from .config import CONFIG

# Ollama's runner holds the GPU only while serving the models jobhunt asked for.
OWN = ("ollama",)


def holders() -> list[tuple[str, int]]:
    """Returns (process name, MiB) for each other program using the GPU."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ignore = tuple(CONFIG.get("gpu_ignore", [])) + OWN
    found = []
    for line in out.strip().splitlines():
        path, _, mib = line.rpartition(",")
        name = path.strip().rsplit("/", 1)[-1]
        if name and not any(i in path for i in ignore):
            found.append((name, int(mib.strip() or 0)))
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

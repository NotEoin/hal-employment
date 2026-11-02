"""Tells whether Hal runs inside WSL2, and what WSL2 needs that a Linux desktop already has.

WSL2 kernels say `microsoft-standard-WSL2` in `osrelease`. Hal there is the Linux install: its data
lives in the distro's own `~/.config/hal-employment`, apart from any Windows install. Two things differ
from a Linux desktop. `systemctl` exists but does nothing until `systemd=true` is set in `/etc/wsl.conf`,
and a fresh distro has no terminal program. `HAL_EMPLOYMENT_OSRELEASE` names a file that replaces
`/proc/sys/kernel/osrelease`, which is how tests fake WSL2.
"""
from __future__ import annotations

import os
from pathlib import Path

OSRELEASE = Path("/proc/sys/kernel/osrelease")
OSRELEASE_ENV = "HAL_EMPLOYMENT_OSRELEASE"
SYSTEMD_RUN_DIR = Path("/run/systemd/system")      # exists only when systemd is PID 1
DOCS_HINT = "docs/howto/run-on-windows-with-wsl2.md"
UNNAMED_PROCESS = ("[N/A]", "N/A", "[Not Found]")  # what nvidia-smi prints for a Windows process
NO_SYSTEMD_ADVICE = (f" Under WSL2, turn systemd on in /etc/wsl.conf and run `wsl --shutdown` (see {DOCS_HINT}).")
NO_TERMINAL_ADVICE = (f" Under WSL2, run `sudo apt-get install -y xterm`; WSLg shows it as a window (see {DOCS_HINT}).")


def is_wsl2() -> bool:
    """True when the kernel release names Microsoft or WSL; False when it cannot be read."""
    path = Path(os.environ.get(OSRELEASE_ENV) or OSRELEASE)
    try:
        text = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return False
    return "microsoft" in text.lower() or "WSL" in text


def systemd_running() -> bool:
    """True when systemd is the init process, so `systemctl --user` can work."""
    return SYSTEMD_RUN_DIR.is_dir()

"""The steps Settings shows under "Use Hal from another computer", filled in for this computer.

Setting up the SSH server needs the owner's password (sudo), so Hal only shows the commands with Copy
buttons and reports whether the server is installed and running; it never runs them. Reaching the desktop
from outside the home network needs a VPN, which Hal does not set up.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..core import paths
from ..web import auth
from . import launcher

log = logging.getLogger(__name__)

TOOL_TIMEOUT_S = 5
SSHD_PATHS = (Path("/usr/sbin/sshd"), Path("/usr/bin/sshd"))
SSH_UNITS = ("ssh", "sshd")       # Debian and Ubuntu name it ssh; Fedora and Arch sshd
LAUNCHER_OS = ("linux", "mac", "windows")


def ssh_server(*, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
               exists: Callable[[Path], bool] = Path.exists) -> dict:
    """{installed, running} for this computer's SSH server; None for each when Hal cannot tell (not Linux)."""
    if paths.platform_name() != "linux":
        return {"installed": None, "running": None}
    installed = any(exists(p) for p in SSHD_PATHS) or bool(shutil.which("sshd"))
    running = False
    for unit in SSH_UNITS:
        try:
            done = run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=TOOL_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("could not ask systemd about %s: %s", unit, e)
            continue
        if done.stdout.strip() == "active":
            running = True
            break
    return {"installed": installed, "running": running}


def steps(host: str, user: str, hal: str) -> list[dict]:
    """The numbered steps, each with the command to copy (or None)."""
    login = f"{user}@{host}"
    rows = [
        ("On this computer, install the SSH server. It asks for your password.",
         "sudo apt install openssh-server"),
        ("Switch it on, now and whenever this computer starts.", "sudo systemctl enable --now ssh"),
        ("On your other computer, open a terminal and make a key. Press Enter to each question.",
         "ssh-keygen -t ed25519"),
        ("Copy the key to this computer. It asks for this computer's password once.", f"ssh-copy-id {login}"),
        ("On a Windows computer, use this instead of the step above (in PowerShell).",
         f'type $env:USERPROFILE\\.ssh\\id_ed25519.pub | ssh {login} "cat >> ~/.ssh/authorized_keys"'),
        ("Back on this computer, switch off signing in with a password, so only your key works.",
         'echo "PasswordAuthentication no" | sudo tee /etc/ssh/sshd_config.d/hal-employment.conf '
         "&& sudo systemctl reload ssh"),
        ("Keep this computer awake while you are away from it: Settings, Power, Automatic suspend: Off "
         "when plugged in.", None),
        ("On a Linux or Mac computer, get the launcher and add it to the menu.",
         f"ssh {login} '{hal} remote-launcher --os linux' > ~/hal-remote.sh && chmod +x ~/hal-remote.sh "
         "&& ~/hal-remote.sh --install"),
        ("On a Windows computer, get the launcher in Command Prompt, then add it to the Start menu.",
         f'ssh {login} "{hal} remote-launcher --os windows" > %USERPROFILE%\\hal-remote.cmd '
         "&& %USERPROFILE%\\hal-remote.cmd --install"),
        ('Open "Hal Employment (desktop)" on your other computer. It keeps a small window open while you use '
         "Hal; close it when you have finished. Load the browser helper there from the folder "
         '"Hal Employment/Browser helper" in your home folder.', None),
    ]
    return [{"n": n, "text": text, "command": command} for n, (text, command) in enumerate(rows, start=1)]


def instructions(*, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
    """What `GET /remote/instructions` answers."""
    host, user = launcher.this_host(), launcher.this_user()
    try:
        hal = launcher.hal_command()
    except ValueError as e:
        log.warning("%s", e)
        hal = launcher.SHIM
    return {"ssh_server": ssh_server(run=run), "host": host, "user": user, "port": auth.server_port(),
            "code_dir": str(paths.code_dir()), "steps": steps(host, user, hal), "launcher_os": list(LAUNCHER_OS)}

"""The laptop launchers, filled in with this desktop's host, user and port.

The templates are `scripts/remote/hal-remote.sh` (Linux and macOS) and `hal-remote.cmd` (Windows) in the
code folder; `render` swaps their placeholder lines for this computer's values.

Design decisions, each with its reason:

- Host and user are checked against a narrow pattern before they are written into a script, because the
  script runs them through a shell on the laptop; anything else is refused with words.
- The launcher calls the `hal-employment-cli` shim by full path (non-interactive SSH shells may lack
  `~/.local/bin` on PATH), never `~/.local/bin/hal-employment`, which on the owner's desktop is Hal's own
  command and ignores its arguments. Without the shim it calls the code folder's Python directly.
- The `.cmd` is written with CRLF endings whatever the template's endings: it is generated, not checked
  out, so `.gitattributes` does not cover it, and cmd.exe misreads labels in LF-only batch files.
"""
from __future__ import annotations

import getpass
import re
import socket
from pathlib import Path

from ..core import paths

SHIM = "~/.local/bin/hal-employment-cli"
TEMPLATES = {"linux": "hal-remote.sh", "mac": "hal-remote.sh", "windows": "hal-remote.cmd"}
FILE_NAMES = {"linux": "hal-remote.sh", "mac": "hal-remote.sh", "windows": "hal-remote.cmd"}
HOST_OK = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_OK = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]{0,31}$")
COMMAND_OK = re.compile(r"^[A-Za-z0-9._/~ -]+$")
PLACEHOLDERS = {
    "sh": {'HOST="USER@DESKTOP"; PORT=8737': 'HOST="{login}"; PORT={port}',
           "HAL='~/.local/bin/hal-employment-cli'": "HAL='{hal}'"},
    "cmd": {'set "HOST=USER@DESKTOP"': 'set "HOST={login}"', 'set "PORT=8737"': 'set "PORT={port}"',
            'set "LPORT=8737"': 'set "LPORT={port}"', 'set "HAL=~/.local/bin/hal-employment-cli"': 'set "HAL={hal}"'},
}


def templates_dir() -> Path:
    """Where the launcher templates live in the code folder."""
    return paths.code_dir() / "scripts" / "remote"


def this_host() -> str:
    """This computer's name on the home network, as the laptop would type it."""
    return socket.gethostname()


def this_user() -> str:
    """The account the laptop signs in to over SSH."""
    return getpass.getuser()


def hal_command(home: Path | None = None) -> str:
    """What the launcher runs on this computer: the shim when it is installed, else the code folder's Python."""
    home = home or Path.home()
    if (home / ".local" / "bin" / "hal-employment-cli").exists():
        return SHIM
    python = paths.code_dir() / ".venv" / "bin" / "python"
    command = f"{python.as_posix()} -m jobhunt"
    if not COMMAND_OK.match(command) or " " in python.as_posix():
        raise ValueError("Hal's folder has a space or symbol in its name, so the launcher cannot call it. "
                         "Install Hal's command first (Settings, Updates).")
    return command


def render(os_name: str, *, host: str, user: str, port: int, hal: str = SHIM) -> bytes:
    """The launcher for `os_name` ("linux", "mac" or "windows") as the bytes to save on the laptop."""
    if os_name not in TEMPLATES:
        raise ValueError(f"There is no launcher for {os_name!r}: choose linux, mac or windows.")
    if not HOST_OK.match(host or ""):
        raise ValueError(f"{host!r} does not look like a computer's name.")
    if not USER_OK.match(user or ""):
        raise ValueError(f"{user!r} does not look like a user name.")
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError(f"{port!r} is not a port number.")
    if not COMMAND_OK.match(hal):
        raise ValueError(f"{hal!r} is not a command the launcher can call.")
    text = (templates_dir() / TEMPLATES[os_name]).read_text(encoding="ascii").replace("\r\n", "\n")
    kind = "cmd" if os_name == "windows" else "sh"
    values = {"login": f"{user}@{host}", "port": port, "hal": hal}
    for old, new in PLACEHOLDERS[kind].items():
        if old not in text:
            raise RuntimeError(f"the launcher template {TEMPLATES[os_name]} has lost its line {old!r}")
        text = text.replace(old, new.format(**values), 1)
    if kind == "cmd":
        text = text.replace("\n", "\r\n")
    return text.encode("ascii")

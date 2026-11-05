"""The platform layer: everything Hal asks of the computer itself.

Opening files and windows, pop-ups, start at login, finding the person's browsers, running hidden or
detached children, and retiring the owner's old systemd timers all go through `current()`, so no other
module tests `os.name`. Linux lives in `linux.py` and Windows in `windows.py`; macOS uses the Linux one,
which answers "not available" where a tool is missing.

Design decisions, each with its reason:

- Each platform takes its outside world (command runner, registry reader, file checks, home folder) as
  constructor arguments, so tests drive the real code with fakes and never touch systemd, the registry or
  a real browser.
- The app window and PDF printing never depend on the person's browser : on Windows they use
  Microsoft Edge, which is always there; on Linux the window uses the first Chromium-family browser found,
  else a tab in the default browser.
- Firefox is found and listed, marked as not supported yet, because the browser helper is a Chromium
  extension and a Firefox build is out of scope for now.
"""
from __future__ import annotations

import functools
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

LEGACY_TIMERS = ("jobhunt-discover.timer", "jobhunt-mail.timer", "jobhunt-digest.timer")
HAL_UNIT = "hal-employment.service"          # Hal's own unit, rendered by the owner's Hal setup
APP_UNIT = "hal-employment-app.service"      # the unit a Linux user install gets
RETIRED_DIR = "retired-by-hal-employment"
TEMPLATES = Path(__file__).with_name("templates")
NOT_SUPPORTED = "not supported yet"
TOOL_TIMEOUT_S = 5                            # systemctl, flatpak, notify-send, nvidia-smi
STOP_GRACE_S = 5                              # how long a stopped child gets to end before it is killed
MANAGED_BY_HAL = "Managed by your Hal setup"
NEW_WINDOW = "--new-window"                  # Chromium's switch for a fresh window


@dataclass(frozen=True)
class Browser:
    """One browser found on this computer."""

    id: str             # opera | chrome | edge | brave | vivaldi | chromium | firefox
    name: str           # "Opera"
    exe: Path
    family: str         # chromium | firefox
    sandboxed: bool     # a snap or flatpak, which cannot read hidden folders
    extensions_url: str  # "opera://extensions"
    args: tuple[str, ...] = ()   # put before the browser's own arguments ("run", "com.opera.Opera" for a flatpak)

    @property
    def supported(self) -> bool:
        """True when the browser helper can live in this browser."""
        return self.family == "chromium"

    def argv(self, *extra: str) -> list[str]:
        """The command line that starts this browser with `extra` arguments."""
        return [str(self.exe), *self.args, *extra]


@dataclass(frozen=True)
class Kind:
    """What Hal knows about one browser before looking for it."""

    id: str
    name: str
    family: str
    extensions_url: str


# In the order Settings lists them, Opera first.
KINDS = (
    Kind("opera", "Opera", "chromium", "opera://extensions"),
    Kind("chrome", "Google Chrome", "chromium", "chrome://extensions"),
    Kind("edge", "Microsoft Edge", "chromium", "edge://extensions"),
    Kind("brave", "Brave", "chromium", "brave://extensions"),
    Kind("vivaldi", "Vivaldi", "chromium", "vivaldi://extensions"),
    Kind("chromium", "Chromium", "chromium", "chrome://extensions"),
    Kind("firefox", "Firefox", "firefox", "about:addons"),
)
KIND = {k.id: k for k in KINDS}


def found(kind_id: str, exe: Path, *, sandboxed: bool = False, args: tuple[str, ...] = ()) -> Browser:
    """A `Browser` of a kind Hal knows, at `exe`."""
    k = KIND[kind_id]
    return Browser(k.id, k.name, exe, k.family, sandboxed, k.extensions_url, args)


def label(browser: Browser) -> str:
    """The browser's name as Settings shows it ("Firefox (not supported yet)")."""
    return browser.name if browser.supported else f"{browser.name} ({NOT_SUPPORTED})"


class Platform(Protocol):
    """What Hal asks of the computer."""

    name: str

    def open_path(self, path: Path) -> None:
        """Opens a file or folder with the default application."""
    def notify(self, title: str, body: str, *, urgent: bool = False) -> None:
        """Shows a desktop notification."""
    def start_at_login(self, on: bool) -> str:
        """Turns start at login on or off and returns a one-line result."""
    def start_at_login_state(self) -> bool | None:
        """Returns whether start at login is on, or None when it cannot be told."""
    def browsers(self) -> list[Browser]:
        """Returns the browsers installed on this computer."""
    def open_in_browser(self, browser: Browser, url: str) -> None:
        """Opens a page in a given browser."""
    def open_browser_window(self, browser: Browser) -> None:
        """Opens a new window of a given browser."""
    def open_in_default_browser(self, url: str) -> None:
        """Opens a page in the person's default browser."""
    def run_hidden(self, args: list[str], **kw) -> subprocess.CompletedProcess:
        """Runs a command without showing a console window."""
    def popen_hidden(self, args: list[str], **kw) -> subprocess.Popen:
        """Starts a command without showing a console window."""
    def kill_tree(self, proc: subprocess.Popen) -> None:
        """Stops a process and everything it started."""
    def spawn_detached(self, args: list[str], *, unit: str | None = None) -> int:
        """Starts a command that outlives this process and returns its pid."""
    def open_console(self, args: list[str], title: str) -> int:
        """Runs a command in a visible console and returns its exit code."""
    def machine(self) -> dict:
        """Returns `memory_gb`, `gpu` and `cpus`, for Settings and the AI choice."""
    def service(self) -> str:
        """Returns how the server runs: `hal-unit`, `app-unit` or `none`."""
    def restart_server(self) -> None:
        """Restarts the Hal server."""
    def documents_folder(self) -> Path:
        """Returns the user's Documents folder."""
    def retire_legacy_timers(self, dry_run: bool = False) -> list[str]:
        """Turns off the timers of earlier versions and returns their names."""
    def restore_legacy_timers(self) -> list[str]:
        """Turns the earlier versions' timers back on and returns their names."""
    def legacy_timers_enabled(self) -> list[str]:
        """Returns the names of the earlier versions' timers that are on."""


@functools.cache
def current() -> Platform:
    """This computer's platform layer."""
    if os.name == "nt":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    from .linux import LinuxPlatform

    return LinuxPlatform()


def retire_legacy_timers(dry_run: bool = False) -> list[str]:
    """Disables the owner's old `jobhunt-*` timers and moves their unit files aside; the names it retired."""
    return current().retire_legacy_timers(dry_run)


def restore_legacy_timers() -> list[str]:
    """Puts back the timers `retire_legacy_timers` moved aside and starts them (`import --undo`)."""
    return current().restore_legacy_timers()


def render(template: str, **values: str) -> str:
    """A file from `templates/` with its `{name}` placeholders filled."""
    return (TEMPLATES / template).read_text(encoding="utf-8").format(**values)

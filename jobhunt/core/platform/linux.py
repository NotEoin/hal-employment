"""The platform layer on Linux: systemd user units, notify-send, xdg-open, browsers on PATH.

Design decisions, each with its reason:

- Every outside call goes through the `run`, `popen` and `which` given to the constructor, with a timeout,
  so tests use fakes and a hung `systemctl` never holds the server.
- A detached child is started with `systemd-run --user --unit=<unit> --collect` when a unit name is given:
  a plain `setsid` child stays in the server unit's cgroup and is killed when that unit stops
  (systemd.kill(5), KillMode=control-group). `systemd-run` does not pass the environment on, so Hal's own
  `HAL_*` variables are passed with `--setenv`.
- When Hal's own unit (`hal-employment.service`, rendered by the owner's Hal setup) exists, start at login
  belongs to it, and Hal never writes or enables its own app unit.
- The browser list is kept for `BROWSERS_CACHE_S`, because the `applying.browser` default asks for it on every
  settings read and `flatpak list` takes a moment.
- Retiring the old timers moves their unit files into `retired-by-hal-employment/`, so a second call finds
  nothing to do and `restore_legacy_timers` can put them back exactly.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from .. import paths
from . import (
    APP_UNIT,
    HAL_UNIT,
    KINDS,
    LEGACY_TIMERS,
    MANAGED_BY_HAL,
    NEW_WINDOW,
    RETIRED_DIR,
    STOP_GRACE_S,
    TOOL_TIMEOUT_S,
    Browser,
    found,
    render,
)

log = logging.getLogger(__name__)

# Binaries looked for on PATH, in order, for each kind.
BINARIES = {
    "opera": ("opera",),
    "chrome": ("google-chrome", "google-chrome-stable"),
    "edge": ("microsoft-edge", "microsoft-edge-stable"),
    "brave": ("brave-browser", "brave"),
    "vivaldi": ("vivaldi-stable", "vivaldi"),
    "chromium": ("chromium", "chromium-browser"),
    "firefox": ("firefox",),
}
# Flathub application ids, for browsers installed only as a flatpak.
FLATPAKS = {
    "opera": "com.opera.Opera",
    "chrome": "com.google.Chrome",
    "edge": "com.microsoft.Edge",
    "brave": "com.brave.Browser",
    "vivaldi": "com.vivaldi.Vivaldi",
    "chromium": "org.chromium.Chromium",
    "firefox": "org.mozilla.firefox",
}
SNAP_BIN = "/snap/"
BROWSERS_CACHE_S = 60      # the default of `applying.browser` asks often; a new browser shows within a minute
TERMINALS = (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"), ("xterm", "-e"))
PASSED_ENV = ("HAL_EMPLOYMENT_HOME", "HAL_EMPLOYMENT_PORT", "HAL_TEST_MODE", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
              "XDG_CACHE_HOME")
LOGIN_ON = "Hal starts when you sign in to this computer."
LOGIN_OFF = "Hal no longer starts when you sign in."
NO_SYSTEMD = "This computer has no systemd user session, so Hal cannot start by itself. Open it from its shortcut."


class LinuxPlatform:
    """Linux, and macOS as far as the same tools exist."""

    name = "linux"

    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 which: Callable[[str], str | None] = shutil.which, home: Path | None = None,
                 execv: Callable[[str, list[str]], None] = os.execv) -> None:
        self._run, self._popen, self._which, self._execv = run, popen, which, execv
        self._home = home
        self._browsers: tuple[float, list[Browser] | None] = (0.0, None)

    # ------------------------------------------------------------------------------------- helpers

    @property
    def home(self) -> Path:
        """The person's home folder."""
        return self._home or Path.home()

    @property
    def units_dir(self) -> Path:
        """`~/.config/systemd/user`, where user units live."""
        return self.home / ".config" / "systemd" / "user"

    def _tool(self, args: list[str], timeout: float = TOOL_TIMEOUT_S) -> subprocess.CompletedProcess | None:
        """Runs a short command; None when it is missing or hangs."""
        if not self._which(args[0]):
            return None
        try:
            return self._run(args, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("%s failed: %s", args[0], e)
            return None

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess | None:
        return self._tool(["systemctl", "--user", *args])

    def _start(self, args: list[str]) -> int:
        """Starts a program that outlives this call, with no console and no output kept."""
        proc = self._popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True)
        return proc.pid

    # ------------------------------------------------------------------------------------- files and pop-ups

    def open_path(self, path: Path) -> None:
        """Opens a file or folder in the desktop's own program."""
        self._start(["xdg-open" if self._which("xdg-open") else "open", str(path)])

    def notify(self, title: str, body: str, *, urgent: bool = False) -> None:
        """A desktop pop-up through notify-send; nothing when it is missing."""
        self._tool(["notify-send", "-a", "Hal Employment", *(["-u", "critical"] if urgent else []), title, body])

    def documents_folder(self) -> Path:
        """The folder a person opens to find Hal's documents."""
        return paths.documents_dir()

    # ------------------------------------------------------------------------------------- browsers

    def _flatpaks(self) -> set[str]:
        done = self._tool(["flatpak", "list", "--app", "--columns=application"])
        if done is None or done.returncode != 0:
            return set()
        return {line.strip() for line in done.stdout.splitlines() if line.strip()}

    def browsers(self) -> list[Browser]:
        """Browsers on PATH, then those installed only as a flatpak; snaps and flatpaks are marked."""
        at, found_before = self._browsers
        if found_before is not None and time.monotonic() - at < BROWSERS_CACHE_S:
            return list(found_before)
        out = self._find_browsers()
        self._browsers = (time.monotonic(), out)
        return list(out)

    def _find_browsers(self) -> list[Browser]:
        out: list[Browser] = []
        flatpaks: set[str] | None = None
        for kind in KINDS:
            exe = next((p for p in (self._which(b) for b in BINARIES[kind.id]) if p), None)
            if exe:
                real = os.path.realpath(exe) if os.path.exists(exe) else exe
                snap = exe.startswith(SNAP_BIN) or real.startswith(SNAP_BIN) or os.path.basename(real) == "snap"
                out.append(found(kind.id, Path(exe), sandboxed=snap))
                continue
            if flatpaks is None:
                flatpaks = self._flatpaks()
            if FLATPAKS[kind.id] in flatpaks:
                tool = self._which("flatpak") or "flatpak"
                out.append(found(kind.id, Path(tool), sandboxed=True, args=("run", FLATPAKS[kind.id])))
        return out

    def open_in_browser(self, browser: Browser, url: str) -> None:
        """Opens `url` in the person's chosen browser (pair page, job tabs)."""
        self._start(browser.argv(url))

    def open_browser_window(self, browser: Browser) -> None:
        """Opens a new window of the person's chosen browser, its address bar ready for a paste.

        Browser pages such as `edge://extensions` cannot be opened this way: Chromium browsers drop an
        internal address given on their command line and show a new tab instead (Edge, Windows 11 test VM,
        2026-10-23), so the setup step copies that address for the person to paste.
        """
        self._start(browser.argv(NEW_WINDOW))

    def open_app_window(self, url: str) -> None:
        """The first Chromium-family browser as an app window, else a tab in the default browser."""
        chromium = next((b for b in self.browsers() if b.supported), None)
        if chromium is None:
            webbrowser.open(url)
            return
        self._start(chromium.argv(f"--app={url}"))

    # ------------------------------------------------------------------------------------- children

    def run_hidden(self, args: list[str], **kw) -> subprocess.CompletedProcess:
        """Runs a command with no window and waits for it."""
        kw.setdefault("timeout", TOOL_TIMEOUT_S)
        return self._run(args, **kw)

    def popen_hidden(self, args: list[str], **kw) -> subprocess.Popen:
        """Starts a command with no window in its own process group, so `kill_tree` ends all of it."""
        return self._popen(args, start_new_session=True, **kw)

    def kill_tree(self, proc: subprocess.Popen) -> None:
        """Ends a child started by `popen_hidden` and everything it started."""
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(STOP_GRACE_S)
        except ProcessLookupError:
            return

    def spawn_detached(self, args: list[str], *, unit: str | None = None) -> int:
        """Starts a program that outlives the server: a transient user unit when systemd is there."""
        if unit and self._which("systemd-run"):
            env = [f"--setenv={k}={os.environ[k]}" for k in PASSED_ENV if os.environ.get(k)]
            return self._start(["systemd-run", "--user", f"--unit={unit}", "--collect", "--quiet",
                                f"--working-directory={paths.code_dir()}", *env, *args])
        return self._start(args)

    def open_console(self, args: list[str], title: str) -> int:
        """A visible terminal running `args` (the Claude sign-in)."""
        for term, flag in TERMINALS:
            if self._which(term):
                extra = ["--title", title] if term == "gnome-terminal" else []
                return self._start([term, *extra, flag, *args])
        raise RuntimeError("No terminal program was found. Install one, such as GNOME Terminal or xterm.")

    def machine(self) -> dict:
        """{memory_gb, gpu, cpus} for Settings and the AI choice."""
        memory_gb = None
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    memory_gb = round(int(line.split()[1]) / 1024 / 1024, 1)
        except OSError:
            pass
        done = self._tool(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        gpu = done.stdout.strip().splitlines()[0] if done and done.returncode == 0 and done.stdout.strip() else None
        return {"memory_gb": memory_gb, "gpu": gpu, "cpus": os.cpu_count()}

    # ------------------------------------------------------------------------------------- start at login

    def service(self) -> str:
        """"hal-unit" when Hal's own unit runs the server, "app-unit" for a user install, else "none"."""
        if (self.units_dir / HAL_UNIT).exists():
            return "hal-unit"
        if (self.units_dir / APP_UNIT).exists():
            return "app-unit"
        return "none"

    def app_unit_text(self) -> str:
        """The user unit a Linux install gets, filled for this checkout."""
        return render(APP_UNIT, python=sys.executable, code_dir=str(paths.code_dir()))

    def desktop_entry_text(self) -> str:
        """The menu entry that opens Hal, filled for this checkout."""
        return render("hal-employment.desktop", python=sys.executable, code_dir=str(paths.code_dir()))

    def start_at_login(self, on: bool) -> str:
        """Enables or disables the app unit; with Hal's own unit, leaves it to that and says so."""
        if self.service() == "hal-unit":
            return MANAGED_BY_HAL
        if not self._which("systemctl"):
            return NO_SYSTEMD
        unit = self.units_dir / APP_UNIT
        if on:
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(self.app_unit_text(), encoding="utf-8")
            self._systemctl("daemon-reload")
            self._systemctl("enable", APP_UNIT)
            return LOGIN_ON
        if unit.exists():
            self._systemctl("disable", APP_UNIT)
        return LOGIN_OFF

    def start_app_unit(self) -> bool:
        """Starts the app unit now (a user install's first start); False without systemd."""
        done = self._systemctl("start", APP_UNIT)
        return done is not None and done.returncode == 0

    def remove_app_unit(self) -> None:
        """Stops, disables and deletes the app unit (`install --remove`); Hal's own unit is never touched."""
        unit = self.units_dir / APP_UNIT
        if unit.exists():
            self._systemctl("disable", "--now", APP_UNIT)
            unit.unlink()
            self._systemctl("daemon-reload")

    def start_at_login_state(self) -> bool | None:
        """True when Hal starts at login, None when this computer cannot say."""
        if self.service() == "hal-unit":
            return True
        done = self._systemctl("is-enabled", APP_UNIT)
        if done is None:
            return None
        return done.stdout.strip() == "enabled"

    def restart_server(self) -> None:
        """Restarts the server through its unit, else by running it again in this process."""
        kind = self.service()
        if kind in ("hal-unit", "app-unit"):
            # --no-block: the restart stops this process, and with it a waiting systemctl
            self._systemctl("--no-block", "restart", HAL_UNIT if kind == "hal-unit" else APP_UNIT)
            return
        # the lock file and the listening socket close on exec, so the new server can take them
        self._execv(sys.executable, [sys.executable, "-m", "jobhunt", "serve", "--background"])

    # ------------------------------------------------------------------------------------- old timers

    def legacy_timers_enabled(self) -> list[str]:
        """The old `jobhunt-*` timers that are still enabled (5 s each); none without systemd."""
        out = []
        for timer in LEGACY_TIMERS:
            done = self._systemctl("is-enabled", timer)
            if done is not None and done.stdout.strip() in ("enabled", "enabled-runtime", "static"):
                out.append(timer)
        return out

    def _unit_files(self, timer: str) -> list[Path]:
        service = timer.removesuffix(".timer") + ".service"
        return [self.units_dir / name for name in (timer, service) if (self.units_dir / name).exists()]

    def retire_legacy_timers(self, dry_run: bool = False) -> list[str]:
        """Disables and stops the old timers and moves their files aside; returns the names it retired."""
        todo = [t for t in LEGACY_TIMERS if self._unit_files(t) or t in self.legacy_timers_enabled()]
        if dry_run or not todo:
            return todo
        retired = self.units_dir / RETIRED_DIR
        retired.mkdir(parents=True, exist_ok=True)
        for timer in todo:
            self._systemctl("disable", "--now", timer)
            for path in self._unit_files(timer):
                path.rename(retired / path.name)
        self._systemctl("daemon-reload")
        log.info("retired the old timers: %s", ", ".join(todo))
        return todo

    def restore_legacy_timers(self) -> list[str]:
        """Moves the retired unit files back, then enables and starts the timers again."""
        retired = self.units_dir / RETIRED_DIR
        back = []
        for timer in LEGACY_TIMERS:
            service = timer.removesuffix(".timer") + ".service"
            for name in (timer, service):
                if (retired / name).exists() and not (self.units_dir / name).exists():
                    (retired / name).rename(self.units_dir / name)
                    if name == timer:
                        back.append(timer)
        if back:
            self._systemctl("daemon-reload")
            for timer in back:
                self._systemctl("enable", "--now", timer)
        return back

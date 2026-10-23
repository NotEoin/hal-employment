"""The platform layer on Windows: App Paths, the Startup folder, toasts, Edge windows.

Design decisions, each with its reason:

- Browsers are found from the registry's App Paths (per user, then per machine) and then from the usual
  install folders. Opera installs per user under `%LOCALAPPDATA%\\Programs\\Opera` and often has no App
  Paths entry, so that folder is always looked at.
- The app window is Microsoft Edge with `--app=`, whatever browser the person uses, because Edge is
  always present on Windows 10 and 11.
- Toasts go through PowerShell's WinRT bridge, and say where to look in their words ("Open Hal: ..."),
  because clicking a toast does nothing yet.
- The registry reader, the file check and the command runner are constructor arguments, so the Linux
  test run exercises this module with a fake registry.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from .. import paths
from . import KINDS, NEW_WINDOW, STOP_GRACE_S, TOOL_TIMEOUT_S, Browser, found

log = logging.getLogger(__name__)

# subprocess's names for these exist only on Windows; the values are from the Win32 process creation flags.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
TOAST_TIMEOUT_S = 15
RESTART_DELAY_S = 3            # the new server waits this long for the old one to let go of its lock
APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
HIVES = ("HKCU", "HKLM")
STARTUP_LINK = "Hal Employment.lnk"
LOGIN_ON = "Hal starts when you sign in to Windows."
LOGIN_OFF = "Hal no longer starts when you sign in."

# The program each kind registers under App Paths.
EXES = {"opera": "opera.exe", "chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe",
        "vivaldi": "vivaldi.exe", "chromium": "chromium.exe", "firefox": "firefox.exe"}
# Usual install folders, as (environment variable, path under it); U setup.ps1:516-522 for the others.
FOLDERS = {
    "opera": (("LOCALAPPDATA", r"Programs\Opera\opera.exe"), ("LOCALAPPDATA", r"Programs\Opera\launcher.exe"),
              ("ProgramFiles", r"Opera\opera.exe"), ("ProgramFiles", r"Opera\launcher.exe")),
    "chrome": (("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
               ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
               ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe")),
    "edge": (("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
             ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe")),
    "brave": (("ProgramFiles", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
              ("ProgramFiles(x86)", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
              ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\Application\brave.exe")),
    "vivaldi": (("LOCALAPPDATA", r"Vivaldi\Application\vivaldi.exe"),
                ("ProgramFiles", r"Vivaldi\Application\vivaldi.exe")),
    "chromium": (("LOCALAPPDATA", r"Chromium\Application\chrome.exe"),),
    "firefox": (("ProgramFiles", r"Mozilla Firefox\firefox.exe"),
                ("ProgramFiles(x86)", r"Mozilla Firefox\firefox.exe")),
}
EDGE_FALLBACK = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$kind = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($kind)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:HAL_TOAST_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:HAL_TOAST_BODY)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""
SHORTCUT = r"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HAL_LINK)
$s.TargetPath = $env:HAL_TARGET
$s.Arguments = '-m jobhunt serve --background'
$s.WorkingDirectory = $env:HAL_CODE
$s.Save()
"""

ANY_SHORTCUT = r"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HAL_LINK)
$s.TargetPath = $env:HAL_TARGET
$s.Arguments = $env:HAL_ARGS
$s.WorkingDirectory = $env:HAL_CODE
$s.Description = $env:HAL_DESCRIPTION
if ($env:HAL_ICON) { $s.IconLocation = $env:HAL_ICON }
$s.Save()
"""


def _reg_default(hive: str, key: str) -> str | None:
    """The default value of a registry key, or None."""
    import winreg

    root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(root, key) as k:
            value, _ = winreg.QueryValueEx(k, "")
            return str(value) if value else None
    except OSError:
        return None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]


class WindowsPlatform:
    """Windows 10 and 11."""

    name = "windows"

    def __init__(self, *, reg: Callable[[str, str], str | None] = _reg_default,
                 exists: Callable[[str], bool] = os.path.exists, env: dict[str, str] | None = None,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self._reg, self._exists, self._run, self._popen = reg, exists, run, popen
        self._env = env

    @property
    def env(self) -> dict[str, str]:
        """The environment the folders are read from."""
        return self._env if self._env is not None else dict(os.environ)

    def _start(self, args: list[str], flags: int = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) -> int:
        proc = self._popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=flags)
        return proc.pid

    def _powershell(self, script: str, env: dict[str, str], timeout: float) -> None:
        try:
            self.run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-Command", script], env={**os.environ, **env}, timeout=timeout,
                            capture_output=True, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("PowerShell failed: %s", e)

    # ------------------------------------------------------------------------------------- files and pop-ups

    def open_path(self, path: Path) -> None:
        """Opens a file or folder in its own program."""
        os.startfile(str(path))

    def notify(self, title: str, body: str, *, urgent: bool = False) -> None:
        """A toast; its words say where to look, because clicking it does nothing yet."""
        self._powershell(TOAST, {"HAL_TOAST_TITLE": title, "HAL_TOAST_BODY": body}, TOAST_TIMEOUT_S)

    def documents_folder(self) -> Path:
        """The folder a person opens to find Hal's documents."""
        return paths.documents_dir()

    # ------------------------------------------------------------------------------------- browsers

    def _find(self, kind_id: str) -> str | None:
        for hive in HIVES:
            value = self._reg(hive, f"{APP_PATHS}\\{EXES[kind_id]}")
            if value:
                value = value.strip().strip('"')
                if self._exists(value):
                    return value
        for var, rest in FOLDERS[kind_id]:
            base = self.env.get(var)
            if base:
                candidate = str(PureWindowsPath(base) / rest)
                if self._exists(candidate):
                    return candidate
        return None

    def browsers(self) -> list[Browser]:
        """Every known browser found through App Paths or its usual folder."""
        out = []
        for kind in KINDS:
            exe = self._find(kind.id)
            if exe:
                out.append(found(kind.id, Path(exe)))
        return out

    def open_in_browser(self, browser: Browser, url: str) -> None:
        """Opens `url` in the person's chosen browser."""
        self._start(browser.argv(url))

    def open_browser_window(self, browser: Browser) -> None:
        """Opens a new window of the person's chosen browser, its address bar ready for a paste.

        Browser pages such as `edge://extensions` cannot be opened this way: Chromium browsers drop an
        internal address given on their command line and show a new tab instead (Edge, Windows 11 test VM,
        2026-10-23), so the setup step copies that address for the person to paste.
        """
        self._start(browser.argv(NEW_WINDOW))

    def edge(self) -> str:
        """Microsoft Edge's program, which every Windows 10 and 11 has."""
        return self._find("edge") or EDGE_FALLBACK

    def open_app_window(self, url: str) -> None:
        """Hal's window: always Microsoft Edge with `--app=`."""
        self._start([self.edge(), f"--app={url}"])

    # ------------------------------------------------------------------------------------- children

    def run_hidden(self, args: list[str], **kw) -> subprocess.CompletedProcess:
        """Runs a command with no console window and waits for it."""
        kw.setdefault("timeout", TOOL_TIMEOUT_S)
        return self._run(args, creationflags=CREATE_NO_WINDOW, **kw)

    def popen_hidden(self, args: list[str], **kw) -> subprocess.Popen:
        """Starts a command with no console window, in its own process group."""
        return self._popen(args, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, **kw)

    def kill_tree(self, proc: subprocess.Popen) -> None:
        """Ends a child and everything it started."""
        if proc.poll() is not None:
            return
        try:
            self.run_hidden(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
            proc.wait(STOP_GRACE_S)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    def spawn_detached(self, args: list[str], *, unit: str | None = None) -> int:
        """Starts a program that outlives the server and any job object it runs in."""
        return self._start(args, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB)

    def open_console(self, args: list[str], title: str) -> int:
        """A visible console window running `args` (the Claude sign-in)."""
        proc = self._popen(["cmd.exe", "/c", "title", title, "&&", *args], creationflags=CREATE_NEW_CONSOLE)
        return proc.pid

    def machine(self) -> dict:
        """{memory_gb, gpu, cpus} for Settings and the AI choice."""
        memory_gb = None
        try:
            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                memory_gb = round(status.ullTotalPhys / 1024 ** 3, 1)
        except (AttributeError, OSError):
            pass
        gpu = None
        try:
            done = self.run_hidden(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                   capture_output=True, text=True, check=False)
            if done.returncode == 0 and done.stdout.strip():
                gpu = done.stdout.strip().splitlines()[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {"memory_gb": memory_gb, "gpu": gpu, "cpus": os.cpu_count()}

    # ------------------------------------------------------------------------------------- start at login

    def startup_link(self) -> Path:
        """The shortcut in the Startup folder that starts Hal at sign-in."""
        base = PureWindowsPath(self.env.get("APPDATA", ""))
        return Path(str(base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_LINK))

    def special_folder(self, name: str) -> Path:
        """The person's "start_menu", "desktop" or "startup" folder."""
        appdata = PureWindowsPath(self.env.get("APPDATA", ""))
        programs = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if name == "start_menu":
            return Path(str(programs))
        if name == "startup":
            return Path(str(programs / "Startup"))
        if name == "desktop":
            return Path(str(PureWindowsPath(self.env.get("USERPROFILE", "")) / "Desktop"))
        raise ValueError(f"no special folder called {name!r}")

    def make_shortcut(self, link: Path, target: str, arguments: str = "", *, workdir: str = "",
                      description: str = "", icon: str = "") -> None:
        """A `.lnk` made through WScript.Shell (U setup.ps1:112-121), so no extra library is needed."""
        self._powershell(ANY_SHORTCUT, {"HAL_LINK": str(link), "HAL_TARGET": target, "HAL_ARGS": arguments,
                                        "HAL_CODE": workdir, "HAL_DESCRIPTION": description, "HAL_ICON": icon},
                         TOOL_TIMEOUT_S * 2)

    def service(self) -> str:
        """"startup-folder" when the Startup shortcut exists, else "none"."""
        return "startup-folder" if self._exists(str(self.startup_link())) else "none"

    def start_at_login(self, on: bool) -> str:
        """Makes or removes the Startup folder shortcut (U setup.ps1:112-121)."""
        link = self.startup_link()
        if on:
            target = Path(sys.executable).with_name("pythonw.exe")
            self._powershell(SHORTCUT, {"HAL_LINK": str(link), "HAL_CODE": str(paths.code_dir()),
                                        "HAL_TARGET": str(target if target.exists() else sys.executable)},
                             TOOL_TIMEOUT_S * 2)
            return LOGIN_ON
        try:
            link.unlink()
        except FileNotFoundError:
            pass
        return LOGIN_OFF

    def start_at_login_state(self) -> bool | None:
        """True when the Startup shortcut exists."""
        return self.service() == "startup-folder"

    def restart_server(self) -> None:
        """Starts a new server a few seconds from now, then ends this one."""
        self.spawn_detached(["cmd.exe", "/c", f"timeout /t {RESTART_DELAY_S} /nobreak >nul & "
                             f"\"{sys.executable}\" -m jobhunt serve --background"])
        os._exit(0)

    # ------------------------------------------------------------------------------------- old timers

    def legacy_timers_enabled(self) -> list[str]:
        """Windows never had the old timers."""
        return []

    def retire_legacy_timers(self, dry_run: bool = False) -> list[str]:
        """Nothing to retire on Windows."""
        return []

    def restore_legacy_timers(self) -> list[str]:
        """Nothing to restore on Windows."""
        return []

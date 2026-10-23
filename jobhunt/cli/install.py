"""`python -m jobhunt install`: shortcuts, the start-at-login entry, the install record, and removal.

The bootstraps run `install --windows` or `install --linux` after `uv sync`; the owner's own checkout uses
`install --dev`; `install --remove` is the uninstall entry.

Design decisions, each with its reason:

- `install` never writes over a file or symlink it did not make, and refuses outright to replace a symlink
  into hal-ecosystem: on the owner's desktop `~/.local/bin/hal-employment` is Hal's own command, so the
  shim is `hal-employment-cli` and every file this writes carries `MARK`.
- On Windows the install record names the bundled PortableGit's `git.exe` by full path, whatever git is on
  PATH, so the updater always runs the version that carries Git Credential Manager.
- The computer is reached through `Installer`'s constructor arguments (folders, the shortcut maker, the
  registry writer, the unit calls), so the tests run the real steps in a temporary home.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ..core import paths, updates

MARK = "made by hal-employment install"
SHIM = "hal-employment-cli"
HAL_COMMAND = "hal-employment"                 # Hal's own command on the owner's desktop: never touched
ECOSYSTEM = "hal-ecosystem"
DESKTOP_FILE = "hal-employment.desktop"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\HalEmployment"
REMOVE_DELAY_S = 3                             # the app folder goes once this process has ended
KEEP_DATA = "Keep your data? [Y/n] "


class InstallRefused(RuntimeError):
    """A step that would overwrite something `install` did not make."""


def _ours(path: Path) -> bool:
    """True when `path` is absent or a plain file this command wrote."""
    if path.is_symlink():
        return False
    if not path.exists():
        return True
    try:
        return MARK in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _into_ecosystem(path: Path) -> bool:
    if not path.is_symlink():
        return False
    return ECOSYSTEM in str(Path(os.readlink(path))) or ECOSYSTEM in str(path.resolve())


def _write_ours(path: Path, text: str, *, executable: bool = False) -> None:
    if _into_ecosystem(path):
        raise InstallRefused(f"{path} belongs to your Hal setup, so it was left alone.")
    if not _ours(path):
        raise InstallRefused(f"{path} was not made by this install, so it was left alone.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(0o755)


class Installer:
    """The install steps for one computer; every outside call is a constructor argument."""

    def __init__(self, *, code: Path | None = None, home: Path | None = None, system: str | None = None,
                 platform=None, say: Callable[[str], None] = print,
                 reg_write: Callable[[str, dict[str, str] | None], None] | None = None,
                 spawn: Callable[[list[str]], None] | None = None) -> None:
        self.code = Path(code or paths.code_dir())
        self.home = Path(home or Path.home())
        self.system = system or ("windows" if os.name == "nt" else "linux")
        self._platform = platform
        self.say = say
        self._reg_write = reg_write or _reg_write
        self._spawn = spawn or _spawn

    @property
    def platform(self):
        """The platform layer (a fake in tests)."""
        if self._platform is None:
            from ..core import platform

            self._platform = platform.current()
        return self._platform

    @property
    def tools(self) -> Path:
        """`<install>/tools`, beside `app/`."""
        return self.code.parent / "tools"

    # ------------------------------------------------------------------------------------ the record

    def record(self, *, git: str, uv: str, extras: list[str]) -> Path:
        """Writes `state/install.json` for a user install."""
        from ..core import clock

        inst = updates.Install(kind="user", code=str(self.code), tools=str(self.tools), git=git, uv=uv,
                               python_dir=os.environ.get("UV_PYTHON_INSTALL_DIR", str(self.tools / "python")),
                               uv_cache=os.environ.get("UV_CACHE_DIR", str(self.tools / "uv-cache")),
                               extras=extras, installed=clock.iso())
        return updates.write_install(inst)

    # ------------------------------------------------------------------------------------ Windows

    def windows_git(self) -> str:
        """The bundled PortableGit's git.exe, always, even when another git is on PATH."""
        return str(self.tools / "git" / "cmd" / "git.exe")

    def windows(self, extras: list[str]) -> None:
        """Start menu and desktop shortcuts, Update Hal, the Startup entry, the uninstall entry, the record."""
        plat = self.platform
        scripts = self.code / ".venv" / "Scripts"
        pythonw = str(scripts / "pythonw.exe")
        for folder in ("start_menu", "desktop"):
            plat.make_shortcut(plat.special_folder(folder) / "Hal Employment.lnk", pythonw, "-m jobhunt open",
                               workdir=str(self.code), description="Find jobs and prepare applications")
        plat.make_shortcut(plat.special_folder("start_menu") / "Update Hal Employment.lnk",
                           str(self.code / "scripts" / "update-hal.cmd"), workdir=str(self.code),
                           description="Update Hal Employment")
        self.say(plat.start_at_login(True))
        python = str(scripts / "python.exe")
        self._reg_write(UNINSTALL_KEY, {"DisplayName": "Hal Employment", "Publisher": "Hal Employment",
                                        "UninstallString": f'"{python}" -m jobhunt install --remove',
                                        "InstallLocation": str(self.code.parent), "NoModify": "1", "NoRepair": "1"})
        self.record(git=self.windows_git(), uv=str(self.tools / "uv" / "uv.exe"), extras=extras)
        self.say("Made the shortcuts in the Start menu and on the desktop.")

    # ------------------------------------------------------------------------------------ Linux

    def shim_path(self) -> Path:
        """`~/.local/bin/hal-employment-cli`."""
        return self.home / ".local" / "bin" / SHIM

    def desktop_path(self) -> Path:
        """`~/.local/share/applications/hal-employment.desktop`."""
        return self.home / ".local" / "share" / "applications" / DESKTOP_FILE

    def linux_files(self) -> None:
        """The command-line shim and the menu entry (all `--dev` makes)."""
        python = self.code / ".venv" / "bin" / "python"
        shim = f'#!/bin/sh\n# {MARK}: runs Hal Employment\'s command line\nexec "{python}" -m jobhunt "$@"\n'
        _write_ours(self.shim_path(), shim, executable=True)
        entry = (f"[Desktop Entry]\nType=Application\nName=Hal Employment\n"
                 f"Comment=Find jobs and prepare applications\nExec={python} -m jobhunt open\n"
                 f"Icon={self.code / 'jobhunt' / 'web' / 'static' / 'app' / 'icon.svg'}\nTerminal=false\n"
                 f"Categories=Office;\n# {MARK}\n")
        _write_ours(self.desktop_path(), entry)
        self.say(f"Made {self.shim_path()} and the menu entry.")

    def linux(self, extras: list[str]) -> None:
        """The shim, the menu entry, the user unit (unless Hal's own unit runs the server), the record."""
        self.linux_files()
        plat = self.platform
        if plat.service() == "hal-unit":
            self.say("Your Hal setup starts Hal Employment, so no unit of its own was made.")
        else:
            self.say(plat.start_at_login(True))
            plat.start_app_unit()
        self.record(git=shutil.which("git") or "git", uv=str(self.tools / "uv"), extras=extras)

    # ------------------------------------------------------------------------------------ removal

    def remove(self, keep_data: bool) -> None:
        """Removes shortcuts, the start-at-login entry and the app folder; the data only when asked."""
        plat = self.platform
        user_install = not updates.read_install().developer
        if self.system == "windows":
            for folder, name in (("start_menu", "Hal Employment.lnk"), ("desktop", "Hal Employment.lnk"),
                                 ("start_menu", "Update Hal Employment.lnk")):
                (plat.special_folder(folder) / name).unlink(missing_ok=True)
            plat.start_at_login(False)
            self._reg_write(UNINSTALL_KEY, None)
        else:
            for path in (self.shim_path(), self.desktop_path()):
                if path.exists() and _ours(path):
                    path.unlink()
            plat.remove_app_unit()
        if not keep_data:
            for folder in {paths.data_dir(), paths.config_dir(), paths.cache_dir()}:
                shutil.rmtree(folder, ignore_errors=True)
            self.say("Deleted your data.")
        else:
            self.say(f"Kept your data in {paths.data_dir()}.")
        if user_install:
            self._remove_code()
        self.say("Hal Employment is removed.")

    def _remove_code(self) -> None:
        if self.system == "windows":
            # Windows cannot delete the running Python, so a detached cmd does it once this process ends
            self._spawn(["cmd.exe", "/c", f'timeout /t {REMOVE_DELAY_S} /nobreak >nul & rmdir /s /q "{self.code}"'])
        else:
            shutil.rmtree(self.code, ignore_errors=True)


def _reg_write(key: str, values: dict[str, str] | None) -> None:
    """Writes (or with None, deletes) a key under HKEY_CURRENT_USER."""
    if os.name != "nt":
        return
    import winreg

    if values is None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        for name, value in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _spawn(args: list[str]) -> None:
    from ..core import platform

    platform.current().spawn_detached(args)


def _keep_data(args) -> bool:
    if args.delete_data:
        return False
    if not sys.stdin or not sys.stdin.isatty():
        return True
    try:
        return input(KEEP_DATA).strip().lower() not in ("n", "no")
    except EOFError:
        return True


def cmd_install(args) -> None:
    """Runs the chosen install step and prints what it did."""
    extras = [e for e in (args.extras or "").split(",") if e]
    inst = Installer()
    try:
        if args.windows:
            inst.windows(extras)
        elif args.linux:
            inst.linux(extras)
        elif args.dev:
            inst.linux_files()
        elif args.remove:
            inst.remove(_keep_data(args))
        else:
            sys.exit("Say what to do: --windows, --linux, --dev or --remove.")
    except (InstallRefused, subprocess.SubprocessError, OSError) as e:
        sys.exit(f"Stopped: {e}")


def register(sub) -> None:
    """Adds `install` to the command line's subcommands."""
    s = sub.add_parser("install", help="make Hal Employment's shortcuts and start-at-login entry, or remove them")
    which = s.add_mutually_exclusive_group()
    which.add_argument("--windows", action="store_true", help="a Windows install made by the install file")
    which.add_argument("--linux", action="store_true", help="a Linux install made by the install file")
    which.add_argument("--dev", action="store_true", help="your own checkout: the command and menu entry only")
    which.add_argument("--remove", action="store_true", help="remove Hal Employment from this computer")
    s.add_argument("--extras", help="the optional parts the install file set up, such as browser,documents")
    s.add_argument("--delete-data", action="store_true", help="with --remove: delete your data too")
    s.set_defaults(fn=cmd_install)

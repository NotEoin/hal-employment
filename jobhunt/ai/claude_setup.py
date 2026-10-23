"""Claude Code on this computer: finding it, installing it, signing in and reading who is signed in.

Someone without a local model thinks with their own Claude Pro or Max plan through the Claude Code command
line, so setup has to install it, sign them in and say which account and plan it found, all without a
console or an admin prompt.

Design decisions, each with its reason:

- The install runs Anthropic's own per-user script (`install.ps1` or `install.sh`), which writes to
  `~/.local/bin` and needs no admin rights. There is no winget fallback, because winget can ask for admin.
  On Windows it runs with `CREATE_NO_WINDOW`, and `~/.local/bin` is added to the user PATH in
  `HKCU\\Environment` (never the machine PATH, which needs admin).
- Sign-in is Anthropic's own flow: `claude auth login` in a small visible window, with Claude's page in the
  browser. Hal never sees or stores the password or the token; it only asks `claude auth status`.
- `claude auth status` prints JSON by default and exits 1 when signed out (checked with Claude Code
  2.1.293). Output that is not JSON comes from an older version, so a text check decides, and the account
  then reads plain "Signed in".
- Every call has a timeout, and the answer of `auth status` is kept for `CACHE_S` so pages and health
  checks never wait on it twice. The sign-in wait asks afresh every `POLL_S`.
- In a test run (`HAL_TEST_MODE=1`) only the fake command line in `tests/support/fake_claude/` may run, the
  real installer never runs and no window opens, unless a test replaces `_popen` or `_open_console`. A real
  command line found on the test computer counts as "not installed", so a test never depends on it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core import paths
from . import claude_cli
from .base import LLMUnavailable

log = logging.getLogger(__name__)

VERSION_TIMEOUT_S = 15
STATUS_TIMEOUT_S = 20
SIGN_OUT_TIMEOUT_S = 20
INSTALL_TIMEOUT_S = 600            # the installer downloads about 100 MB; ten minutes covers slow lines
CLOSE_TIMEOUT_S = 10
POLL_S = 3                         # how often the sign-in wait asks `claude auth status`
SIGN_IN_WAIT_S = 600               # the sign-in step waits up to ten minutes
CACHE_S = 60
STEP_CHARS = 160                   # an installer line longer than this is cut on the step list
CREATE_NO_WINDOW = 0x08000000      # subprocess.CREATE_NO_WINDOW exists only on Windows builds of Python
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002
BROADCAST_TIMEOUT_MS = 5000        # Explorer answers in milliseconds; a hung window must not hold the install

INSTALL_PS1 = "irm https://claude.ai/install.ps1 | iex"
INSTALL_SH = "curl -fsSL https://claude.ai/install.sh | bash"
WINDOW_TITLE = "Claude sign-in"

NOT_INSTALLED = claude_cli.NOT_INSTALLED
SIGNED_OUT = claude_cli.SIGNED_OUT
FREE_PLAN = claude_cli.FREE_PLAN
INSTALL_FAILED = "Claude did not install. Hal kept what the installer said in Activity. Try again."
INSTALL_SLOW = "The Claude installer did not finish within 10 minutes. Try again."
NO_SIGN_IN = "Hal did not see you sign in within 10 minutes. Try again when you are ready."
SIGN_OUT_FAILED = "Claude did not sign out. Try again."

PLANS = {"pro": "Claude Pro", "max": "Claude Max", "team": "Claude Team", "enterprise": "Claude Enterprise",
         "free": "Claude Free"}
_FREE = re.compile(r"\bfree\b", re.I)
_TEXT_SIGNED_OUT = re.compile(r"not (logged|signed) in|logged out|signed out|/login|please (log|sign) in", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# installer advice about PATH and commands to type, which Hal handles itself: on screen it told the person to
# edit Environment Variables and to type `claude --help` (Windows 11 install test)
_HANDLED_BY_HAL = re.compile(r"\bPATH\b|Environment Variables|\.(bash|zsh)rc|^\W*Setup notes:?$|claude --help")

_popen: Callable[..., subprocess.Popen] = subprocess.Popen
_open_console: Callable[[list[str], str], int] | None = None    # None: the platform layer's window
_cache: dict[str, tuple[float, Account | None]] = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class Account:
    """Who `claude auth status` says is signed in; `email` and `plan` are None when it did not say."""

    signed_in: bool
    email: str | None = None
    plan: str | None = None


def _windows() -> bool:
    return sys.platform == "win32"


def _testing() -> bool:
    return os.environ.get(paths.ENV_TEST) == "1" and os.environ.get(claude_cli.REAL_AI_ENV) != "claude"


def local_bin() -> Path:
    """Where Anthropic's installer puts `claude`."""
    return Path.home() / ".local" / "bin"


def executable() -> str | None:
    """The `claude` command (shared with the backend): on PATH, else in `~/.local/bin`; None when absent.

    In a test run, anything but the fake counts as absent.
    """
    exe = claude_cli.executable()
    if exe is None:
        return None
    try:
        claude_cli._allowed_in_tests(exe)
    except LLMUnavailable:
        return None
    return exe


def _flags() -> int:
    return CREATE_NO_WINDOW if _windows() else 0


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    """Runs a short command with no window; None when it could not start or ran out of time."""
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("%s did not answer: %s", Path(args[0]).name, type(e).__name__)
        return None


def version() -> str | None:
    """`claude --version`'s first word ("2.1.293"), or None when Claude Code is missing or silent."""
    exe = executable()
    if exe is None:
        return None
    done = _run([exe, "--version"], VERSION_TIMEOUT_S)
    if done is None or done.returncode != 0 or not done.stdout.strip():
        return None
    return done.stdout.strip().split()[0]


def parse_status(returncode: int, out: str) -> Account:
    """What one `claude auth status` run said."""
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if isinstance(data, dict):
        signed = returncode == 0 and data.get("loggedIn") is True
        email = data.get("email") if isinstance(data.get("email"), str) and data.get("email") else None
        plan = data.get("subscriptionType")
        plan = plan if isinstance(plan, str) and plan else None
        return Account(signed, email if signed else None, plan if signed else None)
    return Account(returncode == 0 and bool(out.strip()) and not _TEXT_SIGNED_OUT.search(out))


def auth_status(fresh: bool = False) -> Account | None:
    """Who is signed in to Claude Code, or None when it is not installed; kept for `CACHE_S` unless `fresh`."""
    with _cache_lock:
        hit = _cache.get("status")
        if hit and not fresh and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
    exe = executable()
    if exe is None:
        acct = None
    else:
        done = _run([exe, "auth", "status"], STATUS_TIMEOUT_S)
        acct = Account(False) if done is None else parse_status(done.returncode, done.stdout)
    with _cache_lock:
        _cache["status"] = (time.monotonic(), acct)
    return acct


def forget() -> None:
    """Drops the kept `auth status` answer, so the next read asks Claude Code again."""
    with _cache_lock:
        _cache.clear()


def plan_words(plan: str | None) -> str | None:
    """The plan's name on screen ("Claude Pro"), or None when Claude Code did not name one."""
    if not plan:
        return None
    key = plan.strip().lower().replace("claude_", "").replace("claude ", "")
    return PLANS.get(key) or "Claude " + key.replace("_", " ").title()


def free_plan(plan: str | None) -> bool:
    """True when the plan Claude Code names is a free plan, which cannot run Claude Code."""
    return bool(plan and _FREE.search(plan.replace("_", " ")))


def account_words(acct: Account | None) -> str:
    """The account row: "Signed in as <email> (Claude Pro)", plain "Signed in" when a field is missing."""
    if acct is None:
        return NOT_INSTALLED
    if not acct.signed_in:
        return "Not signed in"
    plan = plan_words(acct.plan)
    if acct.email and plan:
        return f"Signed in as {acct.email} ({plan})"
    return "Signed in"


# ---------------------------------------------------------------------------------------------- install

def install_command() -> list[str]:
    """Anthropic's per-user installer for this computer; it needs no admin rights."""
    if _windows():
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                INSTALL_PS1]
    return ["bash", "-c", INSTALL_SH]


def user_path_with(current: str, folder: str) -> str | None:
    """The user PATH with `folder` added at the end, or None when it is already there."""
    parts = [p for p in current.split(";") if p.strip()]
    if any(os.path.normcase(p.strip().rstrip("\\/")) == os.path.normcase(folder.rstrip("\\/")) for p in parts):
        return None
    return ";".join([*parts, folder])


def add_to_user_path(folder: Path) -> bool:
    """Adds `folder` to the user PATH in HKCU\\Environment (Windows only, no admin); True when it changed."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        new = user_path_with(str(current), str(folder))
        if new is None:
            return False
        winreg.SetValueEx(key, "Path", 0, kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ)
                          else winreg.REG_EXPAND_SZ, new)
    # tells Explorer, so programs started from now on see the new PATH without a sign-out
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG,
                                             BROADCAST_TIMEOUT_MS, ctypes.byref(result))
    return True


def _step_text(line: str) -> str:
    """An installer line as a step, or "" for a blank line or advice Hal follows itself (logged instead)."""
    text = _ANSI.sub("", line).strip()
    if _HANDLED_BY_HAL.search(text):
        log.info("Claude's installer said: %s", text)
        return ""
    return text[:STEP_CHARS - 3] + "..." if len(text) > STEP_CHARS else text


def install(step: Callable[[str], None], checkpoint: Callable[[], None] = lambda: None) -> str:
    """Runs Anthropic's installer with no window, one step per line it prints; returns the version installed.

    Raises RuntimeError with plain words when it fails or runs past `INSTALL_TIMEOUT_S`.
    """
    if _testing() and _popen is subprocess.Popen:
        raise RuntimeError("Tests never run the real Claude installer.")
    proc = _popen(install_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                  text=True, encoding="utf-8", errors="replace", creationflags=_flags())
    timer = threading.Timer(INSTALL_TIMEOUT_S, proc.kill)
    timer.daemon = True
    timer.start()
    started = time.monotonic()
    try:
        for line in proc.stdout:
            checkpoint()
            text = _step_text(line)
            if text:
                step(text)
        code = proc.wait(CLOSE_TIMEOUT_S)
    except BaseException:
        proc.kill()
        raise
    finally:
        timer.cancel()
        proc.stdout.close()
    if time.monotonic() - started >= INSTALL_TIMEOUT_S:
        raise RuntimeError(INSTALL_SLOW)
    if code != 0:
        raise RuntimeError(INSTALL_FAILED)
    if _windows():
        try:
            if add_to_user_path(local_bin()):
                step("Added Claude to your programs list")
        except OSError:
            log.exception("could not add Claude's folder to the user PATH")
    if str(local_bin()) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), str(local_bin())])
    forget()
    found = version()
    if found is None:
        raise RuntimeError(INSTALL_FAILED)
    return found


# ---------------------------------------------------------------------------------------------- sign in and out

def _console(args: list[str], title: str) -> int:
    if _open_console is not None:
        return _open_console(args, title)
    if _testing():
        raise RuntimeError("Tests never open a window.")
    from ..core import platform

    return platform.current().open_console(args, title)


def _close_console(pid: int) -> None:
    """Closes the sign-in window on Windows; elsewhere the window closes when `claude auth login` ends.

    Only Windows is closed by pid: there the pid is Hal's own `cmd.exe` child, while a Linux terminal's
    launcher has usually exited, so its pid may already belong to another program.
    """
    if not _windows() or _open_console is not None:
        return
    _run(["taskkill", "/PID", str(pid), "/T", "/F"], CLOSE_TIMEOUT_S)


def open_sign_in() -> int:
    """Opens the small "Claude sign-in" window running `claude auth login`; returns its process id."""
    exe = executable()
    if exe is None:
        raise RuntimeError(NOT_INSTALLED)
    forget()
    return _console([exe, "auth", "login"], WINDOW_TITLE)


def wait_for_sign_in(checkpoint: Callable[[], None] = lambda: None, wait_s: float = SIGN_IN_WAIT_S) -> Account:
    """Asks `claude auth status` every `POLL_S` until it says signed in; RuntimeError after `wait_s`."""
    deadline = time.monotonic() + wait_s
    while True:
        checkpoint()
        acct = auth_status(fresh=True)
        if acct is not None and acct.signed_in:
            return acct
        if time.monotonic() >= deadline:
            raise RuntimeError(NO_SIGN_IN)
        time.sleep(min(POLL_S, max(0.0, deadline - time.monotonic())))


def sign_in(step: Callable[[str], None], checkpoint: Callable[[], None] = lambda: None,
            wait_s: float = SIGN_IN_WAIT_S) -> Account:
    """Opens Claude's sign-in window, waits for the account, then closes the window Hal opened."""
    now = auth_status(fresh=True)
    if now is None:
        raise RuntimeError(NOT_INSTALLED)
    if now.signed_in:
        words = account_words(now)
        step("Already " + words[0].lower() + words[1:])
        return now
    step("Opening Claude's sign-in page and a small window called claude")
    pid = open_sign_in()
    step("Waiting for you to sign in")
    acct = wait_for_sign_in(checkpoint, wait_s)
    _close_console(pid)
    step(account_words(acct))
    return acct


def sign_out() -> None:
    """Signs Claude Code out on this computer (`claude auth logout`); RuntimeError when it would not."""
    exe = executable()
    if exe is None:
        raise RuntimeError(NOT_INSTALLED)
    done = _run([exe, "auth", "logout"], SIGN_OUT_TIMEOUT_S)
    forget()
    if done is None or done.returncode != 0:
        raise RuntimeError(SIGN_OUT_FAILED)


# ---------------------------------------------------------------------------------------------- which AI fits

STRONG_MEMORY_GB = 16      # the smallest useful local writer needs about this much beside the person's own work


def has_battery() -> bool | None:
    """True on a laptop, False on a desktop, None when this computer does not say."""
    if _windows():
        try:
            import ctypes

            class _Power(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

            power = _Power()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(power)):
                return None
            no_battery, unknown = 128, 255   # BatteryFlag values in the SYSTEM_POWER_STATUS documentation
            return None if power.BatteryFlag == unknown else power.BatteryFlag != no_battery
        except (AttributeError, OSError):
            return None
    supply = Path("/sys/class/power_supply")
    try:
        return any(p.name.startswith("BAT") for p in supply.iterdir())
    except OSError:
        return None


def _memory(gb: float | None) -> str:
    return f"{gb:g} GB of memory" if gb else "an unknown amount of memory"


def recommend(machine: dict, *, local_running: bool = False) -> dict:
    """Which AI fits this computer: {pick, why, machine}; `machine` is {memory_gb, gpu, laptop}.

    A graphics card with enough memory beside it, or one already running a local AI, picks "on this computer";
    anything else picks the Claude subscription, because a local AI without a strong card is too slow to use.
    """
    gpu, memory = machine.get("gpu"), machine.get("memory_gb")
    this = "This laptop" if machine.get("laptop") else "This computer"
    if gpu and ((memory or 0) >= STRONG_MEMORY_GB or local_running):
        why = f"{this} has {_memory(memory)} and a strong graphics card ({gpu}), so the AI can run here."
        return {"pick": "local", "why": why, "machine": machine}
    if gpu:
        why = f"{this} has {_memory(memory)}, which is too little beside its graphics card."
    else:
        why = f"{this} has {_memory(memory)} and no suitable graphics card."
    return {"pick": "claude_cli", "why": why, "machine": machine}

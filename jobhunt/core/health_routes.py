"""Routes for "Is Hal working?", its Fix buttons, the help file and the browsers Hal found.

Each area lists its checks in a `health.py` or `*_health.py` module as `CHECKS`, objects with `id`, `name`
and `run()`; `run()` answers an object with `ok`, `level` ("ok", "warn" or "bad"), `text` and an optional
`fix` (with `label` and `action` or `route`). The contract types live in `core/health.py`; this module reads
them by attribute so it works with any area's checks.

Design notes:
- Every check runs in its own thread with a time limit, because a check may ask a slow program (Claude Code,
  a keyring, `nvidia-smi`). A check that is too slow or raises becomes a "warn" row in plain words; one bad
  check never hides the others.
- The browsers Hal found come from the platform layer (`core.platform.current().browsers()`) when it is
  there; until then a small search of the usual places stands in, so Settings can mark them "(found)".
"""
from __future__ import annotations

import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from ..web.router import NotFound, Request, route
from . import plugins

log = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 5.0      # a check slower than this is shown as "did not answer"; the page waits no longer
CHECK_THREADS = 8
LEVELS = ("ok", "warn", "bad")
SLOW_WORDS = "This check did not answer in time. Hal tries again when you open this page."
FAILED_WORDS = "This check could not run. The log has the details."

# Program names on Linux and install folders on Windows; Firefox is found but not supported.
LINUX_BINARIES = {
    "opera": ("opera",), "chrome": ("google-chrome", "google-chrome-stable"),
    "edge": ("microsoft-edge", "microsoft-edge-stable"), "brave": ("brave-browser", "brave"),
    "vivaldi": ("vivaldi-stable", "vivaldi"), "chromium": ("chromium", "chromium-browser"), "firefox": ("firefox",),
}
WINDOWS_PATHS = {
    "opera": (r"LOCALAPPDATA\Programs\Opera\opera.exe", r"ProgramFiles\Opera\opera.exe"),
    "chrome": (r"ProgramFiles\Google\Chrome\Application\chrome.exe",
               r"ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
               r"LOCALAPPDATA\Google\Chrome\Application\chrome.exe"),
    "edge": (r"ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
             r"ProgramFiles\Microsoft\Edge\Application\msedge.exe"),
    "brave": (r"ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
              r"LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"),
    "vivaldi": (r"LOCALAPPDATA\Vivaldi\Application\vivaldi.exe", r"ProgramFiles\Vivaldi\Application\vivaldi.exe"),
    "chromium": (r"LOCALAPPDATA\Chromium\Application\chrome.exe",),
    "firefox": (r"ProgramFiles\Mozilla Firefox\firefox.exe", r"ProgramFiles(x86)\Mozilla Firefox\firefox.exe"),
}


def _all_checks() -> list[Any]:
    checks = []
    for mod in plugins.modules("health"):
        checks.extend(getattr(mod, "CHECKS", None) or [])
    return checks


def _fix(fix: Any) -> dict | None:
    if fix is None:
        return None
    out = {"label": str(fix.label)}
    if getattr(fix, "action", None):
        out["action"] = fix.action
    if getattr(fix, "route", None):
        out["route"] = fix.route
    return out


def _row(check: Any, result: Any) -> dict:
    level = getattr(result, "level", "ok" if result.ok else "warn")
    return {"id": check.id, "name": check.name, "ok": bool(result.ok), "level": level if level in LEVELS else "warn",
            "text": str(result.text), "fix": _fix(getattr(result, "fix", None))}


def run_checks(checks: list[Any] | None = None, timeout: float = CHECK_TIMEOUT_S) -> list[dict]:
    """Runs every check at once, each within `timeout` seconds, and returns the rows in their listed order."""
    checks = _all_checks() if checks is None else checks
    if not checks:
        return []
    pool = ThreadPoolExecutor(max_workers=min(CHECK_THREADS, len(checks)), thread_name_prefix="health")
    try:
        futures = [(c, pool.submit(c.run)) for c in checks]
        rows = []
        for check, fut in futures:
            try:
                rows.append(_row(check, fut.result(timeout=timeout)))
            except FutureTimeout:
                rows.append({"id": check.id, "name": check.name, "ok": False, "level": "warn", "text": SLOW_WORDS,
                             "fix": None})
            except Exception:  # one broken check must not hide the rest; the log keeps why
                log.exception("health check %s failed", check.id)
                rows.append({"id": check.id, "name": check.name, "ok": False, "level": "warn", "text": FAILED_WORDS,
                             "fix": None})
        return rows
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def worst(rows: list[dict]) -> str:
    """The worst level among the rows ("ok" when there are none)."""
    return max((r["level"] for r in rows), key=LEVELS.index, default="ok")


@route("GET", "/api/v2/health", slow=True)
def health(req: Request) -> dict:
    """Is Hal working? Every check's row and the worst level."""
    rows = run_checks()
    return {"checks": rows, "worst": worst(rows)}


@route("POST", "/api/v2/health/{check_id}/fix")
def fix(req: Request) -> dict:
    """Runs a check's Fix: starts its action, or names the page to open."""
    check = next((c for c in _all_checks() if c.id == req.params["check_id"]), None)
    if check is None:
        raise NotFound("Hal has no check by that name.")
    row = run_checks([check])[0]
    if not row["fix"]:
        raise NotFound("This check has nothing to fix right now.")
    if row["fix"].get("route"):
        return {"route": row["fix"]["route"]}
    from . import runner

    return {"action_id": runner.start(row["fix"]["action"], why=f"You pressed {row['fix']['label']}",
                                      priority=0, session_id=getattr(req.session, "id_hash", None),
                                      remote=req.remote)}


@route("POST", "/api/v2/help-file", slow=True)
def help_file(req: Request) -> dict:
    """Makes a help file with the person taken out; the page shows the words before anyone sends it."""
    from . import helpfile

    return helpfile.make(req.con)


def _platform_browsers() -> list[Any] | None:
    try:
        from . import platform as plat
        return list(plat.current().browsers())
    except (ImportError, AttributeError, NotImplementedError, OSError):
        return None


def _windows_found(bid: str) -> bool:
    for raw in WINDOWS_PATHS.get(bid, ()):
        var, rest = raw.split("\\", 1)
        base = os.environ.get(var)
        if base and Path(base, rest).is_file():
            return True
    return False


def found_browser_ids() -> set[str]:
    """The ids of the browsers installed on this computer."""
    listed = _platform_browsers()
    if listed is not None:
        return {b.id for b in listed}
    if os.name == "nt":
        return {bid for bid in WINDOWS_PATHS if _windows_found(bid)}
    return {bid for bid, names in LINUX_BINARIES.items() if any(shutil.which(n) for n in names)}


def browsers() -> list[dict]:
    """Every browser the setting lists, with whether it was found and whether Hal can fill forms in it."""
    from .settings.catalog import applying

    found = found_browser_ids()
    return [{"id": bid, "name": words, "found": bid in found, "supported": bid not in applying.UNSUPPORTED_BROWSERS}
            for bid, words in applying.BROWSERS]


@route("GET", "/api/v2/health/browsers")
def browsers_found(req: Request) -> dict:
    """The browsers for Settings, Applying: found ones are marked, Firefox is not supported yet."""
    return {"browsers": browsers()}

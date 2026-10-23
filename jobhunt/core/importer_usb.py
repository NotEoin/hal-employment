"""A USB stick install, copied into this Hal and retired.

Run by the Windows bootstrap when `%USERPROFILE%\\HalEmployment\\data\\jobs.db` exists, by
`python -m jobhunt import --from-usb-install PATH`, or from setup ("We found an older Hal on this computer").
It copies and never moves: the old folder is only renamed `HalEmployment.old-<date>` at the end.

Design decisions, each with its reason:

- Everything that touches the computer outside Hal's folders (stopping the old Hal, its shortcuts, the user
  PATH, the old /jobs command) goes through `System`, so tests drive the real steps with fakes.
- The database is copied with SQLite's backup API, never as bytes, so a WAL left behind by the old Hal is
  folded in; it then migrates through `db.connect()`, which backs up first. A Hal that already holds jobs
  is never overwritten: the import stops and says so.
- `private.toml` is copied byte for byte and compared by SHA-256; it is never parsed, printed or logged.
- Settings typed by hand on the stick (`22,000`, `15 miles`) go through `settings.check`, which parses
  them. A key this Hal cannot read is reported and left out rather than stopping the whole copy, because
  the person cannot edit the old file.
- A secret file is deleted only after the stored value reads back the same, and is overwritten with zeros
  first, so no plaintext copy stays on the disk.
- A desktop shortcut is removed only when it points into the old folder: the new install's own shortcut
  has the same name.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import clock, paths
from .importer import ImportStopped, Report
from .settings import legacy_map

log = logging.getLogger(__name__)

SECRET_FILES = {"gmail-app-password": "email.app_password", "adzuna": "sites.adzuna_key", "reed": "sites.reed_key"}
OLD_JOBS = {"discover": "find.discover", "mail": "mail.check", "digest": "notify.digest"}   # U schedule.json keys
FIXED = {"ai.provider": "claude_cli", "scoring.preset": "humanities_grad"}
STARTUP_LINK = "Hal Employment (background).lnk"          # U setup.ps1:566-574
DESKTOP_LINK = "Hal Employment.lnk"
OLD_SUFFIX = ".old-"
POWERSHELL_TIMEOUT_S = 30
DONE = "Copied your jobs, applications and settings from your old Hal. Nothing was deleted."
WHY = "Hal found an older Hal on this computer"
ALREADY = "This Hal already has jobs, so the old Hal's were not copied over them."

STOP_OLD = r"""
$old = $env:HAL_OLD
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Where-Object { $_.CommandLine -like '*-m jobhunt*' -and
                 ($_.CommandLine -like "*$old*" -or $_.ExecutablePath -like "$old*") } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
LINK_TARGET = r"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HAL_LINK)
Write-Output ($s.TargetPath + '|' + $s.WorkingDirectory + '|' + $s.Arguments)
"""


def _powershell(script: str, env: dict[str, str]) -> str:
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", script], env={**os.environ, **env}, capture_output=True, text=True,
                              timeout=POWERSHELL_TIMEOUT_S, check=False, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("PowerShell failed: %s", e)
        return ""
    return done.stdout


def _stop_old(folder: Path) -> None:
    if os.name == "nt":
        _powershell(STOP_OLD, {"HAL_OLD": str(folder)})


def _link_points_into(link: Path, folder: Path) -> bool:
    if os.name != "nt" or not link.exists():
        return False
    return str(folder).lower() in _powershell(LINK_TARGET, {"HAL_LINK": str(link)}).lower()


def _user_path() -> str | None:
    if os.name != "nt":
        return None
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        try:
            return str(winreg.QueryValueEx(k, "Path")[0])
        except OSError:
            return None


def _set_user_path(value: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, value)


def _special(name: str) -> Path:
    env = os.environ
    if name == "startup":
        return Path(env.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path(env.get("USERPROFILE", str(Path.home()))) / "Desktop"


@dataclass
class System:
    """What the import asks of the computer; tests pass fakes."""

    stop_old: Callable[[Path], None] = _stop_old
    folder: Callable[[str], Path] = _special                 # "startup" | "desktop"
    link_points_into: Callable[[Path, Path], bool] = _link_points_into
    user_path: Callable[[], str | None] = _user_path
    set_user_path: Callable[[str], None] = _set_user_path
    home: Callable[[], Path] = Path.home
    today: Callable[[], str] = lambda: clock.local_day().isoformat()


@dataclass
class _Counts:
    jobs: int = 0
    files: int = 0
    settings: int = 0
    skipped: list[str] = field(default_factory=list)
    secrets: int = 0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jobs(db: Path) -> int:
    if not db.is_file():
        return 0
    con = sqlite3.connect(db, timeout=30)
    try:
        return con.execute("SELECT count(*) FROM jobs").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def _copy_tree(src: Path, dest: Path) -> int:
    if not src.is_dir():
        return 0
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return sum(1 for p in dest.rglob("*") if p.is_file())


def _flatten(config: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            out.update({f"{key}.{k}": v for k, v in value.items()})
        else:
            out[key] = value
    return out


def settings_from(config: dict) -> tuple[dict[str, Any], list[str]]:
    """(setting -> checked value, old keys left out) from the stick's `config.toml`."""
    from . import settings

    flat = _flatten(config)
    values: dict[str, Any] = {}
    skipped: list[str] = []
    right = flat.pop("applicant.right_to_work", None)
    sponsorship = flat.pop("applicant.sponsorship", None)
    if right is not None or sponsorship is not None:
        values["you.right_to_work"] = "need_sponsorship" if _yes(sponsorship) else "uk_no_visa"
    for old, raw in flat.items():
        try:
            entry = legacy_map.lookup(old)
        except KeyError:
            skipped.append(old)
            continue
        if entry.store != "settings" or entry.setting is None or "U" not in entry.layouts:
            if entry.store not in ("removed", "importer", "sources"):
                skipped.append(old)
            continue
        value = legacy_map.new_value(entry, raw)
        if entry.setting in values and isinstance(values[entry.setting], list) and isinstance(value, str):
            value = [*values[entry.setting], value]
        try:
            values[entry.setting] = settings.check(entry.setting, value)
        except (ValueError, KeyError, TypeError) as e:
            log.info("stick import left out %s: %s", old, type(e).__name__)
            skipped.append(old)
    values.update(FIXED)
    return values, skipped


def _yes(value: Any) -> bool:
    return str(value).strip().lower() in ("yes", "true", "1", "y")


def _schedule(con: sqlite3.Connection, file: Path) -> int:
    """The stick's last runs (`schedule.json`) into `schedule_state`, so missed slots are not all run again."""
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for old, value in (raw.items() if isinstance(raw, dict) else ()):
        job = OLD_JOBS.get(old)
        last = value.get("last_run") if isinstance(value, dict) else value
        if job and isinstance(last, str):
            con.execute("INSERT INTO schedule_state (job, last_run, state) VALUES (?, ?, 'imported') "
                        "ON CONFLICT(job) DO UPDATE SET last_run = excluded.last_run", (job, last))
            n += 1
    con.commit()
    return n


def _shred(file: Path) -> None:
    size = file.stat().st_size
    with open(file, "r+b") as f:
        f.write(b"\0" * size)
        f.flush()
        os.fsync(f.fileno())
    file.unlink()


def run(folder: Path, dry_run: bool = False, *, say: Callable[[str], None] | None = None,
        system: System | None = None) -> Report:
    """Copies the stick install at `folder` into this Hal, then retires it; a dry run only reports."""
    system = system or System()
    report = Report(say=say or (lambda line: None))
    folder = Path(folder).expanduser()
    old = folder / "data"
    if not (old / "jobs.db").is_file():
        raise ImportStopped(f"There is no older Hal in {folder}: {old / 'jobs.db'} does not exist.")
    target_db = paths.db_path()
    has_jobs = _jobs(target_db)
    config = {}
    if (folder / "config.toml").is_file():
        try:
            config = tomllib.loads((folder / "config.toml").read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ImportStopped(f"The old Hal's settings cannot be read: {e}") from e
    values, skipped = settings_from(config)
    jobs = _jobs(old / "jobs.db")
    secrets_found = [n for n in SECRET_FILES if (old / "secrets" / n).is_file()]
    if dry_run:
        first = (ALREADY + " ") if has_jobs else f"Would copy {jobs} jobs, {len(values)} settings and " \
            f"{len(secrets_found)} saved keys. "
        report.add("plan", first + "Nothing was changed.", jobs=jobs, settings=len(values),
                   secrets=len(secrets_found))
        return report
    if has_jobs:
        raise ImportStopped(ALREADY)

    counts = _Counts(skipped=skipped)
    system.stop_old(folder)
    report.add("stop", "Stopped the old Hal.")
    _copy_database(old / "jobs.db", target_db, report)
    counts.jobs = _jobs(target_db)
    if counts.jobs != jobs:
        raise ImportStopped(f"The copy holds {counts.jobs} jobs but the old Hal has {jobs}. Nothing was deleted.")
    _copy_files(old, folder, counts, report)
    _apply_settings(values, counts, report)
    _move_secrets(old / "secrets", secrets_found, counts, report)
    from .. import db

    con = db.connect()
    try:
        n = _schedule(con, old / "schedule.json")
    finally:
        con.close()
    if n:
        report.add("schedule", f"Kept when the old Hal last searched and checked email ({n}).", jobs=n)
    _retire(folder, system, report)
    from . import activity

    detail = "\n".join(s["line"] for s in report.steps)
    activity.record("settings", "setup.import_usb", DONE, WHY, detail=detail, tone="good")
    report.add("done", DONE)
    return report


def _copy_database(src: Path, dest: Path, report: Report) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        from . import backup

        con = sqlite3.connect(dest)
        try:
            backup.snapshot(con, "before usb import", folder=paths.backups_dir())
        finally:
            con.close()
    a = sqlite3.connect(src, timeout=30)
    b = sqlite3.connect(dest, timeout=30)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()
    from .. import db

    db.connect().close()     # migrates the copy, with its own backup first
    report.add("database", f"Copied {_jobs(dest)} jobs and their history.", jobs=_jobs(dest))


def _copy_files(old: Path, folder: Path, counts: _Counts, report: Report) -> None:
    data = paths.data_dir()
    private = old / "private.toml"
    if private.is_file():
        dest = paths.private_path()
        if dest.exists():
            report.add("private", "Your private answers here were kept; the old ones were not copied.")
        else:
            shutil.copyfile(private, dest)
            if _sha(private) != _sha(dest):
                dest.unlink()
                raise ImportStopped("The copy of your private answers does not match the original. "
                                    "Nothing was deleted.")
            if os.name != "nt":
                dest.chmod(0o600)
            report.add("private", "Copied your private answers.")
    counts.files += _copy_tree(old / "apply", data / "apply")
    cv = data / "import" / "cv"
    for entry in sorted(old.glob("cv-*")):
        if entry.is_file():
            cv.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, cv / entry.name)
            counts.files += 1
        elif entry.is_dir():
            counts.files += _copy_tree(entry, cv / entry.name)
    counts.files += _copy_tree(folder / "cv-sources", cv / "cv-sources")
    counts.files += _copy_tree(folder / "packs", paths.legacy_packs_dir())
    report.add("files", f"Copied {counts.files} files: applications, CVs and documents.", files=counts.files)


def _apply_settings(values: dict[str, Any], counts: _Counts, report: Report) -> None:
    from . import settings
    from .db import tasks

    settings.set_many(values, by="import")
    counts.settings = len(values)
    line = f"Copied {counts.settings} settings."
    if counts.skipped:
        line += f" Left out {len(counts.skipped)} this Hal does not use."
    report.add("settings", line, settings=counts.settings, left_out=len(counts.skipped))
    tasks.run_now("answers.rescope")
    report.add("answers", "Sorted your saved answers by where they apply.")


def _move_secrets(folder: Path, names: list[str], counts: _Counts, report: Report) -> None:
    from .settings import secrets

    kept = []
    for name in names:
        file = folder / name
        value = file.read_text(encoding="utf-8").strip()
        secret = SECRET_FILES[name]
        secrets.put(secret, value)
        if secrets.get(secret) == value:
            _shred(file)
            counts.secrets += 1
        else:
            kept.append(name)
    if names:
        line = f"Moved {counts.secrets} saved keys into this computer's safe store."
        if kept:
            line += f" {len(kept)} could not be checked, so the old file was kept."
        report.add("secrets", line, secrets=counts.secrets)


def _retire(folder: Path, system: System, report: Report) -> None:
    startup = system.folder("startup") / STARTUP_LINK
    if startup.exists():
        startup.unlink()
    desktop = system.folder("desktop") / DESKTOP_LINK
    if desktop.exists() and system.link_points_into(desktop, folder):
        desktop.unlink()
    current = system.user_path()
    if current:
        parts = [p for p in current.split(";") if p]
        kept = [p for p in parts if not p.lower().startswith(str(folder).lower())]
        if len(kept) != len(parts):
            system.set_user_path(";".join(kept))
    jobs_md = system.home() / ".claude" / "commands" / "jobs.md"
    ours = folder / "hal-commands" / "jobs.md"
    if jobs_md.is_file() and ours.is_file() and _sha(jobs_md) == _sha(ours):
        jobs_md.unlink()
    renamed = folder.with_name(f"{folder.name}{OLD_SUFFIX}{system.today()}")
    n = 2
    while renamed.exists():
        renamed = folder.with_name(f"{folder.name}{OLD_SUFFIX}{system.today()}-{n}")
        n += 1
    folder.rename(renamed)
    report.add("retire", f"Switched off the old Hal and kept its folder as {renamed.name}.")


def make_example(folder: Path) -> Path:
    """A small made-up stick install (gate G9's example and the tests): one job, settings, no secrets."""
    data = folder / "data"
    data.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(data / "jobs.db")
    try:
        con.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, company TEXT, title TEXT)")
        con.execute("INSERT OR IGNORE INTO jobs VALUES ('example-museum', 'Harbour Museum', 'Learning Assistant')")
        con.commit()
    finally:
        con.close()
    (folder / "config.toml").write_text('[search]\nradius_miles = "15 miles"\n', encoding="utf-8")
    return folder

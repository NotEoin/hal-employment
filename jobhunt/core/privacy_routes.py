"""Routes for "Privacy and data": where things are kept, what leaves the computer, Export everything and
Delete everything.

Design notes:
- The export never holds a password, a key or a private answer: `private.toml`, the config folder (the
  secrets file and the local key), the browser profile (its sign-ins), screenshots of forms and logs are left
  out by folder, and every file that goes in is searched for each saved secret and, through the AI guard, for
  each saved private answer. A file that holds one is left out and named in the zip's README.
- Delete everything removes Hal's data, settings, cache, its documents folder and the saved secrets, then
  stops Hal. It never touches Hal's own program folder or the person's record folder (their own files), and
  it refuses while the data is still inside the program folder (the old layout), because there the two are
  mixed.
- The export runs as an action (`privacy.export`), since zipping the documents can take a while.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

from ..web.router import BadRequest, Conflict, NotHere, Request, route
from . import clock, paths, runner

log = logging.getLogger(__name__)

CONFIRM_WORD = "delete"
STOP_DELAY_S = 1.0        # long enough for the reply to reach the page before Hal stops
OPEN_TIMEOUT_S = 10       # the file manager answers at once; this only bounds a stuck one
# Data subfolders the export leaves out, and why (shown in the zip's README).
LEFT_OUT_DIRS = {
    "browser-profile": "the browser's sign-ins and cookies",
    "apply": "pictures of forms, which can show private answers",
    "logs": "Hal's logs",
    "backups": "earlier copies of the records (the newest copy is in the zip)",
    "state": "Hal's working files",
    "claude-work": "Hal's working files",
    "exports": "earlier exports and help files",
}
LEFT_OUT_FILES = {"private.toml": "your private answers", "jobs.db": "copied separately",
                  "jobs.db-wal": "copied separately", "jobs.db-shm": "copied separately"}
ALWAYS_LEAVES = ("Searches sent to job sites", "Checks for updates on GitHub")


def _secret_values() -> list[str]:
    from .settings import secrets

    out = []
    for name in secrets.NAMES:
        try:
            value = secrets.get(name, interactive=False)
        except secrets.SecretsLocked:
            continue
        if value:
            out.append(value)
    return out


def leaves() -> list[str]:
    """What leaves this computer, worked out from the settings."""
    from . import settings
    from .settings import secrets

    out = [ALWAYS_LEAVES[0]]
    if secrets.status("email.app_password") == "saved":
        out.append("Reading your email (read only)")
    if settings.get("ai.provider") == "claude_cli":
        out.append("The advert, your CV and the evidence Hal picks, sent to Claude when Hal writes")
    if settings.get("notify.phone"):
        out.append("Phone alerts through ntfy")
    out.append(ALWAYS_LEAVES[1])
    return out


def _record_folder() -> Path:
    from . import settings

    chosen = settings.get("record.folder")
    return Path(chosen).expanduser() if chosen else paths.record_default()


@route("GET", "/api/v2/privacy")
def privacy(req: Request) -> dict:
    """Where your data is kept, whether this window can open it, and what leaves this computer."""
    from .settings import secrets

    where = {"data": str(paths.data_dir()), "config": str(paths.config_dir()),
             "documents": str(paths.applications_dir()), "record": str(_record_folder())}
    stored = [
        {"what": "Jobs, applications, settings and Activity", "where": str(paths.db_path())},
        {"what": "Private answers", "where": str(paths.private_path())},
        {"what": "Passwords and keys", "where": secrets.backend()["name"]},
        {"what": "Documents for each application", "where": where["documents"]},
        {"what": "Your record", "where": where["record"]},
    ]
    return {"where": where, "can_open": not req.remote, "leaves": leaves(), "stored": stored}


def _db_copy(folder: Path) -> Path:
    from .. import db

    target = folder / "jobs.db"
    src = db.connect()
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def _safe(path: Path, secret_values: list[str]) -> str | None:
    """None when the file may go into the export, else the reason it is left out."""
    from ..ai import guard

    data = path.read_bytes()
    if any(v.encode("utf-8") in data for v in secret_values):
        return "it held a saved password or key"
    if guard.contains_private(data.decode("utf-8", "ignore")):
        return "it held one of your private answers"
    return None


def _files_to_export(data: Path, documents: Path) -> list[tuple[Path, str]]:
    out = []
    if data.is_dir():
        for path in sorted(data.rglob("*")):
            rel = path.relative_to(data)
            if not path.is_file() or rel.parts[0] in LEFT_OUT_DIRS or rel.name in LEFT_OUT_FILES:
                continue
            out.append((path, "data/" + rel.as_posix()))
    if documents.is_dir():
        for path in sorted(documents.rglob("*")):
            if path.is_file():
                out.append((path, "Applications/" + path.relative_to(documents).as_posix()))
    return out


def export(target: Path, step=lambda words: None) -> list[str]:
    """Writes the export zip to `target`; returns what was left out, one line each."""
    secret_values = _secret_values()
    left = [f"{name}: {why}" for name, why in LEFT_OUT_DIRS.items()]
    left += ["private.toml: your private answers", "config folder: your passwords, keys and Hal's own key"]
    with tempfile.TemporaryDirectory(prefix="hal-export-") as tmp, \
            zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        step("Copying your records")
        entries = [(_db_copy(Path(tmp)), "data/jobs.db")]
        step("Adding your documents")
        entries += _files_to_export(paths.data_dir(), paths.applications_dir())
        for path, arc in entries:
            reason = _safe(path, secret_values)
            if reason:
                left.append(f"{arc}: {reason}")
                continue
            zf.write(path, arc)
        readme = ["Hal Employment export, made " + clock.iso(), "",
                  "Passwords, keys and private answers are never in an export.", "", "Left out:"]
        zf.writestr("README.txt", "\n".join(readme + ["- " + line for line in left]) + "\n")
    return left


@runner.action("privacy.export", area="settings", label="Making a zip of everything", priority=0,
               why="You pressed Export everything", exclusive="kind", example=lambda world: {})
def _export_action(ctx: runner.Ctx) -> dict:
    from . import helpfile

    folder = helpfile.exports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = clock.iso().replace(":", "").replace("-", "")
    target = folder / f"Hal export {stamp}.zip"
    export(target, ctx.step)
    ctx.step("Saving the zip")
    file_id = helpfile.register_file(ctx.con, target, "export", "Everything Hal keeps")
    ctx.changed("Made a zip of everything Hal keeps.")
    return {"file_id": file_id}


@route("POST", "/api/v2/privacy/export")
def start_export(req: Request) -> dict:
    """Starts making the zip; the action's result names the file."""
    try:
        aid = runner.start("privacy.export", subject="_export", session_id=getattr(req.session, "id_hash", None),
                           remote=req.remote)
    except RuntimeError as e:
        raise Conflict("Hal is already making a zip.") from e
    return {"action_id": aid}


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def folders_to_delete() -> list[Path]:
    """Hal's own folders of data; never the program folder, never a folder that holds it."""
    code = paths.code_dir()
    if paths.is_legacy_layout():
        raise Conflict("Your data is still inside Hal's own folder. Move it first (see Is Hal working?).")
    out = []
    for folder in (paths.data_dir(), paths.config_dir(), paths.cache_dir(), paths.documents_dir()):
        if _inside(folder, code) or _inside(code, folder):
            raise Conflict("Hal will not delete a folder that holds its own program files.")
        out.append(folder)
    return out


def delete_everything() -> list[str]:
    """Deletes Hal's data, settings, cache, documents and saved secrets; returns what could not be deleted."""
    from .settings import secrets

    folders = folders_to_delete()
    failed = []
    for name in secrets.NAMES:
        try:
            secrets.delete(name)
        except Exception:  # keep going: the rest of the data must still go; the name is reported
            log.exception("could not forget the saved %s", name)
            failed.append(name)
    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                failed.append(str(folder))
    log.warning("deleted everything Hal kept, at the person's request")
    return failed


def stop_hal() -> None:
    """Stops this server shortly after the reply is sent."""
    def stop() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Timer(STOP_DELAY_S, stop).start()


@route("POST", "/api/v2/privacy/delete")
def delete(req: Request) -> dict:
    """Deletes everything Hal keeps, after the page's two-step confirm, then stops Hal."""
    body = req.body if isinstance(req.body, dict) else {}
    if body.get("confirm") != CONFIRM_WORD:
        raise BadRequest(f'Send confirm "{CONFIRM_WORD}" to delete everything.', field="confirm")
    req.close()
    failed = delete_everything()
    stop_hal()
    return {"ok": not failed, "not_deleted": failed}


def open_folder(folder: Path) -> None:
    """Opens `folder` in the file manager of this computer."""
    try:
        from . import platform as plat
        plat.current().open_path(folder)
        return
    except (ImportError, AttributeError, NotImplementedError):
        pass
    if os.name == "nt":
        os.startfile(folder)
        return
    proc = subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reap() -> None:
        try:
            proc.wait(timeout=OPEN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            log.warning("the file manager did not return within %s s", OPEN_TIMEOUT_S)
    threading.Thread(target=reap, daemon=True).start()


@route("POST", "/api/v2/privacy/open-data-folder", local_only=True)
def open_data_folder(req: Request) -> dict:
    """Opens the data folder on this computer."""
    if req.remote:
        raise NotHere("Open the folder on the computer Hal runs on.")
    folder = paths.data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    open_folder(folder)
    return {"ok": True}

"""The record folder's API for the Your record page.

- Files are named by a `file_id` that encodes their path inside the record, and every download is checked
  to sit inside the folder, so no route can hand out any other file. Downloads work in a session from
  another computer: that is how the laptop reaches the files on the desktop.
- Opening or choosing the folder acts on the computer Hal runs on, so those routes are `local_only`.
- Reading the folder and asking it a question are actions (`evidence/actions.py`); the routes return their
  ids at once.
- Uploaded files go into the record's Inbox folder, never over a file already there.
"""
from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

from jobhunt import record
from jobhunt.core import runner
from jobhunt.evidence import actions  # noqa: F401 - registers record.sync and record.ask before a route starts one
from jobhunt.web.router import BadRequest, FileResponse, NotFound, route

log = logging.getLogger(__name__)
INBOX = "Inbox"
OTHER_GROUP = "Other files"                   # files at the top of the folder, in no subfolder
SAMPLE_NAMES = 3                              # file names shown under each group
OPEN_TIMEOUT_S = 10
# The folders setup makes (from the Windows build's setup): Private holds what the AI never sees.
STARTER_FOLDERS = ("Certificates", "Education", "Jobs", "Volunteering", "References", INBOX, "Private")
START_HERE = """---
type: index
---
# Start here

This folder is your record: everything about your studies, jobs and volunteering that a CV leaves out.
Hal reads it when it writes your CV, letters and answers, and says which file each fact came from.

- Put certificates in Certificates, transcripts and coursework in Education, and the story of each job or
  placement in Jobs or Volunteering.
- References go in References. Anything you are not sure about can go in Inbox.
- Files in Private are never sent to the AI. Hal can still show them to you.

Photos and scans of certificates are fine: Hal reads the words in them.
"""


def file_id(rel: str) -> str:
    """A path inside the record as a URL-safe id."""
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def _rel(fid: str) -> str:
    try:
        return base64.urlsafe_b64decode(fid + "=" * (-len(fid) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as e:
        raise NotFound("That file is not in your record.") from e


def _folder_words(folder: Path) -> str:
    home = Path.home()
    try:
        return str(Path("~") / folder.relative_to(home)) if sys.platform != "win32" else str(folder)
    except ValueError:
        return str(folder)


def _group(f: dict) -> str:
    return f["group"] or OTHER_GROUP


@route("GET", "/api/v2/record")
def overview(req) -> dict:
    """The page: where the folder is, what is in it by group, and when Hal last read it."""
    st = record.status()
    found = record.files()
    groups: dict[str, list[dict]] = {}
    for f in found:
        groups.setdefault(_group(f), []).append(f)
    return {"folder_words": _folder_words(Path(st["vault"])), "exists": st["exists"], "can_open": not req.remote,
            "groups": [{"name": name, "n": len(items), "private": all(f["private"] for f in items),
                        "sample": ", ".join(Path(f["name"]).stem for f in items[:SAMPLE_NAMES])}
                       for name, items in sorted(groups.items(), key=lambda kv: (kv[0] == OTHER_GROUP, kv[0]))],
            "last_read": st["read"], "files": len(found), "notes": st["notes"],
            "private_folders": sorted(record._setting("record.private_folders") or [])}


@route("GET", "/api/v2/record/files")
def files(req) -> list[dict]:
    """The files in one group (all files without `group`), each with its id and whether it is private."""
    group = req.query.get("group")
    return [{"file_id": file_id(f["path"]), "name": f["name"], "path": f["path"], "private": f["private"],
             "sub": "/".join(f["path"].split("/")[1:-1])}
            for f in record.files() if group is None or _group(f) == group]


@route("GET", "/api/v2/record/file/{file_id}")
def download(req) -> FileResponse:
    """One file of the record, as a download; works from another computer too."""
    path = record.file_path(_rel(req.params["file_id"]))
    return FileResponse(path, path.name, mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        download=True)


def _free_name(folder: Path, name: str) -> Path:
    clean = Path(name).name.strip() or "file"
    if clean.startswith("."):
        raise BadRequest("Files whose names start with a dot cannot be added.")
    target, n = folder / clean, 2
    while target.exists():
        target, n = folder / f"{Path(clean).stem} ({n}){Path(clean).suffix}", n + 1
    return target


@route("POST", "/api/v2/record/files")
def add_files(req) -> dict:
    """Copies uploaded files into the record's Inbox and starts reading the folder."""
    if not req.files:
        raise BadRequest("Choose one or more files to add.")
    folder = Path(record._live("VAULT"))
    if not folder.exists():
        raise BadRequest("Your record folder is not there yet. Make it first.")
    inbox = folder / INBOX
    inbox.mkdir(exist_ok=True)
    added = []
    for upload in req.files.values():
        target = _free_name(inbox, upload.name)
        target.write_bytes(upload.data)
        added.append(target.name)
    return {"added": added, "action_id": _start_sync(req)}


def _start_sync(req) -> str | None:
    try:
        return runner.start("record.sync", subject="record", session_id=_session(req), remote=req.remote)
    except RuntimeError:
        return None      # a read is already running and will see the new files


def _session(req) -> str | None:
    return getattr(req.session, "id", None)


@route("POST", "/api/v2/record/sync")
def sync(req) -> dict:
    """Reads the folder again now."""
    return {"action_id": _start_sync(req)}


@route("POST", "/api/v2/record/ask")
def ask(req) -> dict:
    """Asks the record a question; the answer is the action's result."""
    question = str((req.body or {}).get("question") or "").strip()
    if not question:
        raise BadRequest("Type a question first.", field="question")
    return {"action_id": runner.start("record.ask", params={"q": question}, session_id=_session(req),
                                      remote=req.remote)}


def open_folder(folder: Path) -> None:
    """Shows `folder` in the computer's file manager."""
    if sys.platform == "win32":
        os.startfile(folder)
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if not shutil.which(opener):
        raise RuntimeError("This computer has no file manager Hal can open.")
    subprocess.Popen([opener, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@route("POST", "/api/v2/record/open-folder", local_only=True)
def open_record(req) -> None:
    """Opens the record folder on this computer."""
    folder = Path(record._live("VAULT"))
    if not folder.exists():
        raise NotFound("Your record folder is not there yet. Make it first.")
    open_folder(folder)


@route("POST", "/api/v2/record/folder", local_only=True)
def choose(req) -> dict:
    """Uses another folder as the record."""
    from jobhunt.core import settings

    folder = Path(str((req.body or {}).get("path") or "")).expanduser()
    if not str(folder).strip() or not folder.is_dir():
        raise BadRequest("That folder is not there. Choose a folder that exists.", field="path")
    settings.set("record.folder", str(folder.resolve()), by="you", session_id=_session(req))
    return {"folder_words": _folder_words(folder.resolve())}


def make_default() -> Path:
    """Makes the record folder with its starter folders and "Start Here.md"; leaves anything there alone."""
    folder = Path(record._live("VAULT"))
    for name in STARTER_FOLDERS:
        (folder / name).mkdir(parents=True, exist_ok=True)
    start = folder / "Start Here.md"
    if not start.exists():
        start.write_text(START_HERE, encoding="utf-8")
    return folder


@route("POST", "/api/v2/record/create-folder")
def create(req) -> dict:
    """Makes the record folder where the settings say (by default Documents, Employment Record)."""
    return {"folder_words": _folder_words(make_default())}


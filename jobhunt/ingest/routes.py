"""The bulk ingest's API: how the latest ingest stands, and starting one from folders on this computer.

Reading a folder by its path acts on the computer Hal runs on, so `POST /api/v2/record/ingest` is
`local_only`; from another computer, files arrive as uploads (`POST /api/v2/record/files`, zips included).
A folder inside the record, or Hal's own data folder, is refused: reading the record into itself would write
a note of every note.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import paths
from jobhunt.ingest import actions, items
from jobhunt.web.router import BadRequest, route

FOLDERS_MAX = 20           # paths in one request; a longer list is a mistake, not a person's choice


@route("GET", "/api/v2/record/ingest")
def latest(req) -> dict:
    """The latest ingest: state, done, total, eta_s, counts by stage and category, the item in hand and errors."""
    return items.summary(req.con)


def _inside(path: Path, folder: Path) -> bool:
    folder = folder.resolve()
    return path == folder or folder in path.parents


@route("POST", "/api/v2/record/ingest", local_only=True)
def start(req) -> dict:
    """Starts reading the folders (or files and zips) at the given paths into the record."""
    given = (req.body or {}).get("folders")
    if not isinstance(given, list) or not given or not all(isinstance(p, str) and p.strip() for p in given):
        raise BadRequest("Choose one or more folders to read.", field="folders")
    if len(given) > FOLDERS_MAX:
        raise BadRequest(f"Choose at most {FOLDERS_MAX} folders at a time.", field="folders")
    chosen = []
    for raw in given:
        path = Path(raw.strip()).expanduser().resolve()
        if not path.exists():
            raise BadRequest(f"{raw} is not there. Choose a folder that exists.", field="folders")
        if _inside(path, Path(record._live("VAULT"))) or _inside(path, paths.data_dir()):
            raise BadRequest(f"{raw} is inside your record or Hal's own folder, so Hal already has it.",
                             field="folders")
        chosen.append(str(path))
    try:
        action_id = actions.queue(folders=chosen, session_id=getattr(req.session, "id", None), remote=req.remote,
                                  why="You chose folders to read into your record")
    except RuntimeError as e:
        raise RuntimeError("Hal is still reading other files into your record. Try again when it is done.") from e
    return {"action_id": action_id}

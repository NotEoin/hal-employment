"""The bulk ingest's API: how the latest ingest stands, and starting one from folders or web links.

Reading a folder by its path acts on the computer Hal runs on, so `POST /api/v2/record/ingest` is
`local_only`; from another computer, files arrive as uploads (`POST /api/v2/record/files`, zips included).
A folder inside the record, or Hal's own data folder, is refused: reading the record into itself would write
a note of every note. The local API key opens these routes too, so `python -m jobhunt ingest FOLDER`
can start a long ingest in the running Hal, where its progress shows on Your record.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import paths
from jobhunt.ingest import actions, items
from jobhunt.web.router import BadRequest, route

FOLDERS_MAX = 20           # paths in one request; a longer list is a mistake, not a person's choice
LINKS_MAX = 20             # links in one request, each read up to `record.web_pages` pages


@route("GET", "/api/v2/record/ingest", auth="session_or_local")
def latest(req) -> dict:
    """The latest ingest: state, done, total, eta_s, counts by stage and category, the item in hand and errors."""
    return items.summary(req.con)


def _inside(path: Path, folder: Path) -> bool:
    folder = folder.resolve()
    return path == folder or folder in path.parents


@route("POST", "/api/v2/record/ingest", auth="session_or_local", local_only=True)
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


@route("POST", "/api/v2/record/ingest/again", auth="session_or_local")
def again(req) -> dict:
    """Starts reading with the AI the documents Hal filed by their words alone (`record.read_all` for new ones)."""
    if not items.by_rules(req.con):
        raise BadRequest("Every document in your record has been read by the AI already.")
    try:
        action_id = actions.queue(again=True, session_id=getattr(req.session, "id", None), remote=req.remote,
                                  why="You asked Hal to read the rest of your documents with the AI")
    except RuntimeError as e:
        raise RuntimeError("Hal is still reading other files into your record. Try again when it is done.") from e
    return {"action_id": action_id}


@route("POST", "/api/v2/record/links", auth="session_or_local")
def links(req) -> dict:
    """Starts reading web links (a site, a GitHub profile or repository, a public Google Doc) into the record."""
    given = (req.body or {}).get("urls")
    if isinstance(given, str):
        given = given.split()
    urls = [u.strip() for u in given or [] if isinstance(u, str) and u.strip()] if isinstance(given, list) else []
    if not urls:
        raise BadRequest("Paste one or more links to read.", field="urls")
    if len(urls) > LINKS_MAX:
        raise BadRequest(f"Paste at most {LINKS_MAX} links at a time.", field="urls")
    try:
        action_id = actions.queue(urls=urls, session_id=getattr(req.session, "id", None), remote=req.remote,
                                  why="You gave links to read into your record")
    except RuntimeError as e:
        raise RuntimeError("Hal is still reading other files into your record. Try again when it is done.") from e
    return {"action_id": action_id}

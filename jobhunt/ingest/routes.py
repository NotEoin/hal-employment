"""The bulk ingest's API: how the latest ingest stands, and starting one from folders or web links.

Reading a folder by its path acts on the computer Hal runs on, so `POST /api/v2/record/ingest` is
`local_only`; from another computer, files arrive as uploads (`POST /api/v2/record/files`, zips included).
A folder inside the record, or Hal's own data folder, is refused: reading the record into itself would write
a note of every note. The local API key opens these routes too, so `python -m jobhunt ingest FOLDER`
can start a long ingest in the running Hal, where its progress shows on Your record. A file kept out as
private comes back with `POST /api/v2/record/ingest/private/allow`; when Hal no longer has it (it came in a
zip, removed once read), the person is asked to add it again, and it is let in then.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import paths
from jobhunt.ingest import actions, items, privacy, roles
from jobhunt.web.router import BadRequest, NotFound, route

FOLDERS_MAX = 20           # paths in one request; a longer list is a mistake, not a person's choice
LINKS_MAX = 20             # links in one request, each read up to `record.web_pages` pages


@route("GET", "/api/v2/record/ingest", auth="session_or_local")
def latest(req) -> dict:
    """The latest ingest: state, done, total, eta_s, counts by stage and category, the item in hand and errors."""
    return items.summary(req.con)


def _inside(path: Path, folder: Path) -> bool:
    folder = folder.resolve()
    return path == folder or folder in path.parents


def _folder(entry) -> tuple[str, str]:
    """One entry of `folders`: a path (a mix), or `{"path", "origin"}`; returns (path, origin)."""
    if isinstance(entry, dict):
        raw, origin = entry.get("path"), entry.get("origin")
    else:
        raw, origin = entry, roles.MIXED
    if not isinstance(raw, str) or not raw.strip():
        raise BadRequest("Choose one or more folders to read.", field="folders")
    try:
        return raw, roles.origin_of(origin)
    except ValueError as e:
        raise BadRequest("Say whether the folder is your own work, course material or a mix of both.",
                         field="folders") from e


@route("POST", "/api/v2/record/ingest", auth="session_or_local", local_only=True)
def start(req) -> dict:
    """Starts reading the folders (or files and zips) at the given paths into the record.

    Each of `folders` is a path, or `{"path", "origin"}` with where the person said it came from
    (`roles.ORIGINS`); a bare path is a mix of both.
    """
    given = (req.body or {}).get("folders")
    if not isinstance(given, list) or not given:
        raise BadRequest("Choose one or more folders to read.", field="folders")
    if len(given) > FOLDERS_MAX:
        raise BadRequest(f"Choose at most {FOLDERS_MAX} folders at a time.", field="folders")
    chosen, origins = [], {}
    for raw, origin in map(_folder, given):
        path = Path(raw.strip()).expanduser().resolve()
        if not path.exists():
            raise BadRequest(f"{raw} is not there. Choose a folder that exists.", field="folders")
        if _inside(path, Path(record._live("VAULT"))) or _inside(path, paths.data_dir()):
            raise BadRequest(f"{raw} is inside your record or Hal's own folder, so Hal already has it.",
                             field="folders")
        chosen.append(str(path))
        origins[str(path)] = origin
    try:
        action_id = actions.queue(folders=chosen, origins=origins, session_id=getattr(req.session, "id", None),
                                  remote=req.remote, why="You chose folders to read into your record")
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


@route("POST", "/api/v2/record/ingest/tidy", auth="session_or_local")
def tidy(req) -> dict:
    """Starts tidying the record: a backup, then everything already filed held to the rules reads follow now."""
    try:
        action_id = actions.queue(tidy=True, session_id=getattr(req.session, "id", None), remote=req.remote,
                                  why="You asked Hal to tidy your record")
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


@route("POST", "/api/v2/record/ingest/private/allow", auth="session_or_local")
def allow_private(req) -> dict:
    """Lets one file kept out as private into the record: reads it now, or once it is added again.

    Returns the ingest's `action_id`, or None with `add_again` when Hal no longer has the file.
    """
    item_id = (req.body or {}).get("id")
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise BadRequest("Choose a file to add.", field="id")
    row = privacy.private_item(req.con, item_id)
    if row is None:
        raise NotFound("That file is not kept out any more.")
    if not Path(row["path"]).is_file():
        privacy.allow(req.con, item_id)
        req.con.commit()
        return {"action_id": None, "add_again": True}
    try:
        action_id = actions.queue(allow=[item_id], session_id=getattr(req.session, "id", None), remote=req.remote,
                                  why="You asked Hal to add a file it had kept out as private")
    except RuntimeError as e:
        raise RuntimeError("Hal is still reading other files into your record. Try again when it is done.") from e
    return {"action_id": action_id, "add_again": False}

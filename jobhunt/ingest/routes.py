"""The older bulk read's API: how the latest one stands, reading again what it filed, and the ways in it had.

New files never go through the bulk read any more, which guessed each file's kind and owner and crossed facts
between people. `POST /api/v2/record/ingest` (folders on this computer, local only, and the local API key
opens it, as `jobhunt ingest` uses) now starts "Sort a big folder for me" (`sections.sort_routes`), whose
proposals the person accepts; `POST /api/v2/record/links` adds each link to Projects as a source of its own
(`pipeline.add_link`), a GitHub account, a GitHub project or a web page by its address. What stays on the bulk
read is for records an older Hal built: how its last read went, "Read them with the AI" (`again`), "Tidy my
record" (`tidy`), and letting in a file it kept out as private (`private/allow`); when Hal no longer has
that file (it came in a zip, removed once read), the person is asked to add it again, and it is let in then.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt.core import events
from jobhunt.ingest import actions, items, privacy
from jobhunt.web.router import BadRequest, NotFound, route

LINKS_MAX = 20             # links in one request
GITHUB_HOSTS = ("github.com", "www.github.com")
ACCOUNT_PARTS = 2          # "github.com/username": the host and one name


@route("GET", "/api/v2/record/ingest", auth="session_or_local")
def latest(req) -> dict:
    """The latest ingest: state, done, total, eta_s, counts by stage and category, the item in hand and errors."""
    return items.summary(req.con)


def _path(entry) -> str:
    """One entry of `folders`: a path, or `{"path", "origin"}` as an older page sent it (the origin is not needed:
    the section the person accepts says whose each file is)."""
    raw = entry.get("path") if isinstance(entry, dict) else entry
    if not isinstance(raw, str) or not raw.strip():
        raise BadRequest("Choose one or more folders to sort.", field="folders")
    return raw


@route("POST", "/api/v2/record/ingest", auth="session_or_local", local_only=True)
def start(req) -> dict:
    """Starts sorting the folders at the given paths; {"action_id", "sort_id"}. Nothing is read in until the
    person accepts the proposals."""
    from jobhunt.sections import sort_routes

    given = (req.body or {}).get("folders")
    if not isinstance(given, list) or not given:
        raise BadRequest("Choose one or more folders to sort.", field="folders")
    out = sort_routes.start_folders(req, [_path(f) for f in given], "You chose folders to sort into your record")
    return {"action_id": out["action_id"], "sort_id": out["sort"]["id"]}


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


def link_kind(url: str) -> str:
    """The Projects kind of document a pasted link is: a GitHub account, one GitHub project, or a web page."""
    parts = [x for x in url.split("://", 1)[-1].split("?")[0].split("/") if x]
    if parts and parts[0].lower() in GITHUB_HOSTS:
        return "github_account" if len(parts) == ACCOUNT_PARTS else "repo_url"
    return "web_page"


@route("POST", "/api/v2/record/links", auth="session_or_local")
def links(req) -> dict:
    """Adds each pasted link to Projects as its own source (a GitHub account, a GitHub project or a web page) and
    starts reading them; {"action_id", "added": [ids], "duplicates"}."""
    from jobhunt.sections import actions as section_actions
    from jobhunt.sections import pipeline

    given = (req.body or {}).get("urls")
    if isinstance(given, str):
        given = given.split()
    urls = [u.strip() for u in given or [] if isinstance(u, str) and u.strip()] if isinstance(given, list) else []
    if not urls:
        raise BadRequest("Paste one or more links to read.", field="urls")
    if len(urls) > LINKS_MAX:
        raise BadRequest(f"Paste at most {LINKS_MAX} links at a time.", field="urls")
    added, duplicates = [], []
    for url in urls:
        try:
            out = pipeline.add_link(req.con, "project", url, doc_kind=link_kind(url))
        except ValueError as e:
            raise BadRequest(str(e), field="urls") from e
        added += out["added"]
        duplicates += out["duplicates"]
    events.publish("profile", sources=added)
    return {"action_id": section_actions.queue(added, session_id=getattr(req.session, "id", None),
                                               remote=req.remote, why="You gave links to add to your projects"),
            "added": added, "duplicates": duplicates}


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

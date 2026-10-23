"""The Where Hal looks routes: list the sources, switch or configure one, remove one the person
added, look at a pasted address or a company, save what was found, and search now.

Looking and searching start actions and answer their id at once; listing reads only the `sources` table
and the secrets' saved-or-not state, so the page loads in well under a second.
"""

from __future__ import annotations

from jobhunt.core import runner, scheduler
from jobhunt.find import source_actions
from jobhunt.find import sources_registry as registry
from jobhunt.web.router import BadRequest, Request, route

SEARCH_JOB = "find.discover"


def _session(req: Request) -> str | None:
    return req.session.id if req.session else None


@route("GET", "/api/v2/sources")
def sources(req: Request) -> dict:
    """Every source with its health, what is missed without keys, and when Hal searches."""
    from jobhunt.core import settings

    return {"sources": registry.listing(req.con), "missed": registry.missed(req.con),
            "times": list(settings.get("schedule.search_times") or [])}


@route("PUT", "/api/v2/sources/{source_id}")
def change(req: Request) -> dict:
    """Switches a source on or off ({on}), or changes its own search words ({config})."""
    body = req.body or {}
    if "on" in body:
        if not isinstance(body["on"], bool):
            raise BadRequest("on must be true or false.", field="on")
        registry.switch(req.con, req.params["source_id"], body["on"])
    elif "config" in body:
        if not isinstance(body["config"], dict):
            raise BadRequest("config must be an object.", field="config")
        registry.configure(req.con, req.params["source_id"], body["config"])
    else:
        raise BadRequest("Send on or config.", field="on")
    return next(s for s in registry.listing(req.con) if s["id"] == req.params["source_id"])


@route("DELETE", "/api/v2/sources/{source_id}")
def remove(req: Request) -> None:
    """Removes a site the person added; Hal's own sites can only be switched off."""
    registry.remove(req.con, req.params["source_id"])


@route("POST", "/api/v2/sources/detect")
def look(req: Request) -> dict:
    """Starts looking at a pasted address ({url}) or for a company's job board ({company})."""
    body = req.body or {}
    url, company = (body.get("url") or "").strip(), (body.get("company") or "").strip()
    if not url and not company:
        raise BadRequest("Paste an address or type a company name.", field="url")
    if url and not url.startswith(("http://", "https://")):
        if company:
            raise BadRequest("Paste the site's address, starting with https://", field="url")
        url, company = "", url
    runner.load_kinds()
    params = {"url": url} if url else {"company": company}
    action_id = runner.start("find.detect_site", params=params, subject=f"detect:{url or company}", replace=True,
                             session_id=_session(req), remote=req.remote,
                             why=f"You asked Hal to look at {url or company}")
    return {"action_id": action_id}


@route("POST", "/api/v2/sources")
def save(req: Request) -> dict:
    """Saves a site Hal looked at and keeps the ticked jobs ({detected_id, keep_urls})."""
    body = req.body or {}
    keep = body.get("keep_urls") or []
    if not isinstance(keep, list):
        raise BadRequest("keep_urls must be a list of addresses.", field="keep_urls")
    return source_actions.save(req.con, body.get("detected_id") or "", [str(u) for u in keep],
                               session_id=_session(req))


@route("POST", "/api/v2/sources/search-now")
def search_now(req: Request) -> dict:
    """Starts a search of every switched-on site now."""
    return {"action_id": scheduler.run_now(SEARCH_JOB)}

"""The core API v2 routes: who and where this window is, signing out, the event stream and Ctrl K search.

`/search` composes every area's `search.py` provider after the built-in list of pages, so the
palette finds pages before any area has landed. A provider that fails is logged and left out of that reply.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sqlite3

from .. import __version__
from ..web import auth, sse
from ..web.router import Request, Stream, route
from . import paths, plugins

log = logging.getLogger(__name__)
SETUP_STATE = "jobhunt.setup.state"      # the setup guide decides whether setup is finished
SEARCH_LIMIT = 30
# The app's pages (static/app/core/router.js ROUTES), so Ctrl K finds them by name.
PAGES = (("Home", "home"), ("Jobs", "jobs"), ("Applications", "apps"), ("Messages", "inbox"), ("My CV", "cv"),
         ("Documents", "docs"), ("Your record", "record"), ("Activity", "activity"),
         ("What Hal remembers", "memory"), ("Settings", "settings"), ("Setup guide", "setup"))


def _setting(key: str, fallback):
    from . import settings

    try:
        return settings.get(key)
    except KeyError:
        return fallback


def setup_done(con: sqlite3.Connection) -> bool:
    """True when the setup guide is finished (always, until the setup package provides its state)."""
    if importlib.util.find_spec(SETUP_STATE) is None:
        return True
    state = importlib.import_module(SETUP_STATE)
    return bool(state.done(con)) if hasattr(state, "done") else True


def first_name() -> str:
    """What Hal calls the person: the preferred name, else the first word of their name."""
    preferred = (_setting("you.preferred_name", "") or "").strip()
    if preferred:
        return preferred
    name = (_setting("you.name", "") or "").strip()
    return name.split()[0] if name else ""


@route("GET", "/api/v2/session")
def session(req: Request) -> dict:
    """Who and where: the session, read by the app shell before anything else."""
    from ..web import app

    remote = req.remote
    here = not remote
    developer = bool(_setting("advanced.developer_mode", paths.is_developer_checkout()))
    return {"remote": remote, "remote_host": req.session.remote_host if req.session else None,
            "developer": developer, "version": __version__, "platform": paths.platform_name(),
            "setup_done": setup_done(req.con), "first_name": first_name(),
            "simple_menu": bool(_setting("advanced.simple_menu", not developer)),
            "theme": _setting("advanced.theme", "system"), "classic": app.classic_on(),
            "install_id": auth.install_id(),
            "caps": {"open_folder": here, "window_mode": here and paths.platform_name() == "linux",
                     "sign_in_here": here, "start_at_login": here}}


@route("POST", "/api/v2/session/end")
def end(req: Request) -> None:
    """Signs this window out."""
    auth.end_session(req.con, req.session.id)


@route("GET", "/api/v2/events")
def events(req: Request) -> Stream:
    """The event stream."""
    session_id = req.session.id
    return Stream(lambda handler: sse.stream(handler, session_id))


@route("GET", "/api/v2/search")
def search(req: Request) -> list[dict]:
    """Ctrl K: pages whose name matches, then each area's results."""
    q = (req.query.get("q") or "").strip()
    if not q:
        return []
    low = q.lower()
    out = [{"label": label, "kind": "Page", "route": r} for label, r in PAGES if low in label.lower()]
    for module in plugins.modules("search"):
        if not hasattr(module, "search"):
            continue
        try:
            out.extend(module.search(req.con, q))
        except Exception:
            log.exception("search in %s failed", module.__name__)
    return out[:SEARCH_LIMIT]

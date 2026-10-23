"""The browser helper's routes of API v2: pairing, the long poll and results.

The window's routes (listing, the browsers found, pairing codes, opening pages in another browser) need
a session; the helper's own routes need its key (`helper` auth, checked by `apply.helpers`). Files the
helper uploads come only from the application's own documents.

Importing this module also loads the helper-folder refresh, so it runs at every start.
"""
from __future__ import annotations

import functools
import logging
import tomllib
from pathlib import Path

from .. import extension
from ..core import platform, runner
from ..web.router import BadRequest, Conflict, FileResponse, NotFound, Request, route
from . import helper_mirror, helpers  # noqa: F401 - helper_mirror registers its start task on import

log = logging.getLogger(__name__)

PREFIX = "/api/v2/helpers"
BROWSERS_FILE = Path(__file__).with_name("browsers.toml")
PICTURES = "/app/setup/img/helper/"
ADOPT_WAIT_S = 2         # a reply within this keeps the toolbar button quick; after that the helper asks again
PDF_MIME = "application/pdf"
FILE_KINDS = ("cv", "letter")


@functools.lru_cache(maxsize=1)
def browsers_table() -> dict:
    """browsers.toml: per browser its extensions page, its steps and their pictures."""
    with BROWSERS_FILE.open("rb") as f:
        return tomllib.load(f)["browsers"]


def _body(req: Request) -> dict:
    if req.body is None:
        return {}
    if not isinstance(req.body, dict):
        raise BadRequest("The request should be a JSON object.")
    return req.body


def _found() -> dict[str, platform.Browser]:
    try:
        return {b.id: b for b in platform.current().browsers()}
    except OSError:
        log.exception("could not look for browsers")
        return {}


def _browser(body: dict) -> platform.Browser:
    wanted = str(body.get("browser") or "")
    entry = browsers_table().get(wanted)
    if entry is None:
        raise BadRequest("Choose the browser you use.", field="browser")
    if not entry["supported"]:
        raise Conflict(entry.get("why") or "Hal's browser helper does not work in that browser yet.")
    found = _found().get(wanted)
    if found is None:
        raise Conflict(f"Hal could not find {entry['name']} on this computer. Open it yourself and type "
                       f"{entry['extensions_url']} in its address bar.")
    return found


def pair_url(port: int, code: str) -> str:
    """The pairing page in the helper's browser; the code is in the fragment, so it never reaches a log."""
    return f"http://127.0.0.1:{port}/pair#code={code}"


@route("GET", PREFIX)
def listing(req: Request) -> list:
    """The paired helpers."""
    return helpers.listing(req.con, req.session)


@route("GET", PREFIX + "/browsers")
def browsers(req: Request) -> dict:
    """The browsers setup explains, which of them are on this computer, and the helper folder."""
    from ..core import paths

    found = _found()
    out = []
    for key, entry in browsers_table().items():
        steps = [{"text": s["text"], "picture": PICTURES + s["picture"]} for s in entry["steps"]]
        out.append({"id": key, "name": entry["name"], "found": key in found, "supported": entry["supported"],
                    "extensions_url": entry["extensions_url"], "steps": steps, "why": entry.get("why", ""),
                    "sandboxed": bool(found[key].sandboxed) if key in found else False})
    folder = paths.helper_dir()
    return {"browsers": out, "folder": str(folder),
            "folder_visible": not any(part.startswith(".") for part in folder.parts),
            "version": helpers.wanted_version()}


@route("POST", PREFIX + "/open-extensions-page", local_only=True)
def open_extensions_page(req: Request) -> dict:
    """Opens a new window of the chosen browser, for the extensions page's address the step has copied.

    Neither a web page nor the browser's command line can open an address such as `edge://extensions`
    (the browser drops it and shows a new tab), so the person pastes it; `address` is what to paste.
    """
    b = _browser(_body(req))
    platform.current().open_browser_window(b)
    return {"ok": True, "address": b.extensions_url}


@route("POST", PREFIX + "/pairing-code")
def pairing_code(req: Request) -> dict:
    """A one-time code for the helper in the chosen browser, and the pairing page's address."""
    from ..web import auth

    browser = str(_body(req).get("browser") or "") or None
    code = helpers.new_code(req.con, req.session, browser)
    return {"code": code, "pair_url": pair_url(auth.server_port(), code), "pair_path": f"/pair#code={code}",
            "expires_in": helpers.CODE_S}


@route("POST", PREFIX + "/open-pair-page", local_only=True)
def open_pair_page(req: Request) -> dict:
    """Opens the pairing page in the chosen browser, where the helper reads the code."""
    from ..web import auth

    body = _body(req)
    code = str(body.get("code") or "")
    if not code:
        raise BadRequest("Press Connect again.", field="code")
    b = _browser(body)
    platform.current().open_in_browser(b, pair_url(auth.server_port(), code))
    return {"ok": True}


@route("POST", PREFIX + "/pair", auth="pairing")
def pair(req: Request) -> dict:
    """Swaps the pairing code for this helper's id and key."""
    body = _body(req)
    try:
        protocol = int(body.get("protocol") or 0)
    except (TypeError, ValueError) as e:
        raise BadRequest("The helper sent a protocol Hal cannot read.") from e
    if protocol != helpers.PROTOCOL:
        raise Conflict("This browser helper is from another version of Hal. Load the helper from Hal's "
                       "helper folder again.")
    out = helpers.pair(req.con, str(body.get("code") or ""), browser=body.get("browser"),
                       version=body.get("version"), protocol=protocol, extension_id=body.get("extension_id"))
    return {**out, "base": helpers.pairing_base()}


@route("POST", PREFIX + "/poll", auth="helper")
def poll(req: Request) -> dict:
    """The helper's long poll: up to 20 s for a command, or a request to reload itself."""
    body = _body(req)
    try:
        protocol = int(body.get("protocol") or 0)
    except (TypeError, ValueError):
        protocol = 0
    if protocol != helpers.PROTOCOL:
        return {"commands": [], "reload": False, "wanted_version": helpers.wanted_version(),
                "error": "This browser helper speaks another protocol. Load it from Hal's helper folder again."}
    return extension.poll_helper(req.helper.id, body.get("version"), protocol)


@route("POST", PREFIX + "/result", auth="helper")
def result(req: Request) -> dict:
    """A command's result; one for a command given to another helper is ignored."""
    body = _body(req)
    if "id" not in body or "ok" not in body:
        raise BadRequest("A result needs its command id and whether it worked.")
    extension.deliver(body, req.helper.id)
    return {"ok": True}


@route("POST", PREFIX + "/adopt", auth="helper")
def adopt(req: Request) -> dict:
    """Hands the helper's tab to Hal: finds or adds its job, then fills it through the helper that sent it.

    Finding the job may ask the AI, so it is an action; when it takes over `ADOPT_WAIT_S` the reply carries
    its `action_id` and the helper asks again with it.
    """
    from . import actions

    body = _body(req)
    action_id = body.get("action_id")
    if not action_id:
        tab = body.get("tab")
        if not isinstance(tab, dict) or not tab.get("url"):
            raise BadRequest("The helper did not send the tab's address.")
        action_id = runner.start("apply.match_tab", params={"page": tab}, helper_id=req.helper.id,
                                 remote=req.remote)
    done = runner.wait(str(action_id), ADOPT_WAIT_S)
    if done["state"] in ("error", "stopped", "interrupted"):
        raise Conflict(done["error"] or done["message"] or "Hal could not take this tab.")
    if done["state"] != "done":
        return {"started": True, "action_id": action_id}
    found = done["result"]
    extension.assign(found["job"], req.helper.id)
    filling = actions.start("apply.adopt", found["job"], params={"url": (body.get("tab") or {}).get("url", "")},
                            helper_id=req.helper.id, remote=req.remote)
    return {"job_id": found["job"], "application_id": found["job"], "title": found["title"],
            "company": found["company"], "action_id": filling}


@route("POST", PREFIX + "/fill-empty", auth="helper")
def fill_empty(req: Request) -> dict:
    """The panel's "Fill empty boxes": fills what the job's tab shows that is still empty."""
    from .driver import act_page

    job_id = str(_body(req).get("job_id") or "")
    if not job_id:
        raise BadRequest("The helper did not say which job this tab is for.")
    entry = runner.run_action(job_id, "filling this page", act_page, job_id)
    return {"ok": True, "state": entry.get("state")}


@route("GET", PREFIX + "/file", auth="helper")
def file(req: Request) -> FileResponse:
    """A document to upload, from that application's own documents only."""
    from .. import documents
    from .queries import _job

    job_id = req.query.get("job_id") or ""
    con = req.con
    try:
        job = _job(con, job_id)
    except LookupError as e:
        raise NotFound("There is no such job.") from e
    files = documents.files(job)
    kind, path = req.query.get("kind"), req.query.get("path")
    chosen = files.get(kind) if kind in FILE_KINDS else next((p for p in files.values() if p == path), None)
    if not chosen:
        raise NotFound("That file is not one of this application's documents.")
    target = Path(chosen)
    mime = PDF_MIME if target.suffix.lower() == ".pdf" else "application/octet-stream"
    return FileResponse(target, target.name, mime, download=True)


@route("DELETE", PREFIX + "/{helper_id}")
def forget(req: Request) -> dict:
    """Forgets a paired helper; its key stops working at once."""
    helpers.forget(req.con, req.params["helper_id"])
    return {"ok": True}

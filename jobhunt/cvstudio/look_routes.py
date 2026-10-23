"""My CV routes for looks, previews, PDFs and exports.

The preview page is sent as the exact bytes the printer prints (with highlights only when asked for), and
without the server's default page policy, because that policy blocks fonts: the page carries its own
Content-Security-Policy instead (`render.POLICY`). Its fonts are served under the preview's own address,
so the page's relative `fonts/<file>` links work in the iframe as they do in the printer's folder.

The PDF and export routes print in the request thread: a CV prints in about a second, and the result is
cached by the page's hash, so the same version and look print once.
"""
from __future__ import annotations

import logging
from typing import Any

from jobhunt.web.router import BadRequest, BytesResponse, FileResponse, NotFound, Request, route

from . import export, render

log = logging.getLogger(__name__)
HTML_MIME = "text/html; charset=utf-8"
CURRENT = "current"           # a version id that means Hal's copy as it is now


def _store() -> Any:
    from . import store

    return store


def _type(type_id: str | None) -> Any:
    """The CV type, or None when it cannot be loaded (the page then keeps the document's own order)."""
    from . import types

    try:
        return types.load(type_id) if type_id else None
    except (LookupError, ValueError) as e:
        log.warning("CV type %r could not be loaded: %s", type_id, e)
        return None


def _layout(store: Any) -> dict:
    try:
        return store.layout() or {}
    except (LookupError, ValueError):
        return {}


def _version(store: Any, version_id: str) -> Any:
    try:
        return store.current()[1] if version_id == CURRENT else store.get(version_id)
    except LookupError as e:
        raise NotFound("That version of your CV is not in Hal's list.") from e


def _look_id(req: Request, row: dict) -> str:
    look_id = req.query.get("look") or row.get("look_id") or render.DEFAULT_LOOK
    try:
        return render.look(look_id).id
    except render.UnknownLook as e:
        raise BadRequest(str(e), field="look") from e


def _parts(req: Request, version_id: str) -> tuple[Any, Any, str, dict]:
    store = _store()
    row = store.doc()
    doc = _version(store, version_id)
    type_ = _type(req.query.get("type") or row.get("type_id"))
    return doc, type_, _look_id(req, row), _layout(store)


@route("GET", "/api/v2/cv/looks")
def looks_list(req: Request) -> list[dict]:
    """The looks, in gallery order, with the one in use marked."""
    try:
        chosen = _store().doc().get("look_id") or render.DEFAULT_LOOK
    except Exception:
        log.exception("could not read the chosen look")
        chosen = render.DEFAULT_LOOK
    return [{**look.public(), "chosen": look.id == chosen} for look in render.looks()]


@route("PUT", "/api/v2/cv/look")
def choose_look(req: Request) -> dict:
    """Chooses the look Hal's copy is printed in."""
    from jobhunt.core import clock, events

    look_id = str((req.body or {}).get("id") or "")
    try:
        chosen = render.look(look_id)
    except render.UnknownLook as e:
        raise BadRequest(str(e), field="id") from e
    _store().doc()     # the document row is made on first use
    req.con.execute("UPDATE cv_documents SET look_id = ?, updated = ? WHERE id = 'main'", (chosen.id, clock.iso()))
    req.con.commit()
    events.publish("cv", look=chosen.id)
    return {"look": chosen.public()}


@route("GET", "/api/v2/cv/versions/{version_id}/preview.html")
def preview(req: Request) -> BytesResponse:
    """The page the preview iframe shows and the printer prints; `hl` lists the ids to highlight."""
    doc, type_, look_id, layout = _parts(req, req.params["version_id"])
    hl = [x for x in (req.query.get("hl") or "").split(",") if x]
    page = render.render_cv_html(doc, type_, look_id, render.profile_from_settings(), highlight=hl, layout=layout)
    return BytesResponse(page.encode("utf-8"), "preview.html", HTML_MIME)


@route("GET", "/api/v2/cv/versions/{version_id}/fonts/{name}")
def preview_font(req: Request) -> FileResponse:
    """One of the CV fonts, for the preview page."""
    for f in render.font_files():
        if f.name == req.params["name"]:
            return FileResponse(f, f.name, "font/woff2")
    raise NotFound("There is no such font.")


@route("GET", "/api/v2/cv/versions/{version_id}/pdf", slow=True)
def version_pdf(req: Request) -> BytesResponse:
    """The version printed as a PDF in a look."""
    doc, type_, look_id, layout = _parts(req, req.params["version_id"])
    data, name, mime = export.export("pdf", doc, type_, look_id, render.profile_from_settings(), layout=layout)
    return BytesResponse(data, name, mime)


@route("GET", "/api/v2/cv/export", slow=True)
def export_cv(req: Request) -> BytesResponse:
    """Hal's copy as a file to download: pdf, docx, txt, jsonresume or website."""
    fmt = req.query.get("format") or "pdf"
    if fmt not in export.FORMATS:
        raise BadRequest(f"Hal cannot export a CV as {fmt!r}.", field="format")
    doc, type_, look_id, layout = _parts(req, req.query.get("version") or CURRENT)
    data, name, mime = export.export(fmt, doc, type_, look_id, render.profile_from_settings(), layout=layout)
    return BytesResponse(data, name, mime, download=True)

"""My CV routes for looks, previews, PDFs and exports.

The preview page is sent as the exact bytes the printer prints (with highlights only when asked for), with
the page's own Content-Security-Policy (`render.POLICY`) as the header as well as in its meta tag, because
the server's default page policy blocks fonts. Its fonts are served under the preview's own address,
so the page's relative `fonts/<file>` links work in the iframe as they do in the printer's folder.

The PDF and export routes print in the request thread: a CV prints in about a second, and the result is
cached by the page's hash, so the same version and look print once.

A version is printed with the type, look and layout of the CV it belongs to; "current" and the look and
export routes take the CV as `?cv=` (the lead CV by default), as every My CV route does.
"""
from __future__ import annotations

import logging
from typing import Any

from jobhunt.cvstudio import export, render
from jobhunt.web.router import BadRequest, BytesResponse, FileResponse, HtmlResponse, NotFound, Request, route

log = logging.getLogger(__name__)
CURRENT = "current"           # a version id that means Hal's copy as it is now


def _store() -> Any:
    from jobhunt.cvstudio import store

    return store


def _type(type_id: str | None) -> Any:
    """The CV type, or None when it cannot be loaded (the page then keeps the document's own order)."""
    from jobhunt.cvstudio import types

    try:
        return types.load(type_id) if type_id else None
    except (LookupError, ValueError) as e:
        log.warning("CV type %r could not be loaded: %s", type_id, e)
        return None


def _layout(store: Any, doc_id: str) -> dict:
    try:
        return store.layout(doc_id=doc_id) or {}
    except (LookupError, ValueError):
        return {}


def _cv(req: Request) -> str | None:
    from jobhunt.cvstudio.routes import cv_of

    return cv_of(req)


def _version(store: Any, version_id: str, doc_id: str | None) -> tuple[Any, str | None]:
    """(the document, its CV's id): the CV's version in use for `CURRENT`, else that version."""
    try:
        if version_id == CURRENT:
            return store.current(doc_id=doc_id)[1], doc_id
        return store.get(version_id), store.version(version_id)["doc_id"]
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
    doc, doc_id = _version(store, version_id, _cv(req))
    row = store.doc(doc_id=doc_id)
    type_ = _type(req.query.get("type") or row.get("type_id"))
    return doc, type_, _look_id(req, row), _layout(store, row["id"])


@route("GET", "/api/v2/cv/looks")
def looks_list(req: Request) -> list[dict]:
    """The looks, in gallery order, with the one in use marked."""
    try:
        chosen = _store().doc(doc_id=_cv(req)).get("look_id") or render.DEFAULT_LOOK
    except Exception:
        log.exception("could not read the chosen look")
        chosen = render.DEFAULT_LOOK
    return [{**look.public(), "chosen": look.id == chosen} for look in render.looks()]


@route("PUT", "/api/v2/cv/look")
def choose_look(req: Request) -> dict:
    """Chooses the look Hal's copy is printed in, and answers My CV's header as GET /cv does."""
    from jobhunt.core import events
    from jobhunt.cvstudio.routes import summary

    look_id = str((req.body or {}).get("id") or "")
    try:
        chosen = render.look(look_id)
    except render.UnknownLook as e:
        raise BadRequest(str(e), field="id") from e
    doc_id = _cv(req)
    _store().set_look(chosen.id, con=req.con, doc_id=doc_id)
    req.con.commit()  # the store joins a transaction already open on `con` rather than committing it
    events.publish("cv", look=chosen.id, cv=doc_id)
    return summary(req.con, doc_id)


@route("GET", "/api/v2/cv/versions/{version_id}/preview.html")
def preview(req: Request) -> HtmlResponse:
    """The page the preview iframe shows and the printer prints; `hl` lists the ids to highlight."""
    doc, type_, look_id, layout = _parts(req, req.params["version_id"])
    hl = [x for x in (req.query.get("hl") or "").split(",") if x]
    page = render.render_cv_html(doc, type_, look_id, render.profile_from_settings(), highlight=hl, layout=layout)
    return HtmlResponse(page, policy=render.POLICY)


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

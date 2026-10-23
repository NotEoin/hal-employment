"""CV type routes of My CV, Type and look.

- The CV's type is `cv_documents.type_id`. Choosing one makes a `type_change` version, so the change shows
  in Versions and can be undone by using the version before it, and starts the suggestions (the
  `cv.suggest` action) when that action exists.
- Built-in types are never saved over: the Writing card and "Copy this type to change it" both work on a
  copy, and the answer says so in words the page shows as they are.
- Comparing two types renders nothing new: it answers the two outlines and the preview addresses the printer serves.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from jobhunt.core import runner, settings
from jobhunt.cvstudio import types as cvtypes
from jobhunt.web.router import BadRequest, NotFound, Request, route

log = logging.getLogger(__name__)
SUGGEST_KIND = "cv.suggest"
NO_CV = "There is no CV in Hal yet. Add yours first."


def _store():
    from jobhunt.cvstudio import store

    return store


def _current_type_id() -> str:
    return _store().doc()["type_id"]


def _goal():
    try:
        from jobhunt.find import goal

        return goal.current()
    except (ImportError, LookupError, KeyError) as e:
        log.info("no goal to suggest a CV type from: %s", e)
        return None


def _set_type(con, type_id: str) -> None:
    _store().set_type(type_id, con=con)
    con.commit()  # the store joins a transaction already open on `con` rather than committing it


def _type_or_404(type_id: str, con) -> cvtypes.CVType:
    try:
        return cvtypes.load(type_id, con)
    except LookupError as e:
        raise NotFound(str(e)) from e


@route("GET", "/api/v2/cv/types")
def list_types(req: Request) -> dict:
    """Every CV type for the picker, the one Hal suggests with its reason, and the Writing card's values."""
    try:
        _, doc = _store().current()
    except LookupError:
        doc = None
    sid, why = cvtypes.suggest(_goal(), doc, subject=str(settings.get("you.education.subject") or ""))
    current = _type_or_404(_current_type_id(), req.con)
    return {"suggested": {"id": sid, "name": cvtypes.load(sid).name, "why": why},
            "current": {"id": current.id, "name": current.name, "builtin": current.builtin},
            "writing": cvtypes.writing_card(current), "spelling": settings.get("cvdocs.spelling"),
            "types": [t.summary() for t in cvtypes.all_types(req.con)]}


@route("PUT", "/api/v2/cv/type")
def choose_type(req: Request) -> dict:
    """Uses another CV type: a new version records the change, then Hal works out suggestions to match."""
    t = _type_or_404(str((req.body or {}).get("id") or ""), req.con)
    if t.id == _current_type_id():
        return {"ok": True, "type": {"id": t.id, "name": t.name}, "version_id": None}
    _set_type(req.con, t.id)
    version_id = None
    try:
        _, doc = _store().current()
        version_id = _store().new_version(doc, origin="type_change", what=f"Changed the CV type to {t.name}",
                                          by="you", type_id=t.id)
    except LookupError:
        log.info("no CV yet; only the type was saved")
    if version_id and SUGGEST_KIND in runner.KINDS:
        runner.start(SUGGEST_KIND, subject="cv", replace=True)
    return {"ok": True, "type": {"id": t.id, "name": t.name}, "version_id": version_id}


@route("POST", "/api/v2/cv/types/{type_id}/copy")
def copy_type(req: Request) -> dict:
    """A copy of a type the person can change."""
    _type_or_404(req.params["type_id"], req.con)
    t = cvtypes.copy(req.params["type_id"], str((req.body or {}).get("name") or ""), req.con)
    return {"id": t.id, "name": t.name}


@route("GET", "/api/v2/cv/types/{type_id}/source")
def type_source(req: Request) -> dict:
    """A type's TOML, to read (built-in) or edit (copy)."""
    t = _type_or_404(req.params["type_id"], req.con)
    return {"id": t.id, "name": t.name, "builtin": t.builtin, "toml": t.source}


@route("PUT", "/api/v2/cv/types/{type_id}/source")
def save_type_source(req: Request) -> dict:
    """Saves a copied type's TOML; a mistake answers 400 with the line it is on."""
    text = (req.body or {}).get("toml")
    if not isinstance(text, str):
        raise BadRequest("Send the type's text as toml.", field="toml")
    _type_or_404(req.params["type_id"], req.con)
    try:
        t = cvtypes.save_source(req.params["type_id"], text, req.con)
    except cvtypes.TypeSourceError as e:
        raise BadRequest(str(e), field="toml", fix={"line": e.line}) from e
    return {"ok": True, "id": t.id, "name": t.name}


@route("GET", "/api/v2/cv/types/compare")
def compare_types(req: Request) -> dict:
    """The current CV under two types side by side: outlines and the preview addresses. No AI."""
    a = _type_or_404(req.query.get("a") or _current_type_id(), req.con)
    b = _type_or_404(req.query.get("b") or cvtypes.FALLBACK, req.con)
    try:
        version_id, doc = _store().current()
    except LookupError as e:
        raise NotFound(NO_CV) from e
    url = f"/api/v2/cv/versions/{quote(version_id)}/preview.html?type="
    return {"a_url": url + quote(a.id), "b_url": url + quote(b.id),
            "a": cvtypes.outline(doc, a), "b": cvtypes.outline(doc, b)}


@route("PUT", "/api/v2/cv/writing")
def save_writing(req: Request) -> dict:
    """The Writing card: saved on the current type, copying a built-in first and saying so."""
    body = {k: v for k, v in (req.body or {}).items() if k in cvtypes.WRITING_FIELDS}
    if not body:
        raise BadRequest("Nothing to change on the Writing card.")
    if "pages" in body and (isinstance(body["pages"], bool) or body["pages"] not in range(cvtypes.MAX_PAGES + 1)):
        raise BadRequest("CV length must be 0 (as needed) to 4 pages.", field="pages")
    for key, allowed in (("tone", tuple(cvtypes.TONES)), ("format", ("bullets", "paragraphs")),
                         ("results", ("where_have", "rarely"))):
        if key in body and body[key] not in allowed:
            raise BadRequest(f"{key} must be one of: {', '.join(allowed)}.", field=key)
    current = _type_or_404(_current_type_id(), req.con)
    try:
        t, note = cvtypes.edit_writing(current.id, body, req.con)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    if t.id != current.id:
        _set_type(req.con, t.id)
    return {"ok": True, "type": {"id": t.id, "name": t.name}, "note": note, "writing": cvtypes.writing_card(t)}

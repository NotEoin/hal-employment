"""The My CV API: the header and "employers get" line, the document, its versions, the bin and the sections.

- Every edit is a new version in the store; nothing here changes a version in place.
- A document or layout that does not fit schema 2 answers 400 with the field path (`CVError`), never 409.
- The CV type and look names come from `types.load` and `render.look`; an id neither knows (a type copy
  that was deleted) is shown in words, so the page never fails over a name.
"""
from __future__ import annotations

import importlib
import logging

from jobhunt.core import activity, settings
from jobhunt.cvstudio import checks, model, store
from jobhunt.cvstudio.model import CVError
from jobhunt.web.router import BadRequest, NotFound, route

log = logging.getLogger(__name__)

AREA = "cv"
WHY_YOU = "You changed it on My CV"
ROUTE_WORDS = {"as_is": "Use my CV as it is", "improve": "Help me improve it", "fresh": "Build me a fresh CV"}
SEND_WORDS = {"original": "your original file", "hal_copy": "Hal's copy"}


def _name_of(module: str, fn: str, item_id: str) -> str:
    """A type's or look's name from its owner's module, else its id in words."""
    try:
        found = getattr(importlib.import_module(module), fn)(item_id)
        return str(getattr(found, "name", None) or found["name"])
    except Exception:  # a module still being built, or an id it does not know: the id stands in
        return item_id.replace("_", " ").capitalize()


def _pending(con) -> int:
    return con.execute("SELECT count(*) FROM cv_suggestions WHERE doc_id = ? AND status = 'pending'",
                       (store.DOC_ID,)).fetchone()[0]


def _glance(con) -> dict:
    """The At a glance card: the Checks tab's numbers for the version in use (cached in `cv_checks`)."""
    out = {"scanner": None, "type_pass": None, "type_total": None, "pending": _pending(con)}
    try:
        version_id, doc, t, row = checks.current_cv()
        found = checks.for_version(con, version_id, doc, t, row["look_id"], checks.profile_now(),
                                   spelling=settings.get("cvdocs.spelling"), columns=checks.columns_of(row["look_id"]))
    except Exception:  # the glance is a summary; the page must draw even when the checks cannot run
        log.exception("could not work out the CV checks for the glance card")
        return out
    out.update(scanner=found["scanner"]["score"], type_pass=found["type"]["passed"],
               type_total=found["type"]["total"])
    return out


def summary(con) -> dict:
    """The body of GET /cv: route, send, the employers-get line, type, look, version, file, at a glance,
    the text the import did not find, and the name and contact line."""
    d = store.doc(con)
    vid, doc = store.current(con)
    v = store.version(vid, con=con)
    line, sub = store.employers_get(con)
    baseline = None
    if d["baseline_file_id"]:
        f = store.file_row(d["baseline_file_id"], con=con)
        baseline = {"id": f["id"], "name": f["name"], "added": f["added"], "pages": f["pages"], "sha256": f["sha256"]}
    return {"route": d["route"], "route_words": ROUTE_WORDS[d["route"]], "send": d["send"],
            "employers_get": line, "employers_get_sub": sub,
            "type": {"id": (t := store.type_in_use(con)), "name": _name_of("jobhunt.cvstudio.types", "load", t)},
            "look": {"id": d["look_id"], "name": _name_of("jobhunt.cvstudio.render", "look", d["look_id"])},
            "version": {"id": vid, "n": v["n"], "what": v["what"], "when": v["created"]},
            "baseline": baseline, "glance": _glance(con),
            "not_found": list(doc.extras.get("not_found") or []), **_profile()}


def _profile() -> dict:
    """The name and contact line a CV prints above its body, from your details (never from the CV)."""
    from jobhunt import cv
    from jobhunt.core import settings

    contact = cv.contact()
    return {"name": str(settings.get("you.name") or "").strip(),
            "contact": " | ".join(v for v in contact.values() if v)}


def _bad(e: CVError) -> BadRequest:
    text = str(e)
    path = text.split(":", 1)[0] if ":" in text and " " not in text.split(":", 1)[0] else None
    return BadRequest(text, field=path)


def _note(text: str, *, kind: str = "cv.version", changed: str | None = None) -> None:
    try:
        activity.record(AREA, kind, text, WHY_YOU, icon="file", changed=changed)
    except Exception:  # Activity is a record of what happened; the change itself already stands
        log.exception("could not add the Activity row for %r", text)


@route("GET", "/api/v2/cv")
def get_cv(req) -> dict:
    """The header, the "employers get" line and the glance card."""
    return summary(req.con)


@route("PUT", "/api/v2/cv/route")
def put_route(req) -> dict:
    """How your CV is made; it also sets what employers get."""
    want = str((req.body or {}).get("route") or "")
    try:
        store.set_route(want, con=req.con)
    except CVError as e:
        raise _bad(e) from e
    _note(f"How your CV is made: {ROUTE_WORDS[want]}", kind="cv.route")
    return summary(req.con)


@route("PUT", "/api/v2/cv/send")
def put_send(req) -> dict:
    """What employers get: your original file, or Hal's copy."""
    want = str((req.body or {}).get("send") or "")
    try:
        store.set_send(want, con=req.con)
    except CVError as e:
        raise _bad(e) from e
    _note(f"Employers now get {SEND_WORDS[want]}", kind="cv.send")
    return summary(req.con)


@route("GET", "/api/v2/cv/document")
def get_document(req) -> dict:
    """The version in use (or `?version=`) as schema 2, with its id and number."""
    vid = req.query.get("version") or store.current(req.con)[0]
    try:
        doc = store.get(vid, con=req.con)
    except LookupError as e:
        raise NotFound("That version of your CV is not there any more.") from e
    v = store.version(vid, con=req.con)
    return {**model.to_dict(doc), "version_id": vid, "n": v["n"]}


@route("PUT", "/api/v2/cv/document")
def put_document(req) -> dict:
    """Saves an edit of the whole document as a new version."""
    body = req.body or {}
    what = str(body.get("what") or "You edited your CV").strip()
    data = body.get("doc")
    if not isinstance(data, dict):
        raise BadRequest("Send the CV as `doc`.", field="doc")
    data = {k: v for k, v in data.items() if k not in ("version_id", "n")}
    try:
        doc = model.from_dict(data)
        before = store.current(req.con)[1]
        vid = store.new_version(doc, origin="manual", what=what, by="you", con=req.con)
    except CVError as e:
        raise _bad(e) from e
    from ..writing import edits

    edits.from_cv(req.con, before, doc)
    n = store.version(vid, con=req.con)["n"]
    _note(f"Saved your CV as version {n}: {what}")
    return {"version_id": vid, "n": n}


@route("GET", "/api/v2/cv/versions")
def get_versions(req) -> list[dict]:
    """Every version of the main CV, newest first; binned ones included and marked."""
    store.current(req.con)
    return store.versions(req.con)


def _version_or_404(req, vid: str) -> dict:
    try:
        return store.version(vid, con=req.con)
    except LookupError as e:
        raise NotFound("That version of your CV is not there any more.") from e


@route("POST", "/api/v2/cv/versions/{vid}/use")
def use_version(req) -> dict:
    """Puts a version back in use; the one it replaces is kept."""
    v = _version_or_404(req, req.params["vid"])
    try:
        store.use(v["id"], con=req.con)
    except CVError as e:
        raise _bad(e) from e
    _note(f"Version {v['n']} of your CV is in use again")
    return summary(req.con)


@route("DELETE", "/api/v2/cv/versions/{vid}")
def bin_version(req) -> dict:
    """Moves a version to the bin, where it stays for 30 days."""
    v = _version_or_404(req, req.params["vid"])
    try:
        store.bin_version(v["id"], con=req.con)
    except CVError as e:
        raise _bad(e) from e
    _note(f"Moved version {v['n']} of your CV to the bin", kind="cv.bin")
    return {"ok": True}


@route("POST", "/api/v2/cv/versions/{vid}/restore")
def restore_version(req) -> dict:
    """Takes a version out of the bin."""
    v = _version_or_404(req, req.params["vid"])
    store.restore(v["id"], con=req.con)
    _note(f"Took version {v['n']} of your CV out of the bin", kind="cv.bin")
    return {"ok": True}


@route("GET", "/api/v2/cv/compare")
def compare(req) -> dict:
    """What changed from version `a` to version `b`."""
    a, b = req.query.get("a"), req.query.get("b")
    if not a or not b:
        raise BadRequest("Pick two versions to compare.", field="a" if not a else "b")
    va, vb = _version_or_404(req, a), _version_or_404(req, b)
    return {"a": {"id": va["id"], "n": va["n"]}, "b": {"id": vb["id"], "n": vb["n"]},
            "changes": store.compare(a, b, con=req.con)}


@route("GET", "/api/v2/cv/layout")
def get_layout(req) -> dict:
    """Sections in order with their names, limits, locks and Show, and the locked lines."""
    return store.layout(req.con)


@route("PUT", "/api/v2/cv/layout")
def put_layout(req) -> dict:
    """Saves the Sections card; a change to the CV makes a new version."""
    body = req.body or {}
    sections = body.get("sections")
    if not isinstance(sections, list) or not all(isinstance(x, dict) and "id" in x for x in sections):
        raise BadRequest("Send every section with its id.", field="sections")
    locked = body.get("locked_lines")
    if locked is not None and not isinstance(locked, list):
        raise BadRequest("Send the locked lines as a list.", field="locked_lines")
    try:
        vid = store.set_layout(sections, locked, con=req.con)
    except (CVError, ValueError) as e:
        raise _bad(CVError(str(e))) from e
    if vid:
        _note(f"Changed your CV's sections (version {store.version(vid, con=req.con)['n']})")
    return {"ok": True, "version_id": vid}

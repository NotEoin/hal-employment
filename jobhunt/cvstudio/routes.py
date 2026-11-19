"""The My CV API: the person's CVs, and for each the header and "employers get" line, the document, its
versions, the bin and the sections.

- Every route acts on one CV, named by the query parameter `?cv=<id>` (`cv_of`); without it, the lead CV
  (`store.lead_id`), so the setup guide, the CLI and older pages keep working with one CV. A query parameter
  rather than a path part, so the many existing `/cv/...` paths keep their shape. Routes that name a version
  act on the CV that version belongs to.
- `GET /cv/cvs` lists the CVs, in use and archived; star, archive (and restore), rename and the starred
  order have routes of their own, and `DELETE /cv` archives: an application may point at a CV, so a CV is
  never deleted. Each of these may change which CV leads Your record's facts, so each ends by letting the
  record follow (`record_cvs.settle_lead`).
- Every edit is a new version in the store; nothing here changes a version in place.
- A document or layout that does not fit schema 2 answers 400 with the field path (`CVError`). Only a save of
  the editor's changes (`PATCH /cv/document`) answers 409, when the version it was made on is no longer in
  use, so the person can reload and keep what they want.
- `GET /cv/pages` prints in the request thread, as the PDF routes do: once per version and printed page, so
  the editor asks right after a save and gets the count in about a second (`pagelimit`).
- The CV type and look names come from `types.load` and `render.look`; an id neither knows (a type copy
  that was deleted) is shown in words, so the page never fails over a name.
"""
from __future__ import annotations

import importlib
import logging

from jobhunt.core import activity, settings
from jobhunt.cvstudio import checks, edit, model, original, pagelimit, parse_actions, record_cvs, store
from jobhunt.cvstudio.model import CVError
from jobhunt.web.router import BadRequest, Conflict, NotFound, route

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


def cv_of(req) -> str | None:
    """The CV a request acts on: `?cv=<id>` once checked (404 when there is no such CV), else the lead CV,
    None while there is no CV at all. It never makes a CV; the store does that on first use."""
    wanted = req.query.get("cv")
    if not wanted:
        return store.lead_id(req.con)
    try:
        return store.doc(req.con, wanted)["id"]
    except store.NoSuchCV as e:
        raise NotFound("That CV is not in Hal any more.") from e


def _pending(con, doc_id: str) -> int:
    return con.execute("SELECT count(*) FROM cv_suggestions WHERE doc_id = ? AND job_id IS NULL "
                       "AND status = 'pending'", (doc_id,)).fetchone()[0]


def _glance(con, doc_id: str) -> dict:
    """The At a glance card: the Checks tab's numbers for the CV's version in use (cached in `cv_checks`)."""
    out = {"scanner": None, "type_pass": None, "type_total": None, "pending": _pending(con, doc_id)}
    try:
        version_id, doc, t, row = checks.current_cv(doc_id)
        found = checks.for_version(con, version_id, doc, t, row["look_id"], checks.profile_now(),
                                   spelling=settings.get("cvdocs.spelling"), columns=checks.columns_of(row["look_id"]),
                                   pages=pagelimit.known(version_id, con)[0], limit=pagelimit.limit_of(doc_id, con)[0])
    except Exception:  # the glance is a summary; the page must draw even when the checks cannot run
        log.exception("could not work out the CV checks for the glance card")
        return out
    out.update(scanner=found["scanner"]["score"], type_pass=found["type"]["passed"],
               type_total=found["type"]["total"])
    return out


def summary(con, doc_id: str | None = None) -> dict:
    """The body of GET /cv for one CV (the lead CV by default): which CV it is and how many there are, route,
    send, the employers-get line, type, look, version, file, what a preview shows of that file while it is sent
    (`original.view`), at a glance, the text the import did not find, and the name and contact line."""
    doc_id = store.doc(con, doc_id)["id"]
    vid, doc = store.current(con, doc_id)
    v = store.version(vid, con=con)
    line, sub = store.employers_get(con, doc_id)
    d = store.doc(con, doc_id)
    baseline = None
    if d["baseline_file_id"]:
        f = store.file_row(d["baseline_file_id"], con=con)
        baseline = {"id": f["id"], "name": f["name"], "added": f["added"], "pages": f["pages"], "sha256": f["sha256"]}
    type_id = store.type_in_use(con, doc_id)
    return {"cv": {"id": doc_id, "name": d["name"] or store.DEFAULT_NAME, "starred": bool(d["starred"]),
                   "archived": d["archived"], "lead": doc_id == store.lead_id(con)},
            "count": con.execute("SELECT count(*) FROM cv_documents").fetchone()[0],
            "route": d["route"], "route_words": ROUTE_WORDS[d["route"]], "send": d["send"],
            "employers_get": line, "employers_get_sub": sub,
            "type": {"id": type_id, "name": _name_of("jobhunt.cvstudio.types", "load", type_id)},
            "look": {"id": d["look_id"], "name": _name_of("jobhunt.cvstudio.render", "look", d["look_id"])},
            "version": {"id": vid, "n": v["n"], "what": v["what"], "when": v["created"]},
            "baseline": baseline, "original": original.view(con, doc_id), "glance": _glance(con, doc_id),
            "not_found": list(doc.extras.get("not_found") or []), **_profile()}


def _profile() -> dict:
    """The name and contact line a CV prints above its body, from your details (never from the CV)."""
    from jobhunt.core import settings
    from jobhunt.cvstudio import cv

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
    """The header, the "employers get" line and the glance card of `?cv=` (the lead CV by default)."""
    return summary(req.con, cv_of(req))


# ------------------------------------------------------------------------------------------------- the CVs

def _cvs(con) -> dict:
    """The list My CVs draws; `reading` marks a CV whose file Hal is reading now (`parse_actions.reading_cvs`)."""
    reading = parse_actions.reading_cvs(con)
    def marked(archived: bool) -> list[dict]:
        return [{**d, "reading": d["id"] in reading} for d in store.documents(con, archived=archived)]

    return {"cvs": marked(False), "archived": marked(True), "lead": store.lead_id(con)}


@route("GET", "/api/v2/cv/cvs")
def list_cvs(req) -> dict:
    """The person's CVs in use (starred first, in their order), the archived ones, and the lead CV's id."""
    if store.lead_id(req.con) is None:
        store.current(req.con)          # the first visit makes the first CV, as GET /cv does
    return _cvs(req.con)


def _change(req, fn, said: str) -> dict:
    """Runs `fn(doc_id)` on the CV `?cv=` names (required here), notes it in Activity, answers the list."""
    if not req.query.get("cv"):
        raise BadRequest("Say which CV.", field="cv")
    doc_id = cv_of(req)
    try:
        d = fn(doc_id)
    except CVError as e:
        raise _bad(e) from e
    _note(said.format(name=d["name"] or store.DEFAULT_NAME), kind="cv.cvs")
    record_cvs.settle_lead(req.con)
    return _cvs(req.con)


@route("PUT", "/api/v2/cv/star")
def put_star(req) -> dict:
    """"Use this for applications": `{on}` for `?cv=`. The last starred CV keeps its star (400 saying why)."""
    on = bool((req.body or {}).get("on"))
    return _change(req, lambda d: store.star(d, on, con=req.con),
                   "Hal uses {name} for applications" if on else "Hal no longer uses {name} for applications")


@route("PUT", "/api/v2/cv/archive")
def put_archive(req) -> dict:
    """Moves `?cv=` to the archive (`{on: true}`, the default) or restores it (`{on: false}`)."""
    on = bool((req.body or {}).get("on", True))
    return _change(req, lambda d: store.archive(d, on, con=req.con),
                   "Moved {name} to the archive" if on else "Restored {name} from the archive")


@route("DELETE", "/api/v2/cv")
def delete_cv(req) -> dict:
    """Deleting a CV archives it: applications may point at it, so nothing in it is removed."""
    return _change(req, lambda d: store.archive(d, True, con=req.con), "Moved {name} to the archive")


@route("PUT", "/api/v2/cv/name")
def put_name(req) -> dict:
    """Renames `?cv=`."""
    name = str((req.body or {}).get("name") or "")
    return _change(req, lambda d: store.rename(d, name, con=req.con), "Renamed a CV to {name}")


@route("PUT", "/api/v2/cv/order")
def put_order(req) -> dict:
    """The order of the starred CVs: `{ids}`, every starred CV once; the first is the lead CV."""
    ids = (req.body or {}).get("ids")
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise BadRequest("Send the CVs' ids in order.", field="ids")
    try:
        store.reorder(ids, con=req.con)
    except CVError as e:
        raise _bad(e) from e
    record_cvs.settle_lead(req.con)
    return _cvs(req.con)


# ----------------------------------------------------------------------------------------- one CV's parts

@route("PUT", "/api/v2/cv/route")
def put_route(req) -> dict:
    """How your CV is made; it also sets what employers get."""
    want = str((req.body or {}).get("route") or "")
    doc_id = cv_of(req)
    try:
        store.set_route(want, con=req.con, doc_id=doc_id)
    except CVError as e:
        raise _bad(e) from e
    _note(f"How your CV is made: {ROUTE_WORDS[want]}", kind="cv.route")
    return summary(req.con, doc_id)


@route("PUT", "/api/v2/cv/send")
def put_send(req) -> dict:
    """What employers get: your original file, or Hal's copy."""
    want = str((req.body or {}).get("send") or "")
    doc_id = cv_of(req)
    try:
        store.set_send(want, con=req.con, doc_id=doc_id)
    except CVError as e:
        raise _bad(e) from e
    _note(f"Employers now get {SEND_WORDS[want]}", kind="cv.send")
    return summary(req.con, doc_id)


@route("GET", "/api/v2/cv/document")
def get_document(req) -> dict:
    """The CV's version in use (or `?version=`) as schema 2, with its id and number."""
    vid = req.query.get("version") or store.current(req.con, cv_of(req))[0]
    try:
        doc = store.get(vid, con=req.con)
    except LookupError as e:
        raise NotFound("That version of your CV is not there any more.") from e
    v = store.version(vid, con=req.con)
    return {**model.to_dict(doc), "version_id": vid, "n": v["n"]}


@route("PUT", "/api/v2/cv/document")
def put_document(req) -> dict:
    """Saves an edit of the whole document as a new version of the CV."""
    body = req.body or {}
    what = str(body.get("what") or "You edited your CV").strip()
    data = body.get("doc")
    if not isinstance(data, dict):
        raise BadRequest("Send the CV as `doc`.", field="doc")
    data = {k: v for k, v in data.items() if k not in ("version_id", "n")}
    doc_id = cv_of(req)
    try:
        doc = model.from_dict(data)
        before = store.current(req.con, doc_id)[1]
        vid = store.new_version(doc, origin="manual", what=what, by="you", doc_id=doc_id, con=req.con)
    except CVError as e:
        raise _bad(e) from e
    from jobhunt.writing import edits

    edits.from_cv(req.con, before, doc)
    n = store.version(vid, con=req.con)["n"]
    _note(f"Saved your CV as version {n}: {what}")
    return {"version_id": vid, "n": n}


@route("PATCH", "/api/v2/cv/document")
def patch_document(req) -> dict:
    """Saves the Edit your CV changes, made on version `base`, as one new version of the CV `base` is in."""
    body = req.body or {}
    base = str(body.get("base") or "")
    if not base:
        raise BadRequest("Say which version you edited.", field="base")
    what = str(body.get("what") or "").strip() or "You edited your CV"
    try:
        vid, before, after = edit.save(req.con, base, edit.changes_from(body.get("changes")), what)
    except store.StaleVersion as e:
        raise Conflict(str(e)) from e
    except LookupError as e:
        raise Conflict("That version of your CV is not there any more. Your changes are not saved.") from e
    except CVError as e:
        raise _bad(e) from e
    from jobhunt.writing import edits

    edits.from_cv(req.con, before, after)
    n = store.version(vid, con=req.con)["n"]
    _note(f"Saved your CV as version {n}: {what}")
    return {"version_id": vid, "n": n}


@route("GET", "/api/v2/cv/pages", slow=True)
def get_pages(req) -> dict:
    """"Keep this CV to" and the version in use's printed pages (`pagelimit.state`), printing it once when it
    has no count yet; `?saved=1` words the warning for right after a save ("This CV is now 3 pages")."""
    return pagelimit.state(cv_of(req), req.con, now=req.query.get("saved") == "1")


@route("PUT", "/api/v2/cv/pages")
def put_pages(req) -> dict:
    """Sets "Keep this CV to" (`{pages}`, one of the card's choices: 0 for no limit, 1 or 2, or a copied type's
    longer rule) for `?cv=`; answers as GET without printing."""
    pages = (req.body or {}).get("pages")
    doc_id = cv_of(req)
    offered = [n for n, _ in pagelimit.state(doc_id, req.con, count=False)["choices"]]
    if isinstance(pages, bool) or pages not in offered:
        raise BadRequest(f"Choose one of: {', '.join(pagelimit.CHOICE_WORDS[n].lower() for n in offered)}.",
                         field="pages")
    try:
        store.set_page_limit(pages, con=req.con, doc_id=doc_id)
    except CVError as e:
        raise _bad(e) from e
    _note(f"Keep this CV to: {pagelimit.CHOICE_WORDS[pages].lower()}", kind="cv.pages")
    return pagelimit.state(doc_id, req.con, count=False)


@route("GET", "/api/v2/cv/record-cards")
def get_record_cards(req) -> list[dict]:
    """The jobs, education, projects and certificates in your record, each as the CV entry it makes."""
    return edit.cards(req.con)


@route("GET", "/api/v2/cv/versions")
def get_versions(req) -> list[dict]:
    """Every version of the CV, newest first, numbered from 1; binned ones included and marked."""
    doc_id = cv_of(req)
    store.current(req.con, doc_id)
    return store.versions(req.con, doc_id)


def _version_or_404(req, vid: str) -> dict:
    try:
        return store.version(vid, con=req.con)
    except LookupError as e:
        raise NotFound("That version of your CV is not there any more.") from e


@route("POST", "/api/v2/cv/versions/{vid}/use")
def use_version(req) -> dict:
    """Puts a version back in use in its CV; the one it replaces is kept."""
    v = _version_or_404(req, req.params["vid"])
    try:
        store.use(v["id"], con=req.con)
    except CVError as e:
        raise _bad(e) from e
    _note(f"Version {v['n']} of your CV is in use again")
    return summary(req.con, v["doc_id"])


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
    return store.layout(req.con, cv_of(req))


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
        vid = store.set_layout(sections, locked, con=req.con, doc_id=cv_of(req))
    except (CVError, ValueError) as e:
        raise _bad(CVError(str(e))) from e
    if vid:
        _note(f"Changed your CV's sections (version {store.version(vid, con=req.con)['n']})")
    return {"ok": True, "version_id": vid}

"""The API of the "Add" panels on Your record: the sections, their sources, and adding, changing, reading again
and removing a source.

The paths sit under `/api/v2/record/sources`: `/api/v2/sources` is the job sites Hal searches
(`find/source_routes.py`). Reading takes a while, so adding a source or asking to read it again starts the
`record.sources` action and returns its id at once; the page follows that action's events. Every change
publishes a `profile` event with the source's id, so open pages read their lists again.

Shapes, exactly as sent:

A section (`SECTION`), from `GET /api/v2/record/sections`:

    {"id": "coursework", "label": "Coursework", "hint": "Essays, reports, ...", "role": "own",
     "target": "education", "target_required": true,
     "doc_kinds": [{"id": "essay", "label": "Essay", "held": "upload", "ready": false}, ...]}

`held` says how a kind of document is added: upload (one file each), files (a folder or zip), link (a web
address), account (a username), folder (a folder on this computer) or manual (typed in); `ready` is false
while its reader is still to come, and the page shows it as coming soon.

A source (`SOURCE`), from `sections.store.shape`:

    {"id": 4, "section": "document", "section_label": "Documents", "target_id": null, "held": "upload",
     "doc_kind": "other", "doc_kind_label": "Something else", "title": "Volunteer of the year",
     "name": "award.pdf", "files": 1, "locator": "", "starred": false, "can_star": false, "current": false,
     "can_be_current": false, "dated": "2025-03", "note_rel": "Sources/Documents/Volunteer of the year.md",
     "added": "2026-11-09T09:00:00Z", "last_checked": "...", "last_changed": "...", "revision": 1,
     "state": "read", "state_words": "Read", "error": "", "suggestion": "cv", "suggestion_label": "CVs"}

Routes:

- `GET /api/v2/record/sections`: [SECTION], in the page's order.
- `GET /api/v2/record/sources?section=&target=`: [SOURCE] of one section (all without `section`), for one
  card with `target`.
- `POST /api/v2/record/sources/{section}`: a multipart form of files `file0`, `file1`, ... with `doc_kind`,
  `target_id`, and `path0`, `path1`, ... the path of each file inside a folder the person chose (none for a
  loose file). A zip is one source of its files. Up to `UPLOAD_MAX_BODY` in all. Returns {"added": [SOURCE],
  "duplicates": [{"name", "id", "section_label"}], "problems": [words], "action_id"}.
- `POST /api/v2/record/sources/{section}/link` {"locator", "doc_kind", "target_id"}: a web address, an account
  name, a folder on this computer (only from this computer, and never the record or Hal's own folder) or a
  project's name typed in; returns the same as an upload.
- `PUT /api/v2/record/sources/{id}` {"title", "doc_kind", "target_id", "starred", "current", "section"}: any of
  them; a new section, kind of document or card reads the source again. Returns SOURCE with "action_id".
- `POST /api/v2/record/sources/{id}/reread` {"allow": true to read one kept out as private}: {"action_id"}.
- `DELETE /api/v2/record/sources/{id}`: removes it with its note and facts; {"hidden": [card ids]}.

A kind of document or card that does not fit is 400 with plain words; a section, source or card that is not
there is 404 (the router's answer to LookupError).
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import events, paths
from jobhunt.evidence.routes import UPLOAD_MAX_BODY
from jobhunt.sections import actions, pipeline, readers, registry, store
from jobhunt.web.router import BadRequest, NotFound, NotHere, Request, route

PREFIX = "/api/v2/record/sources"
FILE_FIELD, PATH_FIELD = "file", "path"
REREAD_ON = ("section", "doc_kind", "target_id")


def _session(req: Request) -> str | None:
    return getattr(req.session, "id", None)


def _body(req: Request) -> dict:
    return req.body if isinstance(req.body, dict) else {}


def _start(req: Request, ids: list[int], *, allow: list[int] = (), why: str) -> str | None:
    return actions.queue(ids, allow=allow, session_id=_session(req), remote=req.remote, why=why)


def _section(req: Request) -> registry.Section:
    try:
        return registry.get(req.params["section"])
    except LookupError as e:
        raise NotFound(str(e)) from e


def _added(req: Request, out: dict, why: str) -> dict:
    events.publish("profile", sources=out["added"])
    return {"added": [store.get(req.con, i) for i in out["added"]], "duplicates": out["duplicates"],
            "problems": out.get("problems", []), "action_id": _start(req, out["added"], why=why)}


@route("GET", "/api/v2/record/sections")
def sections(req: Request) -> list[dict]:
    """Every section with its kinds of document, and whether each one's reader is ready."""
    return [{"id": s.id, "label": s.label, "hint": s.hint, "role": s.role, "target": s.target,
             "target_required": s.target_required,
             "doc_kinds": [{"id": k, "label": registry.DOC_KINDS[k].label, "held": registry.DOC_KINDS[k].held,
                            "ready": readers.ready(s.reader_of(k))} for k in s.doc_kinds]}
            for s in registry.SECTIONS.values()]


@route("GET", PREFIX)
def listing(req: Request) -> list[dict]:
    """The sources of one section, or of one card."""
    target = req.query.get("target") or None
    try:
        return store.listing(req.con, req.query.get("section") or None, int(target) if target else None)
    except (LookupError, ValueError) as e:
        raise BadRequest("Choose a section of your record and a card by its number.") from e


@route("POST", PREFIX + "/{section}", max_body=UPLOAD_MAX_BODY)
def upload(req: Request) -> dict:
    """Keeps the uploaded files as sources of the section and starts reading them."""
    section = _section(req)
    fields = _body(req)
    uploads = [pipeline.Incoming(up.name, up.data, str(fields.get(PATH_FIELD + key[len(FILE_FIELD):]) or ""))
               for key, up in sorted(req.files.items()) if key.startswith(FILE_FIELD)]
    if not uploads:
        raise BadRequest("Choose one or more files to add.")
    try:
        out = pipeline.add_uploads(req.con, section.id, uploads, doc_kind=fields.get("doc_kind") or None,
                                   target_id=fields.get("target_id") or None)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    return _added(req, out, f"You added files to {section.label}")


def _inside(path: Path, folder: Path) -> bool:
    folder = folder.resolve()
    return path == folder or folder in path.parents


def _folder(req: Request, raw: str) -> str:
    """A folder on this computer, checked: there, and not the record or Hal's own folder."""
    if req.remote:
        raise NotHere("Add a folder on the computer Hal runs on, or upload it from this one.")
    path = Path(raw.strip()).expanduser().resolve()
    if not path.is_dir():
        raise BadRequest(f"{raw} is not a folder. Choose a folder that exists.", field="locator")
    if _inside(path, Path(record._live("VAULT"))) or _inside(path, paths.data_dir()):
        raise BadRequest(f"{raw} is inside your record or Hal's own folder, so Hal already has it.",
                         field="locator")
    return str(path)


@route("POST", PREFIX + "/{section}/link")
def link(req: Request) -> dict:
    """Keeps a link, account, folder on this computer or a project typed in as a source, and starts reading it."""
    section = _section(req)
    body = _body(req)
    locator = str(body.get("locator") or "")
    try:
        kind = registry.doc_kind(section, body.get("doc_kind") or None)
        if kind.held == registry.FOLDER and locator.strip():
            locator = _folder(req, locator)
        out = pipeline.add_link(req.con, section.id, locator, doc_kind=kind.id, target_id=body.get("target_id"))
    except ValueError as e:
        raise BadRequest(str(e), field="locator") from e
    return _added(req, out, f"You added {kind.label.lower()} to {section.label}")


@route("PUT", PREFIX + "/{source_id}")
def change(req: Request) -> dict:
    """Changes a source's title, kind of document, card, star, current mark or section."""
    body = _body(req)
    try:
        got = store.change(req.con, req.params["source_id"], body)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    again = any(k in body for k in REREAD_ON)
    events.publish("profile", source_id=got["id"])
    return {**got, "action_id": _start(req, [got["id"]], why="You changed where a source of your record goes")
            if again else None}


@route("POST", PREFIX + "/{source_id}/reread")
def reread(req: Request) -> dict:
    """Starts reading a source again; with `allow`, one kept out as private is read anyway."""
    got = store.get(req.con, req.params["source_id"])
    allow = [got["id"]] if _body(req).get("allow") is True else []
    return {"action_id": _start(req, [got["id"]], allow=allow, why="You asked Hal to read a source again")}


@route("DELETE", PREFIX + "/{source_id}")
def remove(req: Request) -> dict:
    """Removes a source with its note and facts; the cards it alone named are hidden."""
    out = pipeline.remove(req.con, req.params["source_id"])
    events.publish("profile", source_id=int(req.params["source_id"]))
    return out

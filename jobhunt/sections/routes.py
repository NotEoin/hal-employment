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
     "state": "read", "state_words": "Read", "error": "", "suggestion": "cv", "suggestion_label": "CVs",
     "suggestion_kind": "", "suggestion_kind_label": "", "current_chosen": false, "mark": "", "summary": "",
     "can_have_module": false, "module": null, "module_suggested": "", "module_choices": [],
     "dissertation": false, "can_be_dissertation": false}

`suggestion` is a section the source looks like it belongs in, and `suggestion_kind` another kind of document of
its own section that it looks like (a transcript added to Documents as "Something else"). `current` marks the
CV that leads (`profile.cvs`), and `current_chosen` says the person chose it.

`mark` and `summary` are what the reader found (coursework). For coursework and lecture material
`can_have_module` is true, `module` is the person's choice (null until they choose, "" for none),
`module_suggested` Hal's suggestion (one of `module_choices`, the card's modules, or "").
Coursework can be the course's dissertation (`can_be_dissertation`; one per card).

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
  project's name typed in; returns the same as an upload. A project added by hand may send `"form"` (name,
  what, when, part, tools, links; `sections.manual.clean_form`), whose name is then its locator.
- `POST /api/v2/record/sources/{id}/files`: files `file0`, `file1`, ... attached to a project added by hand;
  {"added": [names], "problems": [words], "action_id"}.
- `PUT /api/v2/record/sources/{id}` {"title", "doc_kind", "target_id", "starred", "current", "section",
  "module", "dissertation"}: any of them; a new section, kind of document, card, module, star or dissertation
  mark reads the source again (a starred piece of coursework is read into a showcase project). Returns SOURCE
  with "action_id". A project also takes `"theirs"` (true: one with none of the person's commits is theirs), a portfolio
  `"picked"` (the web addresses of the pieces to add) and a project added by hand `"form"`; each reads it
  again. A project's SOURCE adds "parent_id", "what_changed", "not_yours", "theirs", "pieces" [{"url",
  "title"}], "picked" and "form".
- `POST /api/v2/record/sources/{id}/reread` {"allow": true to read one kept out as private}: {"action_id"}.
- `POST /api/v2/record/sources/{id}/back`: puts back the facts of the read before the last
  (`sections.revisions`); SOURCE with "hidden", or 404 when no earlier read is kept.
- `POST /api/v2/record/sources/{id}/split`: each folder of a code project that is a project itself becomes
  its own source under it (`sections.split`); {"added": [SOURCE], "action_id"}, or 400 when there are none.
- `DELETE /api/v2/record/sources/{id}`: removes it with its note and facts; {"hidden": [card ids]}.

The local API key opens the listing, both ways of adding and reading again, for `jobhunt sources`.

A kind of document or card that does not fit is 400 with plain words; a section, source or card that is not
there is 404 (the router's answer to LookupError).
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import events, paths
from jobhunt.evidence.routes import UPLOAD_MAX_BODY
from jobhunt.sections import actions, pipeline, readers, registry, revisions, store
from jobhunt.web.router import BadRequest, NotFound, NotHere, Request, route

PREFIX = "/api/v2/record/sources"
FILE_FIELD, PATH_FIELD = "file", "path"
REREAD_ON = ("section", "doc_kind", "target_id", "module", "starred", "dissertation", "theirs", "picked", "form")


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


@route("GET", PREFIX, auth="session_or_local")
def listing(req: Request) -> list[dict]:
    """The sources of one section, or of one card."""
    target = req.query.get("target") or None
    try:
        return store.listing(req.con, req.query.get("section") or None, int(target) if target else None)
    except (LookupError, ValueError) as e:
        raise BadRequest("Choose a section of your record and a card by its number.") from e


@route("POST", PREFIX + "/{section}", auth="session_or_local", max_body=UPLOAD_MAX_BODY)
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


@route("POST", PREFIX + "/{section}/link", auth="session_or_local")
def link(req: Request) -> dict:
    """Keeps a link, account, folder on this computer or a project typed in as a source, and starts reading it."""
    section = _section(req)
    body = _body(req)
    locator = str(body.get("locator") or "")
    try:
        kind = registry.doc_kind(section, body.get("doc_kind") or None)
        if kind.held == registry.FOLDER and locator.strip():
            locator = _folder(req, locator)
        if kind.held == registry.MANUAL and body.get("form") is not None:
            from jobhunt.sections import manual

            form = manual.clean_form(body["form"])
            locator = form["name"]
        out = pipeline.add_link(req.con, section.id, locator, doc_kind=kind.id, target_id=body.get("target_id"))
        if kind.held == registry.MANUAL and body.get("form") is not None:
            for source_id in out["added"]:
                store.set_meta(req.con, source_id, form=form)
    except ValueError as e:
        raise BadRequest(str(e), field="locator") from e
    return _added(req, out, f"You added {kind.label.lower()} to {section.label}")


@route("POST", PREFIX + "/{source_id}/files", max_body=UPLOAD_MAX_BODY)
def attach(req: Request) -> dict:
    """Attaches files and pictures to a project added by hand, and reads it again."""
    uploads = [pipeline.Incoming(up.name, up.data) for key, up in sorted(req.files.items())
               if key.startswith(FILE_FIELD)]
    try:
        out = pipeline.attach(req.con, req.params["source_id"], uploads)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    source_id = int(req.params["source_id"])
    events.publish("profile", source_id=source_id)
    return {**out, "action_id": _start(req, [source_id], why="You attached files to a project")}


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


@route("POST", PREFIX + "/{source_id}/reread", auth="session_or_local")
def reread(req: Request) -> dict:
    """Starts reading a source again; with `allow`, one kept out as private is read anyway."""
    got = store.get(req.con, req.params["source_id"])
    allow = [got["id"]] if _body(req).get("allow") is True else []
    return {"action_id": _start(req, [got["id"]], allow=allow, why="You asked Hal to read a source again")}


@route("POST", "/api/v2/record/read-waiting", auth="session_or_local")
def read_waiting(req: Request) -> dict:
    """Starts reading every source still waiting to be read, such as those that waited for Hal's AI."""
    ids = [r["id"] for r in req.con.execute(
        "SELECT id FROM record_sources WHERE state IN ('new', 'reading') ORDER BY id").fetchall()]
    return {"waiting": len(ids), "action_id": _start(req, ids, why="You asked Hal to read what is waiting")}


@route("POST", PREFIX + "/{source_id}/back")
def go_back(req: Request) -> dict:
    """Puts back the facts of the read before the last one; 404 when Hal keeps no earlier read."""
    got = store.get(req.con, req.params["source_id"])
    try:
        out = revisions.go_back(req.con, got["id"])
    except LookupError as e:
        raise NotFound(str(e)) from e
    events.publish("profile", source_id=got["id"])
    return {**store.get(req.con, got["id"]), "hidden": out["hidden"]}


@route("POST", PREFIX + "/{source_id}/split")
def split_by_folder(req: Request) -> dict:
    """Adds each folder of a code project that is a project of its own as its own source, and reads them."""
    from jobhunt.sections import split

    try:
        new = split.split(req.con, req.params["source_id"])
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("profile", sources=new)
    return {"added": [store.get(req.con, i) for i in new],
            "action_id": _start(req, new, why="You split a code project into projects by folder")}


@route("DELETE", PREFIX + "/{source_id}")
def remove(req: Request) -> dict:
    """Removes a source with its note and facts; the cards it alone named are hidden."""
    out = pipeline.remove(req.con, req.params["source_id"])
    events.publish("profile", source_id=int(req.params["source_id"]))
    return out

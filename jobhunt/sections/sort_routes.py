"""The API of "Sort a big folder for me": start a sort, page through its proposals, change them, and accept them.

Sorting and accepting take a while, so starting either returns the action's id at once; the page follows the
action and the `sort` event that names the sort. The local API key opens these routes too, so `jobhunt sort`
works through the running Hal, as `jobhunt ingest` did.

Shapes, exactly as sent:

A sort (`SORT`, `sorts.shape`):

    {"id": 3, "kind": "folder", "label": "Avery stuff", "state": "ready", "error": "",
     "created": "2026-11-09T09:00:00Z", "updated": "...", "skipped": {"junk folder": 12},
     "counts": {"sections": [{"id": "cv", "label": "CVs", "proposed": 2, "ticked": 2, "needs_card": 0}, ...],
                "proposed": 40, "left_out": 9, "copy": 3, "accepted": 0, "added": 0, "failed": 0, "total": 52},
     "undone": false, "can_undo": false}

`kind` is folder, upload, inbox or move; `state` new, sorting, ready, failed or undone.

A row (`ROW`, `sorts.shape_row`):

    {"id": 17, "rel": "Avery stuff/CV/CV.pdf", "name": "CV.pdf", "held": "upload", "size": 48213,
     "mtime": "2024-10-27T10:00:00Z", "section": "cv", "section_label": "CVs", "doc_kind": "cv",
     "doc_kind_label": "CV", "target_id": null, "target_title": "", "target_kind": "", "needs_card": false,
     "star": false, "can_star": false, "by": "rules", "why": "Named or set out like a CV.", "ticked": true,
     "state": "proposed", "copy_of": "", "source_id": null, "note_rel": null, "copies": []}

`copies` lists the older copies Hal set aside for a proposed row ("Older copies"), each {"id", "rel"}.

A group of a module's lecture material or coursework (`GROUP`, `sort_review.rows`): a ROW's fields with
"id": null, "held": "group", the files' "count", the module's "name" and folder ("rel"), and its "group"
key ("lecture|12|Avery stuff/Uni/BSc Computing/Year 2/CS2041 Databases"). A project's numbered folders come
the same way, named by the first and last ("lab1 to lab8"), keyed "project||Avery stuff/Code/lab".

A course Hal found in the folders (`COURSE`, `sort_courses.courses_found`), not yet a card:

    {"key": "Avery stuff/Uni/BSc Computing", "keys": ["Avery stuff/Uni/BSc Computing"], "title": "BSc Computing",
     "qualification": "BSc", "subject": "Computing", "institution": "", "modules": ["CS2041 Databases"],
     "files": 14, "start": "2022-10", "end": "2024-05"}

Routes:

- `GET /api/v2/record/sort`: {"sorts": [SORT], newest first, "cards": {"education": [{"id", "title"}], "job":
  [...]}, "can_move": true when facts come from an older Hal's notes that no move has set aside}.
- `POST /api/v2/record/sort` {"folders": [paths]}: sorts folders on this computer (local only); {"sort",
  "action_id"}.
- `POST /api/v2/record/sort/upload`: a multipart form of files `file0`, `file1`, ... with `path0`, ... (a
  path inside a folder the person chose); zips are unpacked. Kept in Hal's data folder; {"sort", "action_id"}.
- `GET /api/v2/record/sort/{id}?section=&state=proposed&offset=0&limit=50&group=&flat=`: {SORT, "rows":
  [ROW or GROUP], "total", "cards", "courses": [COURSE], "notes": [{"id", "words"}]}, the notes said once
  for the whole sort (`sort_review.notes`: `no_history`, `github`). Proposed lecture material and coursework come one
  GROUP a module (`sort_review.rows`) unless `flat=1`; `group` lists one group's own rows.
- `POST /api/v2/record/sort/{id}/groups` {"group", "ticked", "section", "target_id"}: changes every file of a
  group at once; {"changed"}.
- `POST /api/v2/record/sort/{id}/courses` {"key", "qualification", "subject", "institution"}: makes the
  education card for a course Hal found, with the words given (else Hal's); {"card", "matched"}.
- `PUT /api/v2/record/sort-rows/{id}` {"section", "doc_kind", "target_id", "ticked", "star"}: ROW. A section
  of "" leaves it out.
- `POST /api/v2/record/sort-rows/{id}/keep`: keeps that older copy instead of the one Hal kept
  (`sorts.keep_copy`); the ROW now kept. 400 when it is no copy or the kept one is added already.
- `POST /api/v2/record/sort/{id}/tick` {"ticked", "section"}: ticks or unticks every proposal (of one
  section); {"changed"}.
- `POST /api/v2/record/sort/{id}/accept` {"section"}: accepts the ticked proposals (of one section);
  {"accepted", "need_card", "action_id"}.
- `POST /api/v2/record/sort/{id}/again`: proposes again, keeping what was accepted; {"action_id"}.
- `DELETE /api/v2/record/sort/{id}`: forgets the sort and the files Hal kept for it; what it added stays. A
  move that can still be undone is refused (400).
- `POST /api/v2/record/move`: "Move my record to sections" (`sections.move`): a sort of kind `move` proposing
  a section for each older note; {"sort", "action_id"}.
- `POST /api/v2/record/sort/{id}/finish`: sets aside the older evidence of every note of a move nobody
  accepted; {"set_aside"}.
- `POST /api/v2/record/sort/{id}/undo`: undoes a whole move; {"removed", "restored"}.

A change that does not fit is 400 with plain words; a sort, row or card that is not there is 404; a second
sort while one runs is 409.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import events, paths
from jobhunt.evidence.routes import UPLOAD_MAX_BODY
from jobhunt.ingest import sources as ingest_sources
from jobhunt.sections import sort_actions, sort_review, sorts
from jobhunt.web.router import BadRequest, NotHere, Request, route

PREFIX = "/api/v2/record/sort"
FOLDERS_MAX = 20           # folders in one sort; a longer list is a mistake, not a person's choice
FILE_FIELD, PATH_FIELD = "file", "path"
ZIP = ".zip"


def _session(req: Request) -> str | None:
    return getattr(req.session, "id", None)


def _body(req: Request) -> dict:
    return req.body if isinstance(req.body, dict) else {}


def _inside(path: Path, folder: Path) -> bool:
    folder = folder.resolve()
    return path == folder or folder in path.parents


def checked_folder(raw: str) -> Path:
    """A folder on this computer to sort: there, and not the record or Hal's own folder; ValueError if not."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Choose a folder to sort.")
    path = Path(raw.strip()).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"{raw} is not there. Choose a folder that exists.")
    if _inside(path, Path(record._live("VAULT"))) or _inside(path, paths.data_dir()):
        raise ValueError(f"{raw} is inside your record or Hal's own folder, so Hal already has it.")
    return path


def start_folders(req: Request, folders: list, why: str) -> dict:
    """Starts a sort of folders on this computer; {"sort", "action_id"}. For this route and the older ones."""
    if req.remote:
        raise NotHere("Sort a folder on the computer Hal runs on, or upload a zip of it from this one.")
    if not isinstance(folders, list) or not folders:
        raise BadRequest("Choose one or more folders to sort.", field="folders")
    if len(folders) > FOLDERS_MAX:
        raise BadRequest(f"Choose at most {FOLDERS_MAX} folders at a time.", field="folders")
    try:
        chosen = [checked_folder(f) for f in folders]
    except ValueError as e:
        raise BadRequest(str(e), field="folders") from e
    label = ", ".join(p.name for p in chosen)
    sort_id, action_id = sort_actions.start(req.con, "folder", [str(p) for p in chosen], label,
                                            session_id=_session(req), remote=req.remote, why=why)
    return {"sort": sorts.shape(req.con, sort_id), "action_id": action_id}


def start_upload(req: Request, why: str, *, kind: str = "upload") -> dict:
    """Keeps the uploaded files and zips in Hal's data folder and starts sorting them; {"sort", "action_id",
    "added": [names], "problems": [words]}."""
    fields = _body(req)
    uploads = [(key, up) for key, up in sorted(req.files.items()) if key.startswith(FILE_FIELD)]
    if not uploads:
        raise BadRequest("Choose one or more files or a zip to sort.")
    sort_id = sorts.create(req.con, kind, [], "")
    folder = sorts.work_dir(sort_id)
    folder.mkdir(parents=True, exist_ok=True)
    names, problems = [], []
    for key, up in uploads:
        raw = str(fields.get(PATH_FIELD + key[len(FILE_FIELD):]) or "") or Path(up.name).name
        rel = ingest_sources.safe_member(raw)
        if rel is None or not rel.name or rel.name.startswith("."):
            problems.append(f"{up.name or 'A file'} has a name Hal cannot keep.")
            continue
        names.append(rel.as_posix())
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.suffix.lower() == ZIP:
            held = target.with_name(f".{target.name}")
            held.write_bytes(up.data)
            try:
                problems += ingest_sources.unpack(held, target.with_suffix(""), label=rel.name).problems()
            finally:
                held.unlink(missing_ok=True)
        else:
            target.write_bytes(up.data)
    label = names[0] if len(names) == 1 else f"{len(names)} files"
    sorts.set_roots(req.con, sort_id, [str(folder)], label)
    action_id = sort_actions.begin(req.con, sort_id, session_id=_session(req), remote=req.remote, why=why)
    return {"sort": sorts.shape(req.con, sort_id), "action_id": action_id, "added": names, "problems": problems}


@route("GET", PREFIX, auth="session_or_local")
def listing(req: Request) -> dict:
    """The latest sorts, and the cards a proposal may be for."""
    from jobhunt.sections import move

    return {"sorts": sorts.listing(req.con), "cards": sorts.cards(req.con), "can_move": move.can_move(req.con)}


@route("POST", PREFIX, auth="session_or_local", local_only=True)
def sort_folders(req: Request) -> dict:
    """Starts sorting folders on this computer."""
    return start_folders(req, _body(req).get("folders"), "You asked Hal to sort a big folder")


@route("POST", PREFIX + "/upload", auth="session_or_local", max_body=UPLOAD_MAX_BODY)
def sort_upload(req: Request) -> dict:
    """Keeps uploaded files and zips and starts sorting them."""
    return start_upload(req, "You asked Hal to sort files you uploaded")


@route("GET", PREFIX + "/{sort_id}", auth="session_or_local")
def one(req: Request) -> dict:
    """A sort with one page of its proposals: lecture material and coursework one row a module group, unless
    `flat=1` or another state is asked for."""
    q = req.query
    state = q.get("state") or "proposed"
    try:
        offset, limit = int(q.get("offset") or 0), int(q.get("limit") or sorts.PAGE)
        if state == "proposed" and q.get("flat") != "1":
            page = sort_review.rows(req.con, req.params["sort_id"], section=q.get("section") or None,
                                    group=q.get("group") or None, offset=offset, limit=limit)
        else:
            page = sorts.rows(req.con, req.params["sort_id"], section=q.get("section") or None, state=state,
                              offset=offset, limit=limit)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    return {**sorts.shape(req.con, req.params["sort_id"]), **page, "cards": sorts.cards(req.con),
            "courses": sort_review.courses_found(req.con, req.params["sort_id"]),
            "notes": sort_review.notes(req.con, req.params["sort_id"])}


@route("POST", PREFIX + "/{sort_id}/courses", auth="session_or_local")
def accept_course(req: Request) -> dict:
    """Makes the education card for a course Hal found in the sort's folders, as the person named it."""
    body = _body(req)
    if not isinstance(body.get("key"), str) or not body["key"]:
        raise BadRequest("Say which course: its key from the list.", field="key")
    try:
        out = sort_review.accept_course(req.con, req.params["sort_id"], body["key"],
                                        {k: body[k] for k in sort_review.COURSE_WORDS if k in body})
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("sort", sort_id=int(req.params["sort_id"]))
    return out


@route("POST", PREFIX + "/{sort_id}/groups", auth="session_or_local")
def change_group(req: Request) -> dict:
    """Ticks, moves or gives a card to every file of a module group at once."""
    body = _body(req)
    if not isinstance(body.get("group"), str) or not body["group"]:
        raise BadRequest("Say which group of files: its group from the list.", field="group")
    try:
        out = sort_review.change_group(req.con, req.params["sort_id"], body["group"],
                                       {k: v for k, v in body.items() if k != "group"})
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("sort", sort_id=int(req.params["sort_id"]))
    return out


@route("PUT", "/api/v2/record/sort-rows/{row_id}", auth="session_or_local")
def change_row(req: Request) -> dict:
    """Changes one proposal's section, kind, card, tick or showcase guess."""
    try:
        return sorts.change_row(req.con, req.params["row_id"], _body(req))
    except ValueError as e:
        raise BadRequest(str(e)) from e


@route("POST", "/api/v2/record/sort-rows/{row_id}/keep", auth="session_or_local")
def keep_copy(req: Request) -> dict:
    """Keeps an older copy instead of the one Hal kept."""
    try:
        return sorts.keep_copy(req.con, req.params["row_id"])
    except ValueError as e:
        raise BadRequest(str(e)) from e


@route("POST", PREFIX + "/{sort_id}/tick", auth="session_or_local")
def tick(req: Request) -> dict:
    """Ticks or unticks every proposal of a sort, or of one section."""
    body = _body(req)
    if not isinstance(body.get("ticked"), bool):
        raise BadRequest("Say whether to tick them: true or false.", field="ticked")
    try:
        return {"changed": sorts.tick(req.con, req.params["sort_id"], body["ticked"], body.get("section") or None)}
    except ValueError as e:
        raise BadRequest(str(e)) from e


@route("POST", PREFIX + "/{sort_id}/accept", auth="session_or_local")
def accept(req: Request) -> dict:
    """Accepts the ticked proposals, or one section's, and starts adding them to the record."""
    try:
        out = sorts.accept(req.con, req.params["sort_id"], _body(req).get("section") or None)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    sort_id = int(req.params["sort_id"])
    events.publish("sort", sort_id=sort_id)
    return {**out, "action_id": sort_actions.queue_accept(sort_id, session_id=_session(req), remote=req.remote)
            if out["accepted"] else None}


@route("POST", PREFIX + "/{sort_id}/again", auth="session_or_local")
def again(req: Request) -> dict:
    """Proposes again, keeping what was accepted."""
    s = sorts.row_of(req.con, req.params["sort_id"])
    return {"action_id": sort_actions.begin(req.con, s["id"], session_id=_session(req), remote=req.remote,
                                            why="You asked Hal to sort a folder again")}


@route("POST", "/api/v2/record/move", auth="session_or_local")
def move_record(req: Request) -> dict:
    """Starts "Move my record to sections": a proposal for each note an older Hal wrote."""
    from jobhunt.sections import move

    if not move.can_move(req.con):
        raise BadRequest("Your record has nothing from an older Hal to move.")
    sort_id, action_id = sort_actions.start(req.con, "move", [], "Your record from an older Hal",
                                            session_id=_session(req), remote=req.remote,
                                            why="You asked Hal to move your record to sections")
    return {"sort": sorts.shape(req.con, sort_id), "action_id": action_id}


@route("POST", PREFIX + "/{sort_id}/finish", auth="session_or_local")
def finish_move(req: Request) -> dict:
    """Sets aside what the older notes nobody accepted gave, so the record is all sections."""
    from jobhunt.sections import move

    try:
        out = {"set_aside": move.finish(req.con, req.params["sort_id"])}
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("profile", sort_id=int(req.params["sort_id"]))
    return out


@route("POST", PREFIX + "/{sort_id}/undo", auth="session_or_local")
def undo_move(req: Request) -> dict:
    """Undoes a whole move of an older record."""
    from jobhunt.sections import move

    try:
        out = move.undo(req.con, req.params["sort_id"])
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("profile", sort_id=int(req.params["sort_id"]))
    events.publish("sort", sort_id=int(req.params["sort_id"]))
    return out


@route("DELETE", PREFIX + "/{sort_id}", auth="session_or_local")
def remove(req: Request) -> dict:
    """Forgets a sort and the files Hal kept for it."""
    try:
        sorts.remove(req.con, req.params["sort_id"])
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("sort", sort_id=int(req.params["sort_id"]))
    return {"ok": True}

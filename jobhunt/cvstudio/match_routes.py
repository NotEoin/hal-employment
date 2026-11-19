"""My CV routes for "Match my file's look" and the person's own looks: match, compare, make it fit, rename, delete.

- `GET /cv/look/match` says whether the CV's own file can be matched (a PDF Hal can read) and, while the CV
  uses a personal look, compares it: the file's page pictures beside Hal's copy, and both page counts in words
  ("Your file is 1 page. Hal's copy in this look is 1 page."). Hal's count is the real printed one
  (`pagelimit.state`), so the route may print once and is marked slow.
- `POST /cv/look/match` measures the file (`match.measure`), saves the look named "Like <file name>" and
  chooses it for the CV. Matching the same file again measures it again into the same look, rather than
  making a copy of it each time.
- `POST /cv/looks/{id}/fit` is one "Make it fit" step (`measures.tighter`), offered only while Hal's copy is
  longer than the file. It is never repeated by itself: each step is the person's press.
- `PUT /cv/looks/{id}` renames, `DELETE /cv/looks/{id}` deletes (409 while a CV uses the look).
"""
from __future__ import annotations

import logging
from pathlib import PurePath
from urllib.parse import quote

from jobhunt.core import events
from jobhunt.cvstudio import match, measures, mylooks, original, pagelimit, parse, pdfread, store
from jobhunt.cvstudio.model import CVError
from jobhunt.web.router import BadRequest, Conflict, NotFound, Request, route

log = logging.getLogger(__name__)

NO_FILE = "This CV has no file of its own to match. Upload your CV as a PDF to match its look."
NOT_PDF = ("Hal can match the look of a PDF only, and {name} is {kind}. Save it as a PDF and upload that as "
           "a new version to match it.")
KIND_WORDS = {"docx": "a Word file", "md": "a text file", "txt": "a text file"}
FILE_PAGES = "Your file is {n}."
HAL_PAGES = "Hal's copy in this look is {n}."
LONGER = "Hal's copy is longer. Make it fit takes a little off the line spacing and type size."
NO_ROOM = "Hal will not make the type any smaller: this look is as tight as Hal makes it."


def _cv(req: Request) -> str | None:
    from jobhunt.cvstudio.routes import cv_of

    return cv_of(req)


def _file(doc: dict) -> tuple[dict | None, str]:
    """(the CV's own file as the page shows it, why it cannot be matched or "")."""
    file_id = doc.get("baseline_file_id")
    if not file_id:
        return None, NO_FILE
    row = store.file_row(file_id)
    shown = {"id": file_id, "name": row["name"], "pages": row["pages"], "kind": None, "page_urls": []}
    try:
        data = store.file_bytes(file_id)
    except (CVError, OSError) as e:
        return shown, str(e) if isinstance(e, CVError) else original.GONE_WORDS.format(name=row["name"])
    kind = parse.kind_of(row["name"] or "", data)
    shown["kind"] = kind
    if kind != "pdf":
        return shown, NOT_PDF.format(name=row["name"], kind=KIND_WORDS.get(kind, "not a PDF"))
    pages = row["pages"] or pdfread.pages(data)
    shown["pages"] = pages
    shown["page_urls"] = [original.PAGE_URL.format(n=n, cv=quote(doc["id"]), file=quote(file_id))
                          for n in range(1, pages + 1)]
    return shown, ""


def _pages(n: int) -> str:
    return pagelimit.pages_words(n)


def _compare(doc: dict, file: dict | None) -> dict | None:
    """The CV's personal look in use, with what was approximated and the two page counts; None for a built-in."""
    saved = mylooks.get(doc.get("look_id") or "")
    if saved is None:
        return None
    count = pagelimit.state(doc["id"])
    hal = count["pages"]
    mine = file["pages"] if file and file.get("page_urls") else None
    words = [FILE_PAGES.format(n=_pages(mine))] if mine else []
    words.append(HAL_PAGES.format(n=_pages(hal)) if hal is not None else
                 count["words"] or pagelimit.UNKNOWN)
    longer = bool(mine and hal and hal > mine)
    room = measures.tighter(saved.measures, saved.steps) is not None
    if longer:
        words.append(LONGER if room else NO_ROOM)
    return {"id": saved.id, "name": saved.name, "notes": list(saved.notes), "file_pages": mine, "hal_pages": hal,
            "longer": longer, "can_fit": longer and room, "words": " ".join(words),
            "from_this_file": bool(file and saved.file_id == file["id"])}


def _answer(doc_id: str | None) -> dict:
    doc = store.doc(doc_id=doc_id)
    file, why = _file(doc)
    return {"can": not why, "why": why, "file": file, "look": _compare(doc, file)}


@route("GET", "/api/v2/cv/look/match", slow=True)
def match_state(req: Request) -> dict:
    """Whether the CV's file can be matched, and the comparison while it uses a personal look."""
    return _answer(_cv(req))


@route("POST", "/api/v2/cv/look/match", slow=True)
def match_file(req: Request) -> dict:
    """Measures the CV's own PDF, saves the look and chooses it for the CV."""
    doc_id = _cv(req)
    doc = store.doc(doc_id=doc_id)
    file, why = _file(doc)
    if why:
        raise BadRequest(why)
    try:
        found = match.measure(store.file_bytes(file["id"]))
    except match.CannotMatch as e:
        raise BadRequest(str(e)) from e
    same = next((s for s in mylooks.all_saved() if s.file_id == file["id"]), None)
    if same is not None:
        saved = mylooks.remeasure(same.id, found.measures, found.notes)
    else:
        name = mylooks.LIKE.format(name=PurePath(file["name"] or "your file").name)
        saved = mylooks.create(name, found.measures, notes=found.notes, file_id=file["id"])
    store.set_look(saved.id, doc_id=doc["id"])
    log.info("matched the layout of CV file %s into look %s", file["id"], saved.id)
    events.publish("cv", look=saved.id, cv=doc["id"])
    return _answer(doc["id"])


@route("POST", "/api/v2/cv/looks/{look_id}/fit", slow=True)
def fit(req: Request) -> dict:
    """One "Make it fit" step on a personal look, for the CV that asked."""
    saved = mylooks.get(req.params["look_id"])
    if saved is None:
        raise NotFound("That look is not one of yours.")
    tighter = measures.tighter(saved.measures, saved.steps)
    if tighter is None:
        raise Conflict(NO_ROOM)
    mylooks.set_measures(saved.id, tighter, saved.steps + 1)
    doc_id = _cv(req)
    events.publish("cv", look=saved.id, cv=doc_id)
    return _answer(doc_id)


@route("PUT", "/api/v2/cv/looks/{look_id}")
def rename(req: Request) -> dict:
    """Renames a personal look."""
    try:
        saved = mylooks.rename(req.params["look_id"], str((req.body or {}).get("name") or ""))
    except LookupError as e:
        raise NotFound(str(e)) from e
    except CVError as e:
        raise BadRequest(str(e).split(": ", 1)[-1], field="name") from e
    events.publish("cv", look=saved.id)
    return {"id": saved.id, "name": saved.name}


@route("DELETE", "/api/v2/cv/looks/{look_id}")
def delete(req: Request) -> dict:
    """Deletes a personal look no CV uses."""
    try:
        mylooks.delete(req.params["look_id"])
    except LookupError as e:
        raise NotFound(str(e)) from e
    except mylooks.InUse as e:
        raise Conflict(str(e)) from e
    events.publish("cv", look=req.params["look_id"])
    return {"deleted": req.params["look_id"]}

"""The routes that ask Hal to write for one application: changes in your words, a new letter,
notes for the interview, who to message, and a recruiter's read of the documents.

Each only starts its action (`docs/write_actions.py`) and returns the id; the page follows it on the event
stream. An application is named by its job id or by its number, as on the other
Applications routes.
"""
from __future__ import annotations

from ..apply import actions as apply_actions
from ..apply import queries
from ..apply import routes as apply_routes
from ..apply.routes import PREFIX, _body, _who
from ..core import runner
from ..web.router import BadRequest, Request, route
from .write_actions import WORDS_SHOWN, critique_subject


def _words(req: Request, *, needed: bool) -> str:
    words = _body(req).get("words")
    if words is not None and not isinstance(words, str):
        raise BadRequest("Write what you want changed as text.", field="words")
    words = (words or "").strip()
    if needed and not words:
        raise BadRequest("Say what you want changed first.", field="words")
    return words[:WORDS_SHOWN]


def _start(req: Request, kind: str, params: dict | None = None) -> dict:
    job_id = queries.resolve(req.con, req.params["app_id"])
    return {"action_id": apply_actions.start(kind, job_id, params=params, **_who(req))}


@route("POST", PREFIX + "/{app_id}/documents/change")
def ask_for_changes(req: Request) -> dict:
    """Ask for changes: Hal rewrites the letter, message and answers in the way you said."""
    return _start(req, "docs.change", {"words": _words(req, needed=True)})


@route("POST", PREFIX + "/{app_id}/documents/letter/rewrite")
def rewrite_letter(req: Request) -> dict:
    """Rewrite the letter, with your words when you gave some."""
    words = _words(req, needed=False)
    return _start(req, "docs.rewrite_letter", {"words": words or "Rewrite the letter"})


@route("POST", PREFIX + "/{app_id}/documents/critique")
def critique(req: Request) -> dict:
    """Critique as a recruiter: grades, line comments and the top three fixes for the CV and the letter."""
    job_id = queries.resolve(req.con, req.params["app_id"])
    who = _who(req)
    return {"action_id": runner.start("docs.critique", subject=critique_subject(job_id), job_id=job_id,
                                      params={"job_id": job_id}, replace=True, priority=apply_routes.NOW,
                                      session_id=who["session_id"], helper_id=who["helper_id"],
                                      remote=who["remote"])}


@route("POST", PREFIX + "/{app_id}/prep")
def prepare_for_interview(req: Request) -> dict:
    """Prepare for the interview."""
    return _start(req, "docs.prep")


@route("POST", PREFIX + "/{app_id}/contacts")
def find_contacts(req: Request) -> dict:
    """Find who to message."""
    return _start(req, "docs.contacts")

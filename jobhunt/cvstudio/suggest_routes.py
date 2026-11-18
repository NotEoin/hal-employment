"""The Suggestions tab's API, the fresh CV interview and the Adjust for each job card.

Look again, Try another way and the fresh CV start actions and return their ids. Accept,
Edit, Undo and Leave it as it is are quick database writes and answer at once; after a decision that makes
a new version, the next review starts in the background so the next suggestion is worked out from it.
The list, Look again, Accept all and the fresh CV act on `?cv=` (the lead CV by default); a decision on one
suggestion acts on that suggestion's CV.
"""
from __future__ import annotations

import logging

from jobhunt.core import runner
from jobhunt.cvstudio import (
    fresh,
    suggest,
    suggest_actions,
    tailor,
)
from jobhunt.web.router import BadRequest, Request, json_body, route

log = logging.getLogger(__name__)
PREFIX = "/api/v2/cv"


def _cv(req: Request) -> str | None:
    from jobhunt.cvstudio.routes import cv_of

    return cv_of(req)


def _review_next(doc_id: str | None) -> str | None:
    """Starts a review of one CV unless that CV is reviewed already: the running review reads the CV's version
    in use section by section, so it sees the new version too."""
    try:
        return suggest_actions.start_review(doc_id)
    except RuntimeError as e:
        log.info("this CV's review is running already: %s", e)
        return None


@route("GET", PREFIX + "/suggestions")
def listing(req: Request) -> dict:
    """Suggestions waiting and reviewed, optionally of one kind."""
    return suggest.listing(req.con, req.query.get("kind") or None, doc_id=_cv(req))


@route("POST", PREFIX + "/suggestions/look-again")
def look_again(req: Request) -> dict:
    """Look again."""
    return {"action_id": suggest_actions.start_review(_cv(req))}


@route("POST", PREFIX + "/suggestions/accept-kind")
def accept_kind(req: Request) -> dict:
    """Accept all of a kind (Consistency, Symbols, Dates)."""
    kind = json_body(req).get("kind")
    if kind not in suggest.DETERMINISTIC_KINDS:
        raise BadRequest(f"Accept all works for {', '.join(suggest.DETERMINISTIC_KINDS)} only.", field="kind")
    doc_id = _cv(req)
    out = suggest.accept_kind(req.con, kind, doc_id=doc_id)
    if out["accepted"]:
        _review_next(doc_id)
    return out


@route("POST", PREFIX + "/suggestions/{sugg_id}/accept")
def accept(req: Request) -> dict:
    """Accept, optionally with the person's own wording."""
    body = json_body(req)
    alt, text = body.get("alt"), body.get("text")
    if alt is not None and not isinstance(alt, int):
        raise BadRequest("Say which wording to use by its number.", field="alt")
    if text is not None and not isinstance(text, str):
        raise BadRequest("Send your wording as text.", field="text")
    out = suggest.accept(req.con, req.params["sugg_id"], alt=alt, text=text)
    _review_next(suggest._row(req.con, req.params["sugg_id"])["doc_id"])
    return out


@route("POST", PREFIX + "/suggestions/{sugg_id}/leave")
def leave(req: Request) -> dict:
    """Leave it as it is, with an optional reason."""
    body = json_body(req)
    return suggest.leave(req.con, req.params["sugg_id"], reason=str(body.get("reason") or ""),
                         words=str(body.get("words") or ""), remember=bool(body.get("remember")))


@route("POST", PREFIX + "/suggestions/{sugg_id}/another")
def another(req: Request) -> dict:
    """Try another way."""
    suggest._row(req.con, req.params["sugg_id"])
    return {"action_id": runner.start("cv.another", subject=req.params["sugg_id"],
                                      params={"suggestion_id": req.params["sugg_id"]})}


@route("POST", PREFIX + "/suggestions/{sugg_id}/undo")
def undo(req: Request) -> dict:
    """Undo an accepted suggestion."""
    out = suggest.undo(req.con, req.params["sugg_id"])
    _review_next(suggest._row(req.con, req.params["sugg_id"])["doc_id"])
    return out


@route("GET", PREFIX + "/fresh/questions")
def questions(req: Request) -> list:
    """The fresh CV interview, one question at a time."""
    return list(fresh.QUESTIONS)


@route("POST", PREFIX + "/fresh")
def build_fresh(req: Request) -> dict:
    """Build a fresh CV from the interview answers."""
    body = json_body(req)
    answers, type_id = body.get("answers"), body.get("type_id")
    if not isinstance(answers, dict):
        raise BadRequest("Send the answers to the questions.", field="answers")
    if not isinstance(type_id, str) or not type_id:
        raise BadRequest("Choose a CV type first.", field="type_id")
    return {"action_id": suggest_actions.start_fresh(answers, type_id, _cv(req))}


@route("GET", PREFIX + "/adjust")
def tailor_card(req: Request) -> dict:
    """The Adjust for each job card: the level in use and, when it is forced to No, why."""
    from jobhunt.core import settings

    level, why = tailor.level_for()
    return {"level": level, "setting": settings.get("cvdocs.tailor"),
            "pick_first": settings.get("cvdocs.pick_first"), "forced_no": why}

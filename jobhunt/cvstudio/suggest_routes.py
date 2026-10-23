"""The Suggestions tab's API, the fresh CV interview and the Adjust for each job card.

Look again, Try another way and the fresh CV start actions and return their ids. Accept,
Edit, Undo and Leave it as it is are quick database writes and answer at once; after a decision that makes
a new version, the next review starts in the background so the next suggestion is worked out from it.
"""
from __future__ import annotations

import logging

from jobhunt.core import runner
from jobhunt.cvstudio import (
    fresh,
    suggest,
    suggest_actions,  # noqa: F401 - registers the cv.* kinds before a route starts one
    tailor,
)
from jobhunt.web.router import BadRequest, Request, route

log = logging.getLogger(__name__)
PREFIX = "/api/v2/cv"


def _body(req: Request) -> dict:
    if req.body is None:
        return {}
    if not isinstance(req.body, dict):
        raise BadRequest("Send a JSON object.")
    return req.body


def _review_next() -> str | None:
    """Starts a review unless one is running already; the running one sees the new version too."""
    try:
        return runner.start("cv.suggest")
    except RuntimeError as e:
        log.info("a CV review is running already: %s", e)
        return None


@route("GET", PREFIX + "/suggestions")
def listing(req: Request) -> dict:
    """Suggestions waiting and reviewed, optionally of one kind."""
    return suggest.listing(req.con, req.query.get("kind") or None)


@route("POST", PREFIX + "/suggestions/look-again")
def look_again(req: Request) -> dict:
    """Look again."""
    return {"action_id": runner.start("cv.suggest")}


@route("POST", PREFIX + "/suggestions/accept-kind")
def accept_kind(req: Request) -> dict:
    """Accept all of a kind (Consistency, Symbols, Dates)."""
    kind = _body(req).get("kind")
    if kind not in suggest.DETERMINISTIC_KINDS:
        raise BadRequest(f"Accept all works for {', '.join(suggest.DETERMINISTIC_KINDS)} only.", field="kind")
    out = suggest.accept_kind(req.con, kind)
    if out["accepted"]:
        _review_next()
    return out


@route("POST", PREFIX + "/suggestions/{sugg_id}/accept")
def accept(req: Request) -> dict:
    """Accept, optionally with the person's own wording."""
    body = _body(req)
    alt, text = body.get("alt"), body.get("text")
    if alt is not None and not isinstance(alt, int):
        raise BadRequest("Say which wording to use by its number.", field="alt")
    if text is not None and not isinstance(text, str):
        raise BadRequest("Send your wording as text.", field="text")
    out = suggest.accept(req.con, req.params["sugg_id"], alt=alt, text=text)
    _review_next()
    return out


@route("POST", PREFIX + "/suggestions/{sugg_id}/leave")
def leave(req: Request) -> dict:
    """Leave it as it is, with an optional reason."""
    body = _body(req)
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
    _review_next()
    return out


@route("GET", PREFIX + "/fresh/questions")
def questions(req: Request) -> list:
    """The fresh CV interview, one question at a time."""
    return list(fresh.QUESTIONS)


@route("POST", PREFIX + "/fresh")
def build_fresh(req: Request) -> dict:
    """Build a fresh CV from the interview answers."""
    body = _body(req)
    answers, type_id = body.get("answers"), body.get("type_id")
    if not isinstance(answers, dict):
        raise BadRequest("Send the answers to the questions.", field="answers")
    if not isinstance(type_id, str) or not type_id:
        raise BadRequest("Choose a CV type first.", field="type_id")
    return {"action_id": runner.start("cv.fresh", params={"answers": answers, "type_id": type_id})}


@route("GET", PREFIX + "/adjust")
def tailor_card(req: Request) -> dict:
    """The Adjust for each job card: the level in use and, when it is forced to No, why."""
    from jobhunt.core import settings

    level, why = tailor.level_for()
    return {"level": level, "setting": settings.get("cvdocs.tailor"),
            "pick_first": settings.get("cvdocs.pick_first"), "forced_no": why}

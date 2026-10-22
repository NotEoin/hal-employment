"""Routes for Hal's instructions to the AI: the Advanced prompt editor.

Importing this module at server start also imports `jobhunt.ai`, which registers the one-time
`ai.provider` choice as a start task.
"""
from __future__ import annotations

from ..web.router import BadRequest, NotFound, Request, route
from . import prompts


def _prompt(req: Request) -> str:
    pid = req.params["prompt_id"]
    try:
        prompts.get(pid)
    except prompts.PromptError as e:
        raise NotFound("Hal has no instruction with that name.") from e
    return pid


@route("GET", "/api/v2/prompts")
def all_prompts(req: Request) -> list:
    """Hal's instructions: [{id, name, used_for, changed, default_changed}]."""
    return prompts.listing(req.con)


@route("GET", "/api/v2/prompts/{prompt_id}")
def one(req: Request) -> dict:
    """One instruction with the person's text and Hal's original."""
    return prompts.detail(_prompt(req), req.con)


@route("PUT", "/api/v2/prompts/{prompt_id}")
def change(req: Request) -> dict:
    """Stores the person's edit; 400 in plain words when a needed placeholder is missing."""
    pid = _prompt(req)
    text = (req.body or {}).get("text") if isinstance(req.body, dict) else None
    if not isinstance(text, str):
        raise BadRequest("Write the instruction first.", field="text")
    try:
        prompts.save(pid, text, req.con)
    except prompts.PromptError as e:
        raise BadRequest(str(e), field="text") from e
    return {"changed": True}


@route("DELETE", "/api/v2/prompts/{prompt_id}")
def reset(req: Request) -> dict:
    """Puts back Hal's original."""
    prompts.reset(_prompt(req), req.con)
    return {"changed": False}


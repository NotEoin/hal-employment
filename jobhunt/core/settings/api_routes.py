"""API v2 routes for the Settings page: the schema, values, changes and resets, secrets and private answers.

Design notes:
- No reply ever carries a secret or a private answer: secrets come back as "saved", "not saved" or
  "locked", private answers as saved or not. Writes of either are accepted, never echoed.
- `restart_needed` compares each restart setting with its value when this server started, so it turns
  off again when the person changes it back.
- Unlocking the keyring shows a prompt on the computer Hal runs on, so Retry is only offered there and may
  wait for the person to type their password.
"""
from __future__ import annotations

import threading
from typing import Any

from ...web.router import BadRequest, Conflict, NotHere, Request, route
from .. import plugins, settings
from . import private, secrets

_started: dict[str, Any] | None = None
_started_lock = threading.Lock()


def _restart_values() -> dict[str, Any]:
    reg = settings._registry()
    return {k: settings.get(k) for k, s in reg.REGISTRY.items() if s.restart and s.stored}


@plugins.on_start
def remember_start_values() -> None:
    """Notes the restart settings as this server starts."""
    global _started
    with _started_lock:
        _started = _restart_values()


def _restart_needed() -> bool:
    global _started
    with _started_lock:
        if _started is None:
            _started = _restart_values()
        started = dict(_started)
    now = _restart_values()
    return any(now.get(k) != v for k, v in started.items())


def _session_id(req: Request) -> str | None:
    return getattr(req.session, "id_hash", None)


def _body_value(req: Request, name: str = "value") -> Any:
    if not isinstance(req.body, dict) or name not in req.body:
        raise BadRequest(f"Send the new {name}.")
    return req.body[name]


def _changeable(req: Request, key: str):
    item = settings.setting(key)
    if req.remote and item.remote == "local_only":
        raise NotHere("Change this on the computer Hal runs on.", field=key)
    return item


@route("GET", "/api/v2/settings/schema")
def schema(req: Request) -> dict:
    """Groups and rows for this window: developer rows only in developer mode, local rows disabled remotely."""
    return {"groups": settings.schema(developer=bool(settings.get("advanced.developer_mode")), remote=req.remote)}


@route("GET", "/api/v2/settings")
def values(req: Request) -> dict:
    """Every stored kind's value with its words, which are changed, and whether each secret is saved."""
    reg = settings._registry()
    vals = {k: settings.get(k) for k, s in sorted(reg.REGISTRY.items()) if s.stored}
    return {"values": vals, "changed": sorted(settings.changed()),
            "display": {k: settings.display(k, v) for k, v in vals.items()},
            "secrets": secrets.statuses(), "store": secrets.backend(), "restart_needed": _restart_needed()}


@route("PUT", "/api/v2/settings/{key}")
def change(req: Request) -> dict:
    """Changes one setting; 400 with the field and words when the value cannot be used."""
    key = req.params["key"]
    _changeable(req, key)
    return settings.set(key, _body_value(req), session_id=_session_id(req)).as_dict()


@route("DELETE", "/api/v2/settings/{key}")
def reset(req: Request) -> dict:
    """Puts one setting back to its default."""
    key = req.params["key"]
    _changeable(req, key)
    return settings.reset(key, session_id=_session_id(req)).as_dict()


def _secret_name(req: Request) -> str:
    name = req.params["name"]
    if name not in secrets.NAMES:
        raise LookupError("Hal keeps no key or password by that name.")
    return name


@route("PUT", "/api/v2/secrets/{name}")
def save_secret(req: Request) -> dict:
    """Saves a key or password; the reply says where, never what."""
    name = _secret_name(req)
    value = _body_value(req)
    if not isinstance(value, str):
        raise BadRequest("Type the key or password as text.", field=name)
    try:
        secrets.put(name, value)
    except ValueError as e:
        raise BadRequest(str(e), field=name) from None
    except secrets.SecretsLocked as e:
        raise Conflict(str(e), field=name) from None
    return {"status": "saved", "store": secrets.backend()}


@route("DELETE", "/api/v2/secrets/{name}")
def forget_secret(req: Request) -> dict:
    """Forgets a key or password."""
    secrets.delete(_secret_name(req))
    return {"status": "not saved"}


@route("POST", "/api/v2/secrets/retry", local_only=True, slow=True)
def retry(req: Request) -> dict:
    """Asks the person to unlock their keyring; waits for the prompt on this computer."""
    return {"locked": secrets.retry()}


@route("GET", "/api/v2/private")
def private_questions(req: Request) -> dict:
    """The private questions and whether each is saved; never an answer."""
    return {"questions": private.questions()}


@route("PUT", "/api/v2/private")
def save_private(req: Request) -> dict:
    """Saves answers (write only); an empty answer clears it."""
    answers = _body_value(req, "answers")
    if not isinstance(answers, dict):
        raise BadRequest("Send the answers by question.")
    try:
        return {"saved": private.save(answers)}
    except private.PrivateAnswerError as e:
        raise BadRequest(str(e), field=e.key) from None


@route("DELETE", "/api/v2/private/{key}")
def clear_private(req: Request) -> dict:
    """Forgets one answer."""
    try:
        private.clear(req.params["key"])
    except private.PrivateAnswerError as e:
        raise LookupError(str(e)) from None
    return {"saved": False}

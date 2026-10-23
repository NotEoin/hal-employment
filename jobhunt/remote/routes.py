"""The routes of "Use Hal from another computer": the steps, the launcher and the open windows.

Every route works from a window on another computer too, so the laptop can download its own launcher
and sign out a window it left open. The presence thread that holds back desktop pop-ups while a remote
window is open starts with the server (`sessions.start_presence`).
"""
from __future__ import annotations

from .. import db
from ..core import plugins
from ..web.router import BadRequest, BytesResponse, Request, route
from . import instructions, launcher, sessions

PREFIX = "/api/v2/remote"
MIME = {"windows": "application/x-msdos-program", "linux": "application/x-sh", "mac": "application/x-sh"}


@plugins.on_start
def _presence() -> None:
    sessions.start_presence(db.connect)


@route("GET", PREFIX + "/instructions")
def how(req: Request) -> dict:
    """Whether the SSH server is there and running, this computer's name and user, and the steps."""
    return instructions.instructions()


@route("GET", PREFIX + "/launcher")
def launcher_file(req: Request) -> BytesResponse:
    """The launcher for `os` (linux, mac or windows), filled in for this computer, as a download."""
    from ..web import auth

    os_name = (req.query.get("os") or "").strip().lower()
    if os_name not in launcher.TEMPLATES:
        raise BadRequest("Choose the other computer's system: linux, mac or windows.")
    data = launcher.render(os_name, host=launcher.this_host(), user=launcher.this_user(), port=auth.server_port(),
                           hal=launcher.hal_command())
    return BytesResponse(data, launcher.FILE_NAMES[os_name], MIME[os_name], download=True)


@route("GET", PREFIX + "/sessions")
def open_windows(req: Request) -> list[dict]:
    """The windows open from another computer, newest first."""
    return sessions.listing(req.con, req.session.id if req.session else None)


@route("DELETE", PREFIX + "/sessions/{session_id}")
def sign_out(req: Request) -> dict:
    """Signs one of those windows out."""
    sessions.sign_out(req.con, req.params["session_id"])
    return {"ok": True}

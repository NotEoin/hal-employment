"""GET /api/v2/status: the status line over every page; the words come from `core.status`."""
from __future__ import annotations

from ..web.router import Request, route
from . import runner, status


@route("GET", "/api/v2/status")
def get_status(req: Request) -> dict:
    """What Hal is doing now, or when it works next."""
    runner.flush()
    return status.status(req.con)

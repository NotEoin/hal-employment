"""GET /api/v2/status: the status line over every page; the words come from `core.status`."""
from __future__ import annotations

from jobhunt.core import runner, status
from jobhunt.web.router import Request, route


@route("GET", "/api/v2/status")
def get_status(req: Request) -> dict:
    """What Hal is doing now, or when it works next."""
    runner.flush()
    return status.status(req.con)

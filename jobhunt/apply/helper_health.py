"""The browser helper's line on "Is Hal working?": paired, in touch, and as new as Hal.

Written to the area health contract (`core.health.Check`). The fix opens the helper step of setup.
"""
from __future__ import annotations

from contextlib import closing

from .. import db, extension
from ..core.health import Check, CheckResult, Fix, ok, warn
from . import helpers

OLDER = "The browser helper is older than Hal."
OLDER_REMOTE = "The browser helper is older than Hal. Run the launcher again; it copies the new helper."
SETUP_STEP = "setup-helper"


def helper_check() -> CheckResult:
    """ok when a paired helper is in touch at Hal's version; warn otherwise, with the helper step as the fix."""
    fix = Fix("Set up the browser helper", route=SETUP_STEP)
    with closing(db.connect()) as con:
        rows = helpers.paired(con)
    if not rows:
        if extension.old_helper_seen():
            return warn("An older Hal helper is in use. Set up the new one and remove the old one.", fix)
        return warn("No browser helper is connected yet. Hal cannot fill forms in your browser.", fix)
    for r in rows:
        if extension.still_older(r["id"]):
            return warn(OLDER_REMOTE if r["remote"] else OLDER, fix)
    live = [r for r in rows if extension.connected(r["id"])]
    if not live:
        return warn("The browser helper is not in touch. Open the browser that has it.", fix)
    return ok(f"Connected, version {live[0]['version'] or helpers.wanted_version()}.")


CHECKS = [Check("helper", "Browser helper", helper_check)]

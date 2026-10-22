"""The record folder's line on "Is Hal working?": whether the folder is there and when Hal last read it.

Written to the area health contract (`core.health.Check`). A fix's `action` is a POST route
under /api/v2 and its `route` a hash route, as the core's own checks use them.
"""
from __future__ import annotations

from jobhunt import record
from jobhunt.core.health import Check, CheckResult, Fix, ok, warn


def record_folder() -> CheckResult:
    """ok when the record folder exists and has been read; warn when it is missing or never read."""
    st = record.status()
    if not st["exists"]:
        return warn("Your record folder is not there yet.", Fix("Make the folder", route="record"))
    if not st["read"] and not st["notes"]:
        return warn("Hal has not read your record yet.", Fix("Read it now", action="/record/sync"))
    return ok(f"{st['notes']} notes, read {st['read'] or st['indexed']}.")


CHECKS = [Check("record", "Your record folder", record_folder)]

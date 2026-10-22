"""The record folder's line on "Is Hal working?": whether the folder is there and when Hal last read it.

Written to the area health contract as plain data (`CHECKS`, each with an `id`, a `name` and
a `run` that returns `ok`, `level`, `text` and an optional `fix`), because the shared `Check` classes come
with the health page itself.
"""
from __future__ import annotations

from jobhunt import record


def record_folder() -> dict:
    """ok when the record folder exists; warn when it is missing or has never been read."""
    st = record.status()
    if not st["exists"]:
        return {"ok": False, "level": "warn", "text": "Your record folder is not there yet.",
                "fix": {"label": "Make the folder", "route": "record"}}
    if not st["read"] and not st["notes"]:
        return {"ok": False, "level": "warn", "text": "Hal has not read your record yet.",
                "fix": {"label": "Read it now", "action": "record.sync"}}
    return {"ok": True, "level": "ok", "text": f"{st['notes']} notes, read {st['read'] or st['indexed']}."}


CHECKS = [{"id": "record", "name": "Your record folder", "run": record_folder}]

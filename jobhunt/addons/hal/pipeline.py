"""The Pipeline note in Hal's vault, written after each email check when the Hal add-on says so."""
from __future__ import annotations

from pathlib import Path

from . import _get, enabled, vault


def write(con) -> Path | None:
    """Writes the note when `addon.enabled` and `addon.pipeline_note` are on and the vault exists."""
    folder = vault()
    if not (enabled() and _get("addon.pipeline_note") and folder):
        return None
    from jobhunt import vault as note

    return note.write_note(con, folder)

"""A copy of each application note in a folder the owner chose (`addon.applications_mirror`).

Replaces the copy into the vault's Applications folder that L pack.py:166-168 made. The documents code
calls `copy(path)` when it has written an application's `pack.md`; nothing happens unless the add-on is on
and the folder exists.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import _path_setting, enabled


def folder() -> Path | None:
    """The mirror folder, when the add-on is on and the folder exists."""
    target = _path_setting("addon.applications_mirror") if enabled() else None
    return target if target and target.is_dir() else None


def copy(note: Path, name: str | None = None) -> Path | None:
    """Copies one application note into the mirror folder; returns the copy, or None."""
    target = folder()
    if target is None or not note.is_file():
        return None
    out = target / (name or note.name)
    shutil.copyfile(note, out)
    return out

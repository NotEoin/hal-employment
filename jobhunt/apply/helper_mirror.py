"""The visible copy of the browser helper that browsers load unpacked.

The helper is loaded from `paths.helper_dir()` rather than from the code folder, because snap and flatpak
browsers on Linux cannot read hidden folders such as `~/.local`, and Windows hides AppData in its folder
picker. The copy is refreshed every time Hal starts (a start task), and the updater always restarts Hal,
so the helper still updates with git.

Design decisions, each with its reason:

- Only files whose bytes changed are written, each through a temporary file and a rename, so a browser
  reading the folder mid-refresh never sees half a file, and an unchanged copy is not touched at all.
- Files the code folder no longer has are removed, so a renamed script cannot linger and load.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..core import paths, plugins

log = logging.getLogger(__name__)

TEMP_SUFFIX = ".hal-new"


def source() -> Path:
    """The helper's files in this checkout."""
    return paths.code_dir() / "extension"


def _files(folder: Path) -> dict[Path, Path]:
    return {p.relative_to(folder): p for p in folder.rglob("*") if p.is_file()}


def refresh(src: Path | None = None, dest: Path | None = None) -> dict:
    """Makes `dest` (the visible helper folder) an exact copy of `src`; returns what changed."""
    src = src or source()
    dest = dest or paths.helper_dir()
    if not (src / "manifest.json").is_file():
        raise FileNotFoundError(f"the browser helper's files are missing from {src}")
    dest.mkdir(parents=True, exist_ok=True)
    wanted, there = _files(src), _files(dest)
    written, removed = [], []
    for rel, path in sorted(wanted.items()):
        data = path.read_bytes()
        target = dest / rel
        if rel in there and there[rel].read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + TEMP_SUFFIX)
        temp.write_bytes(data)
        os.replace(temp, target)
        written.append(rel.as_posix())
    for rel in sorted(set(there) - set(wanted)):
        there[rel].unlink()
        removed.append(rel.as_posix())
    if written or removed:
        log.info("refreshed the browser helper folder %s: %d written, %d removed", dest, len(written), len(removed))
    return {"folder": str(dest), "written": written, "removed": removed}


@plugins.on_start
def refresh_at_start() -> None:
    """Start task: refreshes the visible helper folder."""
    refresh()

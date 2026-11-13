"""The board list: where `boards.toml` lives and what it holds.

The import copies the checkout's `boards.toml` into the data folder, so the copy there wins once it exists.
"""
from __future__ import annotations

import tomllib

from ..core import paths

ROOT = paths.code_dir()
BOARDS_PATH = ROOT / "boards.toml"


def boards_path():
    """The board list's file: the import's copy in the data folder, else the checkout's (or a test's) file.

    The import copies the checkout's `boards.toml` to `<data>/import/boards.toml`, and the copy
    in the checkout is deleted once the import has run, so the imported copy wins once it exists.
    """
    if BOARDS_PATH == ROOT / "boards.toml" and not paths.is_legacy_layout():
        imported = paths.data_dir() / "import" / "boards.toml"
        if imported.exists():
            return imported
    return BOARDS_PATH


def boards() -> list[dict]:
    """The board list (until the sources registry replaces it); [] when there is none."""
    path = boards_path()
    if not path.exists():
        return []
    return tomllib.loads(path.read_text()).get("board", [])

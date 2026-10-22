"""Loads config.toml and the board list in boards.toml from the project root, and the private answers.

Answers about the applicant's health (disability, adjustments) live in private.toml in the data folder
(`core.paths.private_path()`), outside the code, so they never enter the project's history. It is read
afresh on each call, so an edit takes effect without a restart. `PRIVATE_PATH` is resolved when used.
"""
import tomllib

from .core import paths

ROOT = paths.code_dir()
CONFIG_PATH = ROOT / "config.toml"
BOARDS_PATH = ROOT / "boards.toml"


def __getattr__(name):  # old path constant, read live (PEP 562)
    if name == "PRIVATE_PATH":
        return paths.private_path()
    raise AttributeError(name)

CONFIG = tomllib.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def boards() -> list[dict]:
    if not BOARDS_PATH.exists():
        return []
    return tomllib.loads(BOARDS_PATH.read_text()).get("board", [])


def private() -> dict:
    """The [applicant] answers in the data folder's private.toml, or {} when there is none."""
    path = globals().get("PRIVATE_PATH") or paths.private_path()
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text()).get("applicant", {})

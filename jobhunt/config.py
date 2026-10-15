"""Loads config.toml and the board list in boards.toml from the project root, and the private answers.

Answers about the applicant's health (disability, adjustments) live in data/private.toml, which
git ignores, so they never enter the project's history. It is read afresh on each call, so an
edit takes effect without a restart.
"""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
BOARDS_PATH = ROOT / "boards.toml"
PRIVATE_PATH = ROOT / "data" / "private.toml"

CONFIG = tomllib.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def boards() -> list[dict]:
    if not BOARDS_PATH.exists():
        return []
    return tomllib.loads(BOARDS_PATH.read_text()).get("board", [])


def private() -> dict:
    """The [applicant] answers in data/private.toml, or {} when there is none."""
    if not PRIVATE_PATH.exists():
        return {}
    return tomllib.loads(PRIVATE_PATH.read_text()).get("applicant", {})

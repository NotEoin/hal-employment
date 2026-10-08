"""Loads config.toml and the board list in boards.toml from the project root."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
BOARDS_PATH = ROOT / "boards.toml"

CONFIG = tomllib.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def boards() -> list[dict]:
    if not BOARDS_PATH.exists():
        return []
    return tomllib.loads(BOARDS_PATH.read_text()).get("board", [])

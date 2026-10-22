"""Stub of the settings registry holding only the keys the server reads.

The real registry declares every key in `core/settings/catalog/*.py`; this one keeps the same
names and fields, so `core.settings` runs against it unchanged.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Setting:
    """One setting, with the registry's fields."""

    key: str
    kind: str
    default: Any
    label: str
    help: str = ""
    group: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    min: float | None = None
    max: float | None = None
    unit: str = ""
    restart: bool = False
    rescore: bool = False
    goal: bool = False
    advanced: bool = False
    dev_only: bool = False
    machine: bool = False
    remote: str = "ok"
    platforms: tuple[str, ...] = ()
    visible_if: str = ""
    order: int = 0
    default_fn: Callable[[Callable[[str], Any]], Any] | None = None
    parse: Callable[[Any], Any] | None = None
    validate: Callable[[Any], str | None] | None = None
    show: Callable[[Any], str] | None = None
    secret_name: str = ""
    to: str = ""
    action: str = ""


class SettingError(ValueError):
    """A value the user gave cannot be used."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key


GROUPS = [("you", "You", "user"), ("cvdocs", "CV and documents", "file"), ("advanced", "Advanced", "gear")]
REGISTRY: dict[str, Setting] = {}


def register(*items: Setting) -> None:
    """Adds settings; a duplicate key raises."""
    for item in items:
        if item.key in REGISTRY:
            raise ValueError(f"setting {item.key} registered twice")
        REGISTRY[item.key] = item


def _developer(get) -> bool:
    from jobhunt.core import paths

    return paths.is_developer_checkout()


def load_all() -> None:
    """Registers the stub's keys once."""
    if REGISTRY:
        return
    register(
        Setting("you.name", "text", "", "Your name"),
        Setting("you.preferred_name", "text", "", "What Hal calls you"),
        Setting("cvdocs.file_name", "text", "{name} CV.pdf", "File names employers see"),
        Setting("cvdocs.letter_file_name", "text", "{name} Cover Letter.pdf", "Cover letter file name"),
        Setting("cvdocs.folder", "path", "", "Where documents are saved"),
        Setting("advanced.port", "int", 8737, "Port", min=1024, max=65535, restart=True),
        Setting("advanced.classic", "bool", False, "Show the classic app"),
        Setting("advanced.developer_mode", "bool", False, "Developer mode", default_fn=_developer),
        Setting("advanced.allow_old_helper", "bool", False, "Accept the old browser helper"),
        Setting("advanced.simple_menu", "bool", True, "Simple menu: 5 items and More"),
        Setting("advanced.theme", "choice", "system", "Light or dark",
                choices=(("system", "Follow this computer"), ("light", "Light"), ("dark", "Dark"))),
    )


def visible(setting: Setting, get, *, developer: bool, platform: str) -> bool:
    """The stub shows every row."""
    return True

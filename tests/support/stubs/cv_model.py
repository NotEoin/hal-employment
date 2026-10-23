"""Stand-in for `jobhunt.cvstudio.model`, for the CV type tests while the real model is missing.

Only the dataclasses and the JSON round trip are here: the type code reads documents and builds changed copies with
`dataclasses.replace`, so nothing else of the contract is needed. `install()` puts this module in place
only when the real one cannot be imported, so with the real model the tests run against it.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = 2
REAL = "jobhunt.cvstudio.model"


@dataclass
class Bullet:
    """One line of an entry."""

    id: str
    text: str
    sources: list[str] = field(default_factory=list)
    locked: bool = False


@dataclass
class Module:
    """One module of a qualification, with its mark as written."""

    name: str
    mark: str = ""
    year: str = ""


@dataclass
class Entry:
    """A job, qualification, project or group."""

    id: str
    heading: str = ""
    org: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    grade: str = ""
    meta: str = ""
    link: str = ""
    style: str = "bullets"
    text: str = ""
    bullets: list[Bullet] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    show_marks_from: int | None = None
    items: list[str] = field(default_factory=list)
    hidden: bool = False
    locked: bool = False


@dataclass
class Section:
    """One section of the CV."""

    id: str
    kind: str
    title: str
    text: str = ""
    entries: list[Entry] = field(default_factory=list)
    also: str = ""
    visible: bool = True
    locked: bool = False
    max_entries: int | None = None


@dataclass
class CVDoc:
    """A whole CV in schema 2."""

    schema: int = SCHEMA
    headline: str = ""
    contact_snapshot: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    closing: str = ""
    locale: str = "en-GB"
    extras: dict = field(default_factory=dict)


def to_json(doc: CVDoc) -> str:
    """Canonical JSON with sorted keys."""
    return json.dumps(asdict(doc), sort_keys=True)


def _entry(d: dict) -> Entry:
    return Entry(**{**d, "bullets": [Bullet(**b) for b in d.get("bullets", [])],
                    "modules": [Module(**m) for m in d.get("modules", [])]})


def from_json(text: str) -> CVDoc:
    """The document `to_json` wrote."""
    d: dict[str, Any] = json.loads(text)
    sections = [Section(**{**s, "entries": [_entry(e) for e in s.get("entries", [])]}) for s in d.get("sections", [])]
    return CVDoc(**{**d, "sections": sections})


def install() -> types.ModuleType:
    """The real model when it exists, else this stub registered under its name."""
    try:
        return importlib.import_module(REAL)
    except ModuleNotFoundError:
        mod = sys.modules[__name__]
        sys.modules[REAL] = mod
        import jobhunt.cvstudio as pkg

        pkg.model = mod
        return mod

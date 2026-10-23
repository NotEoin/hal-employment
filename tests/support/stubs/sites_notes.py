"""A stand-in for `jobhunt.sites.notes`, for the saved-answer tests.

`installed()` yields the module the tests add notes through. When the real module is importable it is
yielded as it is, so the tests run on the real notes store; until then a minimal module with the contract's
`Note`, `NoteSet`, `for_page`, `add`, `used` and `site_key` is put in `sys.modules` for the length of the
block. The stand-in keeps notes in memory and ranks them job, tenant, site, application system, then
global, as the contract's "most specific note wins" asks.
"""
from __future__ import annotations

import contextlib
import importlib
import itertools
import re
import sys
import types as pytypes
from collections.abc import Iterator
from dataclasses import dataclass, field
from unittest import mock
from urllib.parse import urlparse

NAME = "jobhunt.sites.notes"
RANK = {"job": 0, "tenant": 1, "site": 2, "ats": 3, "global": 4}


@dataclass(frozen=True)
class Note:
    """One note, as the contract's `Note`."""

    id: str
    kind: str
    scope_kind: str
    scope_key: str
    cond: dict
    act: dict
    text: str
    origin: str


@dataclass
class NoteSet:
    """The notes for one page, most specific first."""

    notes: list[Note] = field(default_factory=list)

    def all(self, kind: str, **cond) -> list[Note]:
        """Every note of `kind`."""
        return [n for n in self.notes if n.kind == kind]

    def first(self, kind: str, **cond) -> Note | None:
        """The most specific note of `kind`, or None."""
        found = self.all(kind, **cond)
        return found[0] if found else None

    def for_field(self, label: str) -> list[Note]:
        """The notes whose label pattern matches `label`."""
        return [n for n in self.notes if n.cond.get("label") and re.search(n.cond["label"], label or "", re.I)]

    def wire(self) -> list[dict]:
        """The notes as plain dicts."""
        return [vars(n) for n in self.notes]


def site_key(url: str) -> str:
    """The host without "www.", which is enough for the made-up sites of these tests."""
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _stand_in() -> pytypes.ModuleType:
    module = pytypes.ModuleType(NAME)
    kept: list[Note] = []
    counter = itertools.count(1)
    module.Note, module.NoteSet, module.site_key, module.kept = Note, NoteSet, site_key, kept

    def add(scope_kind: str, scope_key: str, kind: str, cond: dict, act: dict, text: str, *, said: str | None = None,
            origin: str = "you") -> str:
        note = Note(f"db:{next(counter)}", kind, scope_kind, scope_key, dict(cond), dict(act), text, origin)
        kept.append(note)
        return note.id

    def for_page(url: str, *, ats: str | None = None, job_id: str | None = None, step: int | None = None) -> NoteSet:
        site = site_key(url)

        def applies(n: Note) -> bool:
            return (n.scope_kind == "global" or (n.scope_kind in ("site", "tenant") and n.scope_key == site)
                    or (n.scope_kind == "ats" and n.scope_key == ats) or (n.scope_kind == "job" and
                                                                           (url or "").startswith(n.scope_key)))

        return NoteSet(sorted((n for n in kept if applies(n)), key=lambda n: RANK[n.scope_kind]))

    module.add, module.for_page = add, for_page
    module.used = lambda note_id, job_id, ok=None, detail="": None
    return module


def _real() -> pytypes.ModuleType | None:
    try:
        module = importlib.import_module(NAME)
    except ImportError:
        return None
    return module if hasattr(module, "for_page") else None


@contextlib.contextmanager
def installed() -> Iterator[pytypes.ModuleType]:
    """The notes module for the length of the block: the real one when it is there, else the stand-in."""
    real = _real()
    if real is not None:
        yield real
        return
    import jobhunt.sites as package

    module = _stand_in()
    # Only this name is put in and taken out: `mock.patch.dict(sys.modules)` would also drop every module
    # first imported inside the block.
    sys.modules[NAME] = module
    try:
        with mock.patch.object(package, "notes", module, create=True):
            yield module
    finally:
        sys.modules.pop(NAME, None)

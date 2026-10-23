"""Stand-ins for the CV store's `doc()` and the CV types' `load`, coded to their contract.

`using(type_id)` makes Hal's copy of the CV use that type for the length of a block. When the real
`jobhunt.cvstudio.store` and `jobhunt.cvstudio.types` are installed, only `store.doc()` is patched to name the
type, so the tests run on the real built-in types; until then minimal modules holding the three types the
made-up people of `support.people` use are put in `sys.modules`. This file can go once
the CV store and types exist, and the tests then set the type through the store.

The `[documents]` tables follow the humanities type; the wording of the technical and career-changer tables is
this file's own.
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import types as pytypes
from collections.abc import Iterator
from dataclasses import dataclass, field
from unittest import mock


@dataclass(frozen=True)
class CVType:
    """The fields of `cvstudio.types.CVType` the document prompts read."""

    id: str
    name: str
    fits: str = ""
    note: str = ""
    pages: int = 1
    section_order: tuple[str, ...] = ()
    sections: dict = field(default_factory=dict)
    writing: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    keywords: dict = field(default_factory=dict)
    documents: dict = field(default_factory=dict)
    prompts: dict = field(default_factory=dict)


TYPES = {
    "humanities_grad": CVType(
        id="humanities_grad", name="Humanities and social science graduate",
        writing={"tone": "plain", "spelling": "en-GB",
                 "banned": ["passionate", "hard-working", "team player", "go-getter"]},
        documents={"evidence": ["dissertation", "strong modules", "volunteering", "part-time work"],
                   "letter_mentions": "the dissertation and the most relevant volunteering or work",
                   "hiring_titles": ["hiring manager", "head of research", "policy manager", "team leader"],
                   "prep_topics": ["why this sector", "a written exercise", "competency questions"],
                   "special_cases": []}),
    "technical_grad": CVType(
        id="technical_grad", name="Technical graduate",
        writing={"tone": "plain", "spelling": "en-GB", "banned": ["rockstar", "ninja"]},
        documents={"evidence": ["projects", "modules from 70", "internships", "part-time work"],
                   "letter_mentions": "the most relevant projects and modules, by their names",
                   "hiring_titles": ["engineering manager", "head of engineering", "CTO", "team lead"],
                   "prep_topics": ["the tools the advert names", "coding practice", "system design basics"],
                   "special_cases": ["ktp"]}),
    "career_changer": CVType(
        id="career_changer", name="Career changer",
        writing={"tone": "confident", "spelling": "en-GB", "banned": ["career break"]},
        documents={"evidence": ["skills from earlier jobs, each with an example", "training", "results at work"],
                   "letter_mentions": "why the change, and the earlier work that carries over",
                   "hiring_titles": ["hiring manager", "operations manager", "programme manager"],
                   "prep_topics": ["why you are changing career", "how your earlier work carries over",
                                   "competency questions"],
                   "special_cases": []}),
}


def _real(name: str) -> pytypes.ModuleType | None:
    try:
        return importlib.import_module(f"jobhunt.cvstudio.{name}")
    except ImportError:
        return None


def _stand_in_modules(type_id: str) -> dict[str, pytypes.ModuleType]:
    store = pytypes.ModuleType("jobhunt.cvstudio.store")
    store.doc = lambda: {"id": "main", "type_id": type_id, "look_id": "classic"}
    kinds = pytypes.ModuleType("jobhunt.cvstudio.types")
    kinds.CVType = CVType

    def load(wanted: str) -> CVType:
        if wanted not in TYPES:
            raise LookupError(f"There is no CV type {wanted!r}.")
        return TYPES[wanted]

    kinds.load = load
    kinds.all_types = lambda: list(TYPES.values())
    return {"jobhunt.cvstudio.store": store, "jobhunt.cvstudio.types": kinds}


@contextlib.contextmanager
def using(type_id: str) -> Iterator[None]:
    """Hal's copy of the CV has CV type `type_id` for the length of the block."""
    import jobhunt.cvstudio as package

    store, kinds = _real("store"), _real("types")
    if store is not None and kinds is not None:
        real_doc = store.doc
        with mock.patch.object(store, "doc", lambda: {**real_doc(), "type_id": type_id}):
            yield
        return
    modules = _stand_in_modules(type_id)
    # Only these two names are put in and taken out again: `mock.patch.dict(sys.modules)` would also drop
    # every module first imported inside the block, and importing one twice registers its routes twice.
    sys.modules.update(modules)
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(package, "store", modules["jobhunt.cvstudio.store"], create=True))
            stack.enter_context(mock.patch.object(package, "types", modules["jobhunt.cvstudio.types"], create=True))
            yield
    finally:
        for name in modules:
            sys.modules.pop(name, None)

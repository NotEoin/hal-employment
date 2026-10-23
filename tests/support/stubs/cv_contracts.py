"""Stand-ins for the CV modules the printing code calls: `cvstudio.model`, `cvstudio.store` and
`cvstudio.types`, with the contract's names.

`install()` puts each stand-in in `sys.modules` only when the real module is missing, so the same tests run
on the real modules once they exist. The stand-ins
hold only what printing calls: the schema 2 dataclasses, `plain_text`, `to_v1`, the store's `doc`, `current`,
`get`, `new_version` and `layout`, and `types.load` for two made-up built-in types.
"""
from __future__ import annotations

import importlib
import importlib.util
import itertools
import sys
import types as pytypes
from dataclasses import dataclass, field
from typing import Any


def _real(name: str) -> bool:
    """True when the real module exists in the checkout."""
    if name in sys.modules:
        return not getattr(sys.modules[name], "STAND_IN", False)
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


# ---------------------------------------------------------------------------------------------- model

@dataclass
class Bullet:
    id: str
    text: str
    sources: list[str] = field(default_factory=list)
    locked: bool = False


@dataclass
class Module:
    name: str
    mark: str = ""
    year: str = ""


@dataclass
class Entry:
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
    schema: int = 2
    headline: str = ""
    contact_snapshot: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    closing: str = ""
    locale: str = "en-GB"
    extras: dict = field(default_factory=dict)


def plain_text(doc: CVDoc, profile: dict) -> str:
    """The CV as plain text, name first."""
    lines = [profile.get("name", ""), doc.headline]
    for s in doc.sections:
        if not s.visible:
            continue
        lines += ["", s.title.upper(), s.text]
        for e in s.entries:
            if e.hidden:
                continue
            lines.append(", ".join(x for x in (e.heading, e.org) if x))
            lines += [f"- {b.text}" for b in e.bullets] + [e.text, ", ".join(e.items)]
    return "\n".join(x for x in lines if x is not None).strip() + "\n"


def to_v1(doc: CVDoc, profile: dict) -> dict:
    """A small version of today's cv.json shape."""
    out: dict[str, Any] = {"title": doc.headline, "summary": "", "education": [], "experience": [], "skills": []}
    for s in doc.sections:
        if s.kind == "summary":
            out["summary"] = s.text
        for e in s.entries:
            body = "\n".join(f"- {b.text}" for b in e.bullets) or e.text
            if s.kind == "education":
                out["education"].append({"qualification": e.heading, "meta": f"{e.org} | {e.start} - {e.end}",
                                         "grade": e.grade, "detail": body})
            elif s.kind in ("experience", "volunteering"):
                out["experience"].append({"role": e.heading, "org": e.org, "dates": f"{e.start} - {e.end}",
                                          "body": body})
            elif s.kind == "skills":
                out["skills"].append([e.heading, list(e.items)])
    return out


# ---------------------------------------------------------------------------------------------- types

@dataclass(frozen=True)
class CVType:
    id: str
    name: str
    fits: str
    note: str
    pages: int
    section_order: tuple[str, ...]
    sections: dict
    writing: dict
    checks: dict
    keywords: dict
    documents: dict
    prompts: dict


BUILT_IN = {
    "humanities_grad": CVType(
        "humanities_grad", "Humanities and social science graduate", "Recent graduates in history and similar",
        "Shows the dissertation and strong modules.", 2,
        ("summary", "education", "volunteering", "experience", "skills", "interests"),
        {"summary": {"title": "Profile"}, "education": {"title": "Education", "max_entries": 2,
                                                         "show_modules": "strong", "min_mark": 65},
         "experience": {"title": "Work experience", "max_entries": 4}, "skills": {"title": "Skills"},
         "volunteering": {"title": "Volunteering"}, "interests": {"title": "Interests"}},
        {"style": "bullets"}, {}, {}, {}, {}),
    "technical_grad": CVType(
        "technical_grad", "Technical graduate", "Computing, engineering and science graduates",
        "Projects with tools and results.", 1,
        ("summary", "education", "projects", "experience", "skills"),
        {"summary": {"title": "Profile"}, "education": {"title": "Education", "show_modules": "strong",
                                                         "min_mark": 70},
         "projects": {"title": "Projects"}, "experience": {"title": "Experience"}, "skills": {"title": "Skills"}},
        {"style": "bullets"}, {}, {}, {}, {}),
}


def load(type_id: str) -> CVType:
    """A made-up built-in type; KeyError names an unknown one."""
    return BUILT_IN[type_id]


def all_types() -> list[CVType]:
    """Both made-up types."""
    return list(BUILT_IN.values())


# ---------------------------------------------------------------------------------------------- store

class _Store:
    """One CV document in memory."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.current: str | None = None
        self.versions: dict[str, tuple[int, CVDoc]] = {}
        self.counter = itertools.count(1)


_STORE = _Store()


def doc() -> dict:
    """The cv_documents row, made on first use as the real store does; versions stay in memory."""
    from jobhunt import db
    from jobhunt.core import clock

    con = db.connect()
    try:
        row = con.execute("SELECT * FROM cv_documents WHERE id = 'main'").fetchone()
        if row is None:
            now = clock.iso()
            con.execute("INSERT INTO cv_documents (id, route, send, type_id, look_id, layout, created, updated) "
                        "VALUES ('main', 'improve', 'hal_copy', 'humanities_grad', 'classic', '{}', ?, ?)",
                        (now, now))
            con.commit()
            row = con.execute("SELECT * FROM cv_documents WHERE id = 'main'").fetchone()
        out = dict(row)
    finally:
        con.close()
    out["current_version_id"] = _STORE.current
    return out


def current() -> tuple[str, CVDoc]:
    """(version id, document) of Hal's copy."""
    vid = _STORE.current
    if vid is None:
        raise LookupError("There is no CV yet.")
    return vid, _STORE.versions[vid][1]


def get(version_id: str) -> CVDoc:
    """One version; LookupError when there is none."""
    if version_id not in _STORE.versions:
        raise LookupError("There is no such version of your CV.")
    return _STORE.versions[version_id][1]


def new_version(d: CVDoc, *, origin: str, what: str, by: str, parent: str | None = None, job_id: str | None = None,
                type_id: str | None = None, file_id: str | None = None) -> str:
    """Stores a version and makes it current."""
    n = next(_STORE.counter)
    vid = f"v{n}"
    _STORE.versions[vid] = (n, d)
    _STORE.current = vid
    return vid


def layout() -> dict:
    """No sections set by the person."""
    return {"sections": [], "locked_lines": []}


def reset() -> None:
    """Empties the store between tests."""
    _STORE.reset()


def _module(name: str, attrs: dict[str, Any]) -> None:
    mod = pytypes.ModuleType(name)
    mod.__dict__.update(attrs)
    mod.STAND_IN = True
    sys.modules[name] = mod
    parent, _, child = name.rpartition(".")
    setattr(importlib.import_module(parent), child, mod)


def install() -> None:
    """Registers each stand-in whose real module is missing."""
    if not _real("jobhunt.cvstudio.model"):
        _module("jobhunt.cvstudio.model", {"SCHEMA": 2, "Bullet": Bullet, "Module": Module, "Entry": Entry,
                                           "Section": Section, "CVDoc": CVDoc, "plain_text": plain_text,
                                           "to_v1": to_v1})
    if not _real("jobhunt.cvstudio.types"):
        _module("jobhunt.cvstudio.types", {"CVType": CVType, "load": load, "all_types": all_types})
    if not _real("jobhunt.cvstudio.store"):
        _module("jobhunt.cvstudio.store", {"doc": doc, "current": current, "get": get,
                                           "new_version": new_version, "layout": layout, "reset": reset})

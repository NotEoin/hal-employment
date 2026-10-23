"""Stand-in for `jobhunt.cvstudio.model`, installed only while the real module is missing.

It keeps to the contract's names and fields. `apply` supports the targets the suggestions use, and these are
the semantics the real model must have too:

- `replace` on `.../b:<id>`, `.../text`, `.../title`, `.../heading` or an entry field such as `.../start`
  sets that text;
- `reorder` on `cv` takes section ids, on `s:<id>` entry ids, and on `s:<id>/e:<id>` the entry's items;
- `hide` and `show` on a section or an entry set `visible` or `hidden`;
- `insert` on an entry adds `Bullet(value)` at `position`; `delete` on a bullet removes it.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = 2
ENTRY_FIELDS = ("heading", "org", "location", "start", "end", "grade", "meta", "link", "text")


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
    schema: int = SCHEMA
    headline: str = ""
    contact_snapshot: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    closing: str = ""
    locale: str = "en-GB"
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Change:
    op: str
    target: str
    value: Any = None
    position: int | None = None


class CVError(ValueError):
    """A target or field that is not in the document."""


def to_json(doc: CVDoc) -> str:
    return json.dumps(asdict(doc), sort_keys=True, ensure_ascii=True)


def from_json(text: str) -> CVDoc:
    raw = json.loads(text)
    sections = []
    for s in raw.get("sections", []):
        entries = []
        for e in s.get("entries", []):
            e = dict(e)
            e["bullets"] = [Bullet(**b) for b in e.get("bullets", [])]
            e["modules"] = [Module(**m) for m in e.get("modules", [])]
            entries.append(Entry(**e))
        sections.append(Section(**{**s, "entries": entries}))
    return CVDoc(**{**raw, "sections": sections})


def validate(doc: CVDoc, type_=None) -> list[str]:
    out = []
    ids = [s.id for s in doc.sections]
    if len(ids) != len(set(ids)):
        out.append("Two sections have the same id.")
    for s in doc.sections:
        for e in s.entries:
            if not (e.heading or e.items or e.text or e.bullets):
                out.append(f"An entry in {s.title} is empty.")
    return out


def _parts(target: str) -> list[tuple[str, str]]:
    out = []
    for part in target.split("/"):
        key, _, val = part.partition(":")
        out.append((key, val) if val else ("field", key))
    return out


def find(doc: CVDoc, target: str) -> object:
    if target == "cv":
        return doc
    node: Any = doc
    for key, val in _parts(target):
        if key == "s":
            node = next((s for s in node.sections if s.id == val), None)
        elif key == "e":
            node = next((e for e in node.entries if e.id == val), None)
        elif key == "b":
            node = next((b for b in node.bullets if b.id == val), None)
        elif key == "field":
            if not hasattr(node, val):
                raise CVError(f"{target}: no field {val}")
            node = getattr(node, val)
        if node is None:
            raise CVError(f"{target}: not in the CV")
    return node


def _owner(doc: CVDoc, target: str) -> tuple[Any, str]:
    head, _, last = target.rpartition("/")
    if ":" in last:
        return find(doc, target), "text"
    return find(doc, head), last


def _by_ids(items: list, ids: list[str], key) -> list:
    known = {key(x): x for x in items}
    if sorted(known) != sorted(ids):
        raise CVError("the new order must name every part once")
    return [known[i] for i in ids]


def apply(doc: CVDoc, change: Change) -> CVDoc:
    new = copy.deepcopy(doc)
    op, target = change.op, change.target
    if op == "replace" or op == "retitle":
        node, attr = _owner(new, target)
        if not hasattr(node, attr):
            raise CVError(f"{target}: cannot be replaced")
        setattr(node, attr, change.value)
    elif op == "reorder":
        node = find(new, target)
        if isinstance(node, CVDoc):
            node.sections = _by_ids(node.sections, list(change.value), lambda s: s.id)
        elif isinstance(node, Section):
            node.entries = _by_ids(node.entries, list(change.value), lambda e: e.id)
        elif isinstance(node, Entry):
            if sorted(node.items) != sorted(change.value):
                raise CVError("the new order must name every item once")
            node.items = list(change.value)
        else:
            raise CVError(f"{target}: cannot be reordered")
    elif op in ("hide", "show"):
        node = find(new, target)
        if isinstance(node, Section):
            node.visible = op == "show"
        elif isinstance(node, Entry):
            node.hidden = op == "hide"
        else:
            raise CVError(f"{target}: cannot be hidden")
    elif op == "insert":
        node = find(new, target)
        if not isinstance(node, Entry):
            raise CVError(f"{target}: a line goes into an entry")
        bullet = change.value if isinstance(change.value, Bullet) else Bullet(**change.value)
        node.bullets.insert(len(node.bullets) if change.position is None else change.position, bullet)
    elif op == "delete":
        head, _, last = target.rpartition("/")
        parent = find(new, head)
        parent.bullets = [b for b in parent.bullets if f"b:{b.id}" != last]
    else:
        raise CVError(f"unknown change {op}")
    return new


def diff(a: CVDoc, b: CVDoc) -> list[dict]:
    out = []
    old = {s.id: s for s in a.sections}
    for s in b.sections:
        before = old.get(s.id)
        if before is not None and before.text != s.text:
            out.append({"where": s.title, "kind": "Reworded", "before": before.text, "after": s.text,
                        "target": f"s:{s.id}/text"})
    return out


def plain_text(doc: CVDoc, profile: dict) -> str:
    lines = [profile.get("name", ""), doc.headline]
    for s in doc.sections:
        if not s.visible:
            continue
        lines += [s.title, s.text]
        for e in s.entries:
            if e.hidden:
                continue
            lines += [" ".join(x for x in (e.heading, e.org, e.start, e.end, e.grade, e.meta) if x), e.text]
            lines += [b.text for b in e.bullets] + [", ".join(e.items)]
            lines += [f"{m.name} {m.mark}".strip() for m in e.modules]
    return "\n".join(x for x in lines if x)


def to_v1(doc: CVDoc, profile: dict) -> dict:
    return {"title": doc.headline, "sections": [s.title for s in doc.sections]}

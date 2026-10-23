"""The CV as Hal keeps it: schema 2, with a stable id on every section, entry and line.

- A document is plain dataclasses, written as canonical JSON (sorted keys) so the same CV always gives the
  same bytes, and so a version can be compared or hashed.
- Ids never change once given. An entry's first id comes from its kind and its original heading, so the
  import of today's `cv.json` gives the same ids in every version it makes; a part added later gets
  a fresh random id.
- `apply` and `diff` work by id, never by position or name, so renaming an entry keeps its edits.
- The name and contact block are never part of the CV body: they come from the profile (`you.*`), so a new
  phone number reaches every document at once. `contact_snapshot` only keeps what an upload or import said.
- `to_v1` and `from_v1` translate to and from today's `cv.json` shape, for the callers not converted yet
  (pack, cvpdf, cvscore) and for the "website cv.json" export. Fields of `cv.json` that schema 2
  has no place for are kept in `extras`, so nothing is lost on the way through.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal, get_args

SCHEMA = 2
SectionKind = Literal["summary", "education", "experience", "volunteering", "projects", "research", "skills",
                      "achievements", "publications", "certifications", "languages", "interests", "teaching",
                      "talks", "portfolio", "additional", "custom"]
KINDS: tuple[str, ...] = get_args(SectionKind)
STYLES = ("bullets", "paragraph")
OPS = ("replace", "insert", "delete", "move", "hide", "show", "reorder", "retitle")
# The id prefix of an entry in each kind of section: "ed-1a2b3c4d" is an education entry.
PREFIX = {"summary": "su", "education": "ed", "experience": "ex", "volunteering": "vo", "projects": "pr",
          "research": "re", "skills": "sk", "achievements": "ac", "publications": "pu", "certifications": "ce",
          "languages": "la", "interests": "in", "teaching": "te", "talks": "ta", "portfolio": "po",
          "additional": "ad", "custom": "cu"}
ID_HEX = 8                     # characters of sha1 in an entry id

# Today's cv.json (cv-docs.md 1): its sections, their titles in the PDF and its order.
V1_SECTIONS = ("education", "projects", "experience", "skills", "additional")
V1_ORDER = V1_SECTIONS
V1_TITLES = {"summary": "Profile", "education": "Education", "projects": "Projects", "experience": "Experience",
             "skills": "Skills", "additional": "Additional"}
V1_NAMED_BY = {"education": "qualification", "projects": "name", "experience": "role"}
V1_PAIRS = ("skills", "additional")
V1_TOP = ("title", "contact", "summary", "moreProjects", "closing", "pdfProjects", "order", "titles",
          *V1_SECTIONS)
V1_FIELDS = {"education": ("qualification", "grade", "meta", "detail", "modules", "pdfModulesMin", "modulesNote",
                           "hidden", "_key", "_id"),
             "projects": ("slug", "name", "stack", "body", "hidden", "_key", "_id"),
             "experience": ("role", "org", "dates", "body", "hidden", "_key", "_id")}
# Sections of other kinds have no place in cv.json: entry-shaped ones go under experience, the rest under
# additional, so unconverted callers still see their words.
V1_AS_EXPERIENCE = ("volunteering", "research", "teaching")
DATE_JOIN = " - "
META_JOIN = " | "


class CVError(ValueError):
    """A CV that does not fit schema 2; the message names the field path."""


@dataclass
class Bullet:
    """One line of an entry."""

    id: str
    text: str
    sources: list[str] = field(default_factory=list)   # record passages ("Volunteering/agreement.pdf > Role")
    locked: bool = False                               # "Lines Hal must not touch"


@dataclass
class Module:
    """One module of a course; marks are optional for everyone."""

    name: str
    mark: str = ""        # as written: "74", "A", "Distinction"
    year: str = ""


@dataclass
class Entry:
    """A job, qualification, project, skill group or other item of a section."""

    id: str
    heading: str = ""
    org: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    grade: str = ""
    meta: str = ""
    link: str = ""
    style: Literal["bullets", "paragraph"] = "bullets"
    text: str = ""
    bullets: list[Bullet] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    show_marks_from: int | None = None
    items: list[str] = field(default_factory=list)
    hidden: bool = False
    locked: bool = False


@dataclass
class Section:
    """A section of the CV, as the CV shows it."""

    id: str
    kind: SectionKind
    title: str
    text: str = ""
    entries: list[Entry] = field(default_factory=list)
    also: str = ""
    visible: bool = True
    locked: bool = False
    max_entries: int | None = None


@dataclass
class CVDoc:
    """A whole CV, without the name and contact block (those come from the profile)."""

    schema: int = SCHEMA
    headline: str = ""
    contact_snapshot: dict = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    closing: str = ""
    locale: str = "en-GB"
    extras: dict = field(default_factory=dict)


Target = str   # "s:education", "s:education/e:ed-1a2b3c4d", ".../b:<bullet id>", ".../text", ".../title";
#                "" is the whole document, and "headline" or "closing" its own fields


@dataclass(frozen=True)
class Change:
    """One edit to a document, by target."""

    op: Literal["replace", "insert", "delete", "move", "hide", "show", "reorder", "retitle"]
    target: Target
    value: Any = None
    position: int | None = None


# ------------------------------------------------------------------------------------------------- ids

def entry_id(kind: str, heading: str) -> str:
    """The first id of an entry: its kind's prefix and a hash of its kind and original heading."""
    digest = hashlib.sha1(f"{kind}\n{heading}".encode()).hexdigest()[:ID_HEX]
    return f"{PREFIX.get(kind, 'cu')}-{digest}"


def new_id(prefix: str) -> str:
    """A fresh id for a part added after the import ("b-3f9a1c2e")."""
    return f"{prefix}-{secrets.token_hex(ID_HEX // 2)}"


def _unique(wanted: str, taken: set[str]) -> str:
    out, n = wanted, 2
    while out in taken:
        out, n = f"{wanted}-{n}", n + 1
    taken.add(out)
    return out


# ------------------------------------------------------------------------------------------- JSON form

def to_dict(doc: CVDoc) -> dict:
    """The document as plain data."""
    return asdict(doc)


def to_json(doc: CVDoc) -> str:
    """Canonical JSON: sorted keys, ASCII, no spaces; the same document always gives the same text."""
    return json.dumps(to_dict(doc), sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _build(cls, data: Any, path: str):
    if not isinstance(data, dict):
        raise CVError(f"{path}: expected an object, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise CVError(f"{path}.{unknown[0]}: not a field of {cls.__name__}")
    out = {}
    for name, value in data.items():
        where = f"{path}.{name}"
        if name == "bullets":
            value = [_build(Bullet, b, f"{where}[{i}]") for i, b in enumerate(_list(value, where))]
        elif name == "modules":
            value = [_build(Module, m, f"{where}[{i}]") for i, m in enumerate(_list(value, where))]
        elif name == "entries":
            value = [_build(Entry, e, f"{where}[{i}]") for i, e in enumerate(_list(value, where))]
        elif name == "sections":
            value = [_build(Section, s, f"{where}[{i}]") for i, s in enumerate(_list(value, where))]
        elif name in ("sources", "items"):
            value = [str(x) for x in _list(value, where)]
        elif name in ("contact_snapshot", "extras"):
            if not isinstance(value, dict):
                raise CVError(f"{where}: expected an object")
        elif name in ("show_marks_from", "max_entries", "schema"):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise CVError(f"{where}: expected a whole number")
        elif name in ("hidden", "locked", "visible"):
            if not isinstance(value, bool):
                raise CVError(f"{where}: expected true or false")
        elif not isinstance(value, str):
            raise CVError(f"{where}: expected text, got {type(value).__name__}")
        out[name] = value
    if "id" in known and "id" not in out:
        raise CVError(f"{path}.id: missing")
    try:
        return cls(**out)
    except TypeError as e:
        raise CVError(f"{path}: {e}") from e


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise CVError(f"{where}: expected a list")
    return value


def from_dict(data: dict) -> CVDoc:
    """A document from plain data; CVError names the first field that does not fit."""
    doc = _build(CVDoc, data, "cv")
    if doc.schema != SCHEMA:
        raise CVError(f"cv.schema: this is schema {doc.schema}; Hal reads schema {SCHEMA}")
    return doc


def from_json(text: str) -> CVDoc:
    """A document from its JSON."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CVError(f"cv: not JSON ({e.msg} at line {e.lineno})") from e
    return from_dict(data)


def validate(doc: CVDoc, type_: Any = None) -> list[str]:
    """Problems with the document in plain words; empty when it is sound.

    With a CV type, the sections it requires (`sections.<id>.required = true`) must be there.
    """
    problems = []
    if doc.schema != SCHEMA:
        problems.append(f"This CV is schema {doc.schema}; Hal reads schema {SCHEMA}.")
    seen: set[str] = set()
    for s in doc.sections:
        if s.kind not in KINDS:
            problems.append(f"Section {s.id} has an unknown kind, {s.kind!r}.")
        if s.id in seen:
            problems.append(f"Two sections have the id {s.id}.")
        seen.add(s.id)
        if s.visible and not s.title.strip():
            problems.append(f"Section {s.id} has no title.")
        if s.max_entries is not None and s.max_entries < 0:
            problems.append(f"Section {s.title or s.id} has a negative most-entries limit.")
        for e in s.entries:
            if e.id in seen:
                problems.append(f"Two parts have the id {e.id}.")
            seen.add(e.id)
            if e.style not in STYLES:
                problems.append(f"Entry {e.heading or e.id} has an unknown style, {e.style!r}.")
            for b in e.bullets:
                if b.id in seen:
                    problems.append(f"Two lines have the id {b.id}.")
                seen.add(b.id)
    sections = getattr(type_, "sections", None) or {}
    have = {s.kind for s in doc.sections} | {s.id for s in doc.sections}
    for sid, rule in sections.items():
        if isinstance(rule, dict) and rule.get("required") and sid not in have:
            problems.append(f"Your CV type needs a {rule.get('title') or sid} section.")
    return problems


# ---------------------------------------------------------------------------------------------- targets

def _parse(target: Target) -> list[tuple[str, str]]:
    if target == "":
        return []        # the whole document: where sections are inserted or reordered
    parts = []
    for raw in target.split("/"):
        if ":" in raw:
            k, _, v = raw.partition(":")
            if k not in ("s", "e", "b") or not v:
                raise CVError(f"{target}: {raw!r} is not a part of a target")
            parts.append((k, v))
        elif raw:
            parts.append(("f", raw))
        else:
            raise CVError(f"{target}: empty part")
    return parts


def raw_kind(k: str) -> str:
    """The words for a target part's letter."""
    return {"s": "sections", "e": "entries", "b": "lines"}[k]


def _locate(doc: CVDoc, target: Target) -> tuple[Any, list | None, Any, str | None]:
    """(parent object, the list holding the object or None, the object, the field named last or None)."""
    parts = _parse(target)
    obj: Any = doc
    parent: Any = None
    holder: list | None = None
    for i, (k, v) in enumerate(parts):
        if k == "f":
            if i != len(parts) - 1:
                raise CVError(f"{target}: a field must come last")
            if not any(f.name == v for f in fields(obj)):
                raise CVError(f"{target}: {type(obj).__name__} has no field {v!r}")
            return parent, holder, obj, v
        pool = {"s": getattr(obj, "sections", None), "e": getattr(obj, "entries", None),
                "b": getattr(obj, "bullets", None)}[k]
        if pool is None:
            raise CVError(f"{target}: {type(obj).__name__} holds no {raw_kind(k)}")
        found = next((x for x in pool if x.id == v), None)
        if found is None:
            raise CVError(f"{target}: there is no {v!r} here")
        parent, holder, obj = obj, pool, found
    return parent, holder, obj, None


def find(doc: CVDoc, target: Target) -> object:
    """The section, entry, line or field value a target names; CVError when it is not there."""
    _, _, obj, name = _locate(doc, target)
    return getattr(obj, name) if name else obj


def target_of(section: Section, entry: Entry | None = None, bullet: Bullet | None = None) -> Target:
    """The target of a section, an entry in it, or a line of that entry."""
    out = f"s:{section.id}"
    if entry is not None:
        out += f"/e:{entry.id}"
    if bullet is not None:
        out += f"/b:{bullet.id}"
    return out


def _part(value: Any, cls, prefix: str):
    if isinstance(value, cls):
        return copy.deepcopy(value)
    if cls is Bullet and isinstance(value, str):
        return Bullet(id=new_id(prefix), text=value)
    if isinstance(value, dict):
        data = {"id": new_id(prefix), **value}
        return _build(cls, data, cls.__name__.lower())
    raise CVError(f"cannot insert {type(value).__name__} as {cls.__name__}")


def apply(doc: CVDoc, change: Change) -> CVDoc:
    """The document with one change made; the input is never changed."""
    if change.op not in OPS:
        raise CVError(f"{change.target}: unknown change {change.op!r}")
    out = copy.deepcopy(doc)
    if change.op == "insert":
        _, _, obj, name = _locate(out, change.target)
        if name:
            raise CVError(f"{change.target}: insert into a section or an entry, not a field")
        if isinstance(obj, CVDoc):
            pool, part = obj.sections, _part(change.value, Section, "custom")
        elif isinstance(obj, Section):
            pool, part = obj.entries, _part(change.value, Entry, PREFIX.get(obj.kind, "cu"))
        elif isinstance(obj, Entry):
            pool, part = obj.bullets, _part(change.value, Bullet, "b")
        else:
            raise CVError(f"{change.target}: a line holds no parts")
        pool.insert(len(pool) if change.position is None else change.position, part)
        return out
    _, holder, obj, name = _locate(out, change.target)
    if change.op == "replace":
        if name:
            setattr(obj, name, change.value)
        elif isinstance(obj, Bullet) and isinstance(change.value, str):
            obj.text = change.value
        elif isinstance(change.value, dict):
            for k, v in change.value.items():
                if k == "id" or not any(f.name == k for f in fields(obj)):
                    raise CVError(f"{change.target}: cannot replace {k!r}")
                setattr(obj, k, v)
        else:
            raise CVError(f"{change.target}: replace needs a field, or new values")
        return out
    if change.op == "retitle":
        if not isinstance(obj, Section):
            raise CVError(f"{change.target}: only a section has a title")
        obj.title = str(change.value or "")
        return out
    if change.op in ("hide", "show"):
        if isinstance(obj, Section):
            obj.visible = change.op == "show"
        elif isinstance(obj, Entry):
            obj.hidden = change.op == "hide"
        else:
            raise CVError(f"{change.target}: only a section or an entry can be hidden")
        return out
    if change.op == "delete":
        if holder is None or name:
            raise CVError(f"{change.target}: nothing to delete")
        holder.remove(obj)
        return out
    if change.op == "move":
        if holder is None or name or change.position is None:
            raise CVError(f"{change.target}: move needs a part and a position")
        holder.remove(obj)
        holder.insert(max(0, min(change.position, len(holder))), obj)
        return out
    # reorder: the target holds parts; value lists their ids in the new order
    pool = {CVDoc: "sections", Section: "entries", Entry: "bullets"}.get(type(obj))
    if pool is None or name:
        raise CVError(f"{change.target}: nothing to reorder")
    items = getattr(obj, pool)
    order = list(change.value or [])
    if sorted(order) != sorted(x.id for x in items):
        raise CVError(f"{change.target}: the new order must name each part once")
    by_id = {x.id: x for x in items}
    setattr(obj, pool, [by_id[i] for i in order])
    return out


# ------------------------------------------------------------------------------------------------ diff

def _label(s: Section, e: Entry | None = None) -> str:
    return f"{s.title or s.id}, {e.heading or e.id}" if e is not None else (s.title or s.id)


ENTRY_TEXT = ("heading", "org", "location", "start", "end", "grade", "meta", "link", "text")


def diff(a: CVDoc, b: CVDoc) -> list[dict]:
    """What changed from `a` to `b`, by id: {where, kind, before, after, target}.

    Kinds: Reworded, Added, Moved, Left out, Hidden (and Shown, for a part brought back).
    """
    out: list[dict] = []

    def add(where: str, kind: str, before: Any, after: Any, target: str) -> None:
        out.append({"where": where, "kind": kind, "before": before, "after": after, "target": target})

    for name, where in (("headline", "The line under your name"), ("closing", "Closing line")):
        if getattr(a, name) != getattr(b, name):
            add(where, "Reworded", getattr(a, name), getattr(b, name), name)
    old = {s.id: s for s in a.sections}
    new = {s.id: s for s in b.sections}
    for sid in _moves([s.id for s in a.sections], [s.id for s in b.sections]):
        add(_label(new[sid]), "Moved", None, None, f"s:{sid}")
    for s in b.sections:
        t = f"s:{s.id}"
        was = old.get(s.id)
        if was is None:
            add(_label(s), "Added", None, s.title, t)
            continue
        if was.visible != s.visible:
            add(_label(s), "Hidden" if not s.visible else "Shown", None, None, t)
        for name in ("title", "text", "also"):
            if getattr(was, name) != getattr(s, name):
                add(_label(s) if name != "title" else _label(was), "Reworded", getattr(was, name),
                    getattr(s, name), f"{t}/{name}")
        if was.max_entries != s.max_entries:
            add(_label(s), "Reworded", was.max_entries, s.max_entries, f"{t}/max_entries")
        _entries(was, s, add)
    for s in a.sections:
        if s.id not in new:
            add(_label(s), "Left out", s.title, None, f"s:{s.id}")
    return out


def _moves(before: list[str], after: list[str]) -> list[str]:
    """The fewest ids, among those in both lists, whose moving explains the new order.

    The rest keep their relative order (a longest increasing run of old positions), so swapping two
    entries reports one move, not two.
    """
    where = {x: i for i, x in enumerate(before)}
    common = [x for x in after if x in where]
    best: list[list[str]] = []
    for i, x in enumerate(common):
        run = max((best[j] for j in range(i) if where[common[j]] < where[x]), key=len, default=[])
        best.append([*run, x])
    stay = set(max(best, key=len, default=[]))
    return [x for x in common if x not in stay]


def _entries(was: Section, s: Section, add) -> None:
    old = {e.id: e for e in was.entries}
    new = {e.id: e for e in s.entries}
    t = f"s:{s.id}"
    for eid in _moves([e.id for e in was.entries], [e.id for e in s.entries]):
        add(_label(s, new[eid]), "Moved", None, None, f"{t}/e:{eid}")
    for e in s.entries:
        et = f"{t}/e:{e.id}"
        before = old.get(e.id)
        if before is None:
            add(_label(s, e), "Added", None, e.heading, et)
            continue
        if before.hidden != e.hidden:
            add(_label(s, e), "Hidden" if e.hidden else "Shown", None, None, et)
        for name in ENTRY_TEXT:
            if getattr(before, name) != getattr(e, name):
                add(_label(s, e), "Reworded", getattr(before, name), getattr(e, name), f"{et}/{name}")
        for name in ("items", "modules", "style", "show_marks_from"):
            if getattr(before, name) != getattr(e, name):
                old_v, new_v = getattr(before, name), getattr(e, name)
                if name == "modules":
                    old_v, new_v = [asdict(m) for m in old_v], [asdict(m) for m in new_v]
                add(_label(s, e), "Reworded", old_v, new_v, f"{et}/{name}")
        ob = {x.id: x for x in before.bullets}
        nb = {x.id: x for x in e.bullets}
        for bid in _moves([x.id for x in before.bullets], [x.id for x in e.bullets]):
            add(_label(s, e), "Moved", None, nb[bid].text, f"{et}/b:{bid}")
        for x in e.bullets:
            if x.id not in ob:
                add(_label(s, e), "Added", None, x.text, f"{et}/b:{x.id}")
            elif ob[x.id].text != x.text:
                add(_label(s, e), "Reworded", ob[x.id].text, x.text, f"{et}/b:{x.id}")
        for x in before.bullets:
            if x.id not in nb:
                add(_label(s, e), "Left out", x.text, None, f"{et}/b:{x.id}")
    for e in was.entries:
        if e.id not in new:
            add(_label(was, e), "Left out", e.heading, None, f"{t}/e:{e.id}")


# ------------------------------------------------------------------------------------------ plain text

def dates(e: Entry) -> str:
    """An entry's dates as written: "Sep 2023 - Jun 2024", or the one date given."""
    return DATE_JOIN.join(x for x in (e.start, e.end) if x) if e.end else e.start


def _body(e: Entry) -> list[str]:
    return [b.text for b in e.bullets] if e.style == "bullets" else ([e.text] if e.text else [])


def plain_text(doc: CVDoc, profile: dict) -> str:
    """The CV as plain text, the only facts a draft may use; hidden parts are left out.

    `profile` gives the name (`name`) and a status line (`status`), never part of the CV body.
    """
    lines = [profile.get("name") or "", doc.headline]
    for s in doc.sections:
        if not s.visible:
            continue
        if s.text:
            lines.append(f"{s.title}: {s.text}")
        for e in s.entries:
            if e.hidden:
                continue
            head = ", ".join(x for x in (e.heading, e.org, e.location) if x)
            when = dates(e)
            extra = "; ".join(x for x in (e.grade, e.meta, ", ".join(e.items)) if x)
            lines.append(f"{s.title}: {head}" + (f" ({when})" if when else "") + (f": {extra}" if extra else ""))
            lines += _body(e)
            if e.modules:
                lines.append("Modules: " + ", ".join(f"{m.name} {m.mark}".strip() for m in e.modules))
        if s.also:
            lines.append(s.also)
    if profile.get("status"):
        lines.append(profile["status"])
    return re.sub(r"\*\*", "", "\n".join(x for x in lines if x))


# ------------------------------------------------------------------------------- today's cv.json shape

def split_dates(text: str) -> tuple[str, str]:
    """("Sep 2023", "Jun 2024") from "Sep 2023 - Jun 2024"; any other text is kept whole as the start."""
    start, sep, end = text.partition(DATE_JOIN)
    return (start, end) if sep and start and end else (text, "")


def _lines_to_entry(text: str, entry_id_: str) -> tuple[str, str, list[Bullet]]:
    lines = text.split("\n")
    if text and all(line.startswith("- ") for line in lines):
        return "bullets", "", [Bullet(id=f"{entry_id_}.b{i + 1}", text=line[2:]) for i, line in enumerate(lines)]
    return "paragraph", text, []


def _entry_text(e: Entry) -> str:
    return "\n".join(f"- {b.text}" for b in e.bullets) if e.style == "bullets" else e.text


def _mark_out(mark: str) -> Any:
    return int(mark) if mark.isdigit() else mark


def from_v1(v1: dict) -> CVDoc:
    """Today's `cv.json` (with Hal's `hidden`, `_key`, `_id` and `pdfProjects`) as schema 2.

    An entry's id is the `_id` it carries, if any, else made from its kind and its
    original name (`_key`, else its name), so every import of the same CV gives the same ids.
    """
    taken: set[str] = set()
    extras: dict = {}
    other = {k: copy.deepcopy(v) for k, v in v1.items() if k not in V1_TOP}
    if other:
        extras["v1"] = other
    if "order" in v1:
        extras["v1_order"] = True
    titles = {**V1_TITLES, **(v1.get("titles") or {})}
    module_years: dict = {}
    field_extras: dict = {}
    sections: dict[str, Section] = {"summary": Section(id="summary", kind="summary", title=titles["summary"],
                                                      text=v1.get("summary") or "")}
    for name in V1_NAMED_BY:
        sec = Section(id=name, kind=name, title=titles[name])
        for item in v1.get(name) or []:
            heading = item.get(V1_NAMED_BY[name]) or ""
            eid = item.get("_id") or _unique(entry_id(name, item.get("_key") or heading), taken)
            taken.add(eid)
            e = Entry(id=eid, heading=heading, hidden=bool(item.get("hidden")))
            if name == "education":
                org, sep, when = (item.get("meta") or "").partition(META_JOIN)
                e.org = org
                e.start, e.end = split_dates(when) if sep else ("", "")
                if sep and not when:
                    field_extras.setdefault(eid, {})["meta"] = item.get("meta")
                e.grade = item.get("grade") or ""
                e.meta = item.get("modulesNote") or ""
                e.style, e.text, e.bullets = _lines_to_entry(item.get("detail") or "", eid)
                years = []
                for year in item.get("modules") or []:
                    years.append({k: year[k] for k in ("year", "mark") if k in year})
                    e.modules += [Module(name=str(r[0]), mark=str(r[1]), year=str(year.get("year") or ""))
                                  for r in year.get("rows") or []]
                if years:
                    module_years[eid] = years
                if item.get("pdfModulesMin") is not None:
                    e.show_marks_from = int(item["pdfModulesMin"])
            elif name == "projects":
                e.meta, e.link = item.get("stack") or "", item.get("slug") or ""
                e.style, e.text, e.bullets = _lines_to_entry(item.get("body") or "", eid)
            else:
                e.org = item.get("org") or ""
                e.start, e.end = split_dates(item.get("dates") or "")
                e.style, e.text, e.bullets = _lines_to_entry(item.get("body") or "", eid)
            rest = {k: copy.deepcopy(v) for k, v in item.items() if k not in V1_FIELDS[name]}
            if rest:
                field_extras.setdefault(eid, {}).update(rest)
            sec.entries.append(e)
        sections[name] = sec
    if v1.get("moreProjects"):
        sections["projects"].also = v1["moreProjects"]
    if v1.get("pdfProjects") not in (None, ""):
        sections["projects"].max_entries = int(v1["pdfProjects"])
    skills = Section(id="skills", kind="skills", title=titles["skills"])
    for pair in v1.get("skills") or []:
        eid = _unique(entry_id("skills", str(pair[0])), taken)
        items = pair[1] if isinstance(pair[1], list) else [pair[1]]
        skills.entries.append(Entry(id=eid, heading=str(pair[0]), items=[str(x) for x in items]))
    sections["skills"] = skills
    additional = Section(id="additional", kind="additional", title=titles["additional"])
    for pair in v1.get("additional") or []:
        eid = _unique(entry_id("additional", str(pair[0])), taken)
        additional.entries.append(Entry(id=eid, heading=str(pair[0]), style="paragraph", text=str(pair[1])))
    sections["additional"] = additional
    order = [x for x in (v1.get("order") or V1_ORDER) if x in sections and x != "summary"]
    order += [x for x in V1_ORDER if x not in order]
    if module_years:
        extras["module_years"] = module_years
    if field_extras:
        extras["v1_fields"] = field_extras
    return CVDoc(headline=v1.get("title") or "", contact_snapshot=copy.deepcopy(v1.get("contact") or {}),
                 sections=[sections["summary"], *(sections[x] for x in order)], closing=v1.get("closing") or "",
                 extras=extras)


def _modules_v1(doc: CVDoc, e: Entry) -> list[dict]:
    years = (doc.extras.get("module_years") or {}).get(e.id)
    if years is None:
        years = []
        for m in e.modules:
            if not any(y.get("year") == m.year for y in years):
                years.append({"year": m.year})
    out = []
    for y in years:
        rows = [[m.name, _mark_out(m.mark)] for m in e.modules if m.year == str(y.get("year") or "")]
        out.append({**y, "rows": rows})
    return out


def to_v1(doc: CVDoc, profile: dict) -> dict:
    """The document in today's `cv.json` shape, entries left out kept with `hidden` and each carrying `_id`.

    `profile["contact"]` is the contact block; the CV body never holds one.
    """
    fx = doc.extras.get("v1_fields") or {}
    out: dict = {**copy.deepcopy(doc.extras.get("v1") or {}), "title": doc.headline,
                 "contact": copy.deepcopy(profile.get("contact") or {}), "summary": "", "education": [],
                 "projects": [], "experience": [], "skills": [], "additional": [], "moreProjects": "",
                 "closing": doc.closing}
    titles: dict = {}
    order: list[str] = []
    for s in doc.sections:
        if s.kind == "summary":
            out["summary"] = s.text if s.visible else ""
            continue
        v1_name = s.kind if s.kind in V1_SECTIONS else (
            "experience" if s.kind in V1_AS_EXPERIENCE else "additional")
        if s.kind in V1_SECTIONS and s.title != V1_TITLES[s.kind]:
            titles[s.kind] = s.title
        if v1_name not in order:
            order.append(v1_name)
        if s.kind not in V1_SECTIONS and s.text and s.visible:
            out["additional"].append([s.title, s.text])
        for e in s.entries:
            hidden = e.hidden or not s.visible
            if v1_name == "education":
                when = dates(e)
                item = {"qualification": e.heading, "grade": e.grade,
                        "meta": META_JOIN.join([e.org, when]) if when else e.org, "detail": _entry_text(e)}
                if not e.grade:
                    item.pop("grade")
                if e.modules or e.id in (doc.extras.get("module_years") or {}):
                    item["modules"] = _modules_v1(doc, e)
                if e.show_marks_from is not None:
                    item["pdfModulesMin"] = e.show_marks_from
                if e.meta:
                    item["modulesNote"] = e.meta
            elif v1_name == "projects":
                item = {"slug": e.link, "name": e.heading, "stack": e.meta, "body": _entry_text(e)}
            elif v1_name == "experience":
                item = {"role": e.heading, "org": e.org, "dates": dates(e), "body": _entry_text(e)}
            elif s.kind == "skills":
                if not hidden:
                    out["skills"].append([e.heading, list(e.items)])
                continue
            else:
                if not hidden:
                    words = e.text or "; ".join(e.items) or "; ".join(b.text for b in e.bullets)
                    out["additional"].append([e.heading, words])
                continue
            item.update(copy.deepcopy(fx.get(e.id) or {}))
            item["_id"] = e.id
            if hidden:
                item["hidden"] = True
            out[v1_name].append(item)
        if s.kind == "projects":
            out["moreProjects"] = s.also
            if s.max_entries is not None:
                out["pdfProjects"] = s.max_entries
    if titles:
        out["titles"] = titles
    if doc.extras.get("v1_order") or order != [x for x in V1_ORDER if x in order]:
        out["order"] = order
    return out


def string_leaves(value: Any) -> list[str]:
    """Every non-empty string in a nested value, numbers included as text."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int | float):
        return [str(value)]
    if isinstance(value, dict):
        return [x for v in value.values() for x in string_leaves(v)]
    if isinstance(value, list | tuple):
        return [x for v in value for x in string_leaves(v)]
    return []


def _norm(text: str) -> str:
    return " ".join(text.split())


def not_found(v1_printable: dict, doc: CVDoc) -> list[str]:
    """The strings of a printable `cv.json` that the document does not hold anywhere."""
    have = [_norm(x) for x in string_leaves(to_dict(doc))]
    have += [_norm(x) for x in string_leaves(to_v1(doc, {"contact": doc.contact_snapshot}))]
    pool = set(have)
    joined = "\n".join(have)
    missing = []
    for leaf in string_leaves(v1_printable):
        n = _norm(leaf)
        if n and n not in pool and n not in joined and leaf not in missing:
            missing.append(leaf)
    return missing

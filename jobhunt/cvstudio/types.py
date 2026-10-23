"""CV types: what goes in a CV, how it is written and how it is checked.

- Built-in types are the TOML files in `cvstudio/types/`; a copy the person makes is a `cv_types` row with
  the same TOML, edited as text. Every source goes through one parser, so a copy with a mistake reports the
  line it is on, whether the mistake is bad TOML or a value the schema refuses.
- A built-in is never changed: editing one (the Writing card) first makes a copy named "<type> (yours)".
- A type changes a CV only when it is shown: `arrange` returns a copy of the document with the type's
  order, titles and limits. That is why comparing two types needs no AI.
- `suggest` picks a type by rules from the goal's career stage, the subject studied and the roles wanted,
  scored by each type's own `[suggest]` table, so a copied type can be suggested by the same rules.
"""
from __future__ import annotations

import dataclasses
import functools
import json
import logging
import re
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).resolve().parent / "types"
# The order the picker shows the built-ins in: graduates by field first, then the other stages.
BUILTIN_ORDER = ("humanities_grad", "technical_grad", "stem_grad", "business_grad", "general_grad", "student",
                 "career_changer", "experienced", "school_leaver", "academic", "creative")
FALLBACK = "general_grad"
COPY_SUFFIX = " (yours)"
MAX_PAGES = 4
MAX_NAME = 80
MAX_SOURCE_CHARS = 50_000      # a type is a page of settings; anything longer was pasted by mistake

SECTION_KINDS = ("summary", "education", "experience", "volunteering", "projects", "research", "skills",
                 "achievements", "publications", "certifications", "languages", "interests", "teaching",
                 "talks", "portfolio", "additional", "custom")
CHECK_IDS = ("profile_evidence", "result_bullets", "dissertation_shown", "modules_strong", "dates_one_format",
             "no_personal_data", "pages_within", "one_spelling", "entry_says_what", "contact_at_top",
             "projects_with_evidence", "education_first", "skills_backed", "right_to_work_line",
             "no_module_codes", "no_first_person", "portfolio_link", "newest_first", "availability_line")
TOP_KEYS = {"id", "name", "fits", "note", "pages", "section_order", "optional_sections", "order_words",
            "length_words", "tips", "suggest", "sections", "writing", "checks", "keywords", "documents", "prompts"}
SECTION_KEYS = {"title": str, "max_entries": int, "max_bullets": int, "max_words": int, "show_modules": str,
                "min_mark": int, "dissertation": bool, "style": str, "optional": bool, "newest_first": bool,
                "headlines": list}
CHOICES = {
    ("sections", "show_modules"): ("all", "strong", "none"),
    ("sections", "style"): ("groups", "list"),
    ("writing", "person"): ("none", "first"),
    ("writing", "style"): ("bullets", "paragraphs"),
    ("writing", "results"): ("where_have", "rarely"),
    ("writing", "spelling"): ("en-GB", "en-US"),
    ("writing", "tone"): ("plain", "formal", "confident"),
}
WRITING_KEYS = {"person": str, "style": str, "bullet_words": list, "verb_first": bool, "results": str,
                "spelling": str, "tone": str, "banned": list}
DOCUMENT_KEYS = {"evidence": list, "letter_mentions": str, "hiring_titles": list, "prep_topics": list,
                 "special_cases": list}
SUGGEST_KEYS = ("stages", "subjects", "roles")
# The same section kinds shown under their usual names when a type does not name them.
DEFAULT_TITLES = {"summary": "Profile", "education": "Education", "experience": "Experience",
                  "volunteering": "Volunteering", "projects": "Projects", "research": "Research", "skills": "Skills",
                  "achievements": "Achievements", "publications": "Publications", "certifications": "Certifications",
                  "languages": "Languages", "interests": "Interests", "teaching": "Teaching", "talks": "Talks",
                  "portfolio": "Portfolio", "additional": "Additional", "custom": "Other"}
LENGTH_WORDS = {0: "As long as needed", 1: "1 page", 2: "Up to 2 pages"}
# Rule weights for `suggest`: the career stage settles most cases, then the subject, then the roles.
STAGE_POINTS, SUBJECT_POINTS, ROLE_POINTS = 3, 2, 2
STAGE_TYPES = ("student", "career_changer", "experienced", "school_leaver")
TONES = {"plain": "Plain", "formal": "Formal", "confident": "Confident"}


class TypeSourceError(ValueError):
    """A type's TOML that cannot be used, with the line the problem is on (1-based; 0 when unknown)."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(f"Line {line}: {message}" if line else message)
        self.message, self.line = message, line


@dataclass(frozen=True)
class CVType:
    """One CV type, built in or copied."""

    id: str
    name: str
    fits: str
    note: str
    pages: int
    section_order: tuple[str, ...]
    sections: dict
    writing: dict
    checks: dict[str, float]
    keywords: dict
    documents: dict
    prompts: dict
    optional_sections: tuple[str, ...] = ()
    tips: tuple[str, ...] = ()
    suggest_rules: dict = field(default_factory=dict)
    order_words: str = ""
    length_words: str = ""
    builtin: bool = True
    based_on: str = ""
    source: str = ""

    def title_of(self, kind: str) -> str:
        """What a section of this kind is called under this type."""
        return (self.sections.get(kind) or {}).get("title") or DEFAULT_TITLES.get(kind, kind.title())

    def summary(self) -> dict:
        """The picker's card: name, who it fits, order, length and note."""
        order = self.order_words or ", ".join(self.title_of(k) for k in self.section_order)
        length = self.length_words or LENGTH_WORDS.get(self.pages, f"Up to {self.pages} pages")
        return {"id": self.id, "name": self.name, "fits": self.fits, "order": order, "pages": length,
                "note": self.note, "builtin": self.builtin, "tips": list(self.tips), "based_on": self.based_on}


# Reading and checking a source ------------------------------------------------------------------------------

def _line_of(text: str, key: str, table: str = "") -> int:
    """The line `key = ...` is on inside `[table]` (or the table header itself when `key` is empty)."""
    current = ""
    for n, line in enumerate(text.splitlines(), 1):
        head = re.match(r"\s*\[([^\]]+)\]\s*(#.*)?$", line)
        if head:
            current = head.group(1).strip()
            if not key and current == table:
                return n
            continue
        if key and current == table and re.match(rf"\s*\"?{re.escape(key)}\"?\s*=", line):
            return n
    return 0


def _fail(text: str, message: str, key: str = "", table: str = "") -> TypeSourceError:
    return TypeSourceError(message, _line_of(text, key, table) or _line_of(text, "", table))


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_table(text: str, table: str, data: Any, keys: dict) -> None:
    if not isinstance(data, dict):
        raise _fail(text, f"[{table}] must be a table.", table.split(".")[0])
    group = table.split(".")[0]
    for key, value in data.items():
        want = keys.get(key)
        if want is None:
            raise _fail(text, f"\"{key}\" is not something [{group}] can hold.", key, table)
        if want is int and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise _fail(text, f"\"{key}\" must be a whole number, 0 or more.", key, table)
        if want is not int and not isinstance(value, want):
            raise _fail(text, f"\"{key}\" must be {_kind_words(want)}.", key, table)
        if want is list and key != "bullet_words" and not _strings(value):
            raise _fail(text, f"\"{key}\" must be a list of words in quotes.", key, table)
        allowed = CHOICES.get((group, key))
        if allowed and value not in allowed:
            raise _fail(text, f"\"{key}\" must be one of: {', '.join(allowed)}.", key, table)


def _kind_words(t: type) -> str:
    return {str: "words in quotes", bool: "true or false", list: "a list in square brackets",
            dict: "a table"}.get(t, "a value")


def parse(text: str, *, builtin: bool = False, based_on: str = "") -> CVType:
    """A `CVType` from TOML; raises `TypeSourceError` naming the line of the first problem."""
    if len(text) > MAX_SOURCE_CHARS:
        raise TypeSourceError(f"This is longer than a CV type can be ({MAX_SOURCE_CHARS} characters).")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        found = re.search(r"\(at line (\d+), column \d+\)", str(e))
        message = re.sub(r"\s*\(at line \d+, column \d+\)", "", str(e)).strip()
        raise TypeSourceError(f"{message}. Check the quotes, brackets and = signs.",
                              int(found.group(1)) if found else 0) from e
    for key in data:
        if key not in TOP_KEYS:
            raise _fail(text, f"\"{key}\" is not part of a CV type.", key)
    for key in ("id", "name", "fits", "note"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise _fail(text, f"\"{key}\" is missing or empty.", key)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", data["id"]):
        raise _fail(text, "\"id\" may use only small letters, digits, - and _.", "id")
    if len(data["name"]) > MAX_NAME:
        raise _fail(text, f"\"name\" must be at most {MAX_NAME} characters.", "name")
    pages = data.get("pages", 0)
    if isinstance(pages, bool) or not isinstance(pages, int) or not 0 <= pages <= MAX_PAGES:
        raise _fail(text, f"\"pages\" must be a whole number from 0 (as needed) to {MAX_PAGES}.", "pages")
    for key in ("section_order", "optional_sections"):
        value = data.get(key, [])
        if not _strings(value) or (key == "section_order" and not value):
            raise _fail(text, f"\"{key}\" must be a list of section kinds in quotes.", key)
        for kind in value:
            if kind not in SECTION_KINDS:
                raise _fail(text, f"\"{kind}\" is not a kind of section. Use one of: {', '.join(SECTION_KINDS)}.",
                            key)
    if not _strings(data.get("tips", [])):
        raise _fail(text, "\"tips\" must be a list of sentences in quotes.", "tips")
    sections = data.get("sections", {})
    if not isinstance(sections, dict):
        raise _fail(text, "[sections] must be a table.", "sections")
    for kind, table in sections.items():
        if kind not in SECTION_KINDS:
            raise _fail(text, f"\"{kind}\" is not a kind of section.", "", f"sections.{kind}")
        _check_table(text, f"sections.{kind}", table, SECTION_KEYS)
    writing = data.get("writing", {})
    _check_table(text, "writing", writing, WRITING_KEYS)
    words = writing.get("bullet_words")
    if words is not None and not (len(words) == 2 and all(isinstance(w, int) and not isinstance(w, bool)
                                                          and w > 0 for w in words) and words[0] <= words[1]):
        raise _fail(text, "\"bullet_words\" must be two whole numbers, fewest then most: [8, 30].",
                    "bullet_words", "writing")
    checks = data.get("checks", {})
    if not isinstance(checks, dict):
        raise _fail(text, "[checks] must be a table.", "checks")
    for cid, weight in checks.items():
        if cid not in CHECK_IDS:
            raise _fail(text, f"\"{cid}\" is not a check Hal knows. Use one of: {', '.join(CHECK_IDS)}.",
                        cid, "checks")
        if isinstance(weight, bool) or not isinstance(weight, int | float) or weight < 0:
            raise _fail(text, f"The weight of \"{cid}\" must be a number, 0 or more (0 switches it off).",
                        cid, "checks")
    keywords = data.get("keywords", {})
    if not isinstance(keywords, dict) or not _strings(keywords.get("skills", [])):
        raise _fail(text, "[keywords] needs skills = [\"...\"], a list of words in quotes.", "skills", "keywords")
    aliases = keywords.get("aliases", {})
    if not isinstance(aliases, dict) or not all(_strings(v) for v in aliases.values()):
        raise _fail(text, "\"aliases\" must be { word = [\"other name\", ...] }.", "aliases", "keywords")
    _check_table(text, "documents", data.get("documents", {}), DOCUMENT_KEYS)
    prompts = data.get("prompts", {})
    if not isinstance(prompts, dict) or not all(isinstance(v, str) for v in prompts.values()):
        raise _fail(text, "Every line in [prompts] must be words in quotes.", "", "prompts")
    rules = data.get("suggest", {})
    if not isinstance(rules, dict) or any(k not in SUGGEST_KEYS or not _strings(v) for k, v in rules.items()):
        raise _fail(text, "[suggest] may hold stages, subjects and roles, each a list of words in quotes.",
                    "", "suggest")
    return CVType(
        id=data["id"], name=data["name"].strip(), fits=data["fits"].strip(), note=data["note"].strip(), pages=pages,
        section_order=tuple(data["section_order"]), sections=sections, writing=writing,
        checks={k: float(v) for k, v in checks.items()}, keywords=keywords, documents=data.get("documents", {}),
        prompts=prompts, optional_sections=tuple(data.get("optional_sections", [])),
        tips=tuple(data.get("tips", [])), suggest_rules=rules, order_words=data.get("order_words", ""),
        length_words=data.get("length_words", ""), builtin=builtin, based_on=based_on, source=text)


# Built-ins and copies --------------------------------------------------------------------------------------

@functools.cache
def _builtin(type_id: str) -> CVType:
    path = BUILTIN_DIR / f"{type_id}.toml"
    t = parse(path.read_text(encoding="utf-8"), builtin=True)
    if t.id != type_id:
        raise TypeSourceError(f"{path.name} says its id is {t.id!r}.", _line_of(t.source, "id"))
    return t


def builtin_ids() -> list[str]:
    """Every built-in type id, in the picker's order, then any file not in that order."""
    found = sorted(p.stem for p in BUILTIN_DIR.glob("*.toml"))
    return [i for i in BUILTIN_ORDER if i in found] + [i for i in found if i not in BUILTIN_ORDER]


def _con(con: sqlite3.Connection | None):
    if con is not None:
        return con, False
    from jobhunt import db

    return db.connect(), True


def _copy_row(type_id: str, con: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    c, own = _con(con)
    try:
        return c.execute("SELECT * FROM cv_types WHERE id = ?", (type_id,)).fetchone()
    finally:
        if own:
            c.close()


def load(type_id: str, con: sqlite3.Connection | None = None) -> CVType:
    """A type by id, built in or copied; LookupError when there is none."""
    if (BUILTIN_DIR / f"{type_id}.toml").is_file() and re.fullmatch(r"[a-z0-9_-]+", type_id or ""):
        return _builtin(type_id)
    row = _copy_row(type_id, con)
    if row is None:
        raise LookupError(f"There is no CV type called {type_id!r}.")
    try:
        return parse(row["toml"], builtin=False, based_on=row["based_on"] or "")
    except TypeSourceError:
        # A saved copy was checked when saved; one that no longer reads falls back to the type it came from.
        log.warning("the saved CV type %s no longer reads; using %s", type_id, row["based_on"] or FALLBACK)
        return dataclasses.replace(load(row["based_on"] or FALLBACK, con), id=type_id, name=row["name"],
                                   builtin=False, based_on=row["based_on"] or "")


def all_types(con: sqlite3.Connection | None = None) -> list[CVType]:
    """Every type: the built-ins in the picker's order, then the person's copies, oldest first."""
    out = [_builtin(i) for i in builtin_ids()]
    c, own = _con(con)
    try:
        rows = c.execute("SELECT id FROM cv_types ORDER BY created, id").fetchall()
    finally:
        if own:
            c.close()
    return out + [load(r["id"], con) for r in rows]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "type"


def copy(type_id: str, name: str = "", con: sqlite3.Connection | None = None) -> CVType:
    """A new copy of a type the person can change, named `name` or "<type> (yours)"."""
    from jobhunt import db

    base = load(type_id, con)
    name = (name or f"{base.name}{COPY_SUFFIX}").strip()[:MAX_NAME]
    c, own = _con(con)
    try:
        taken = {r["id"] for r in c.execute("SELECT id FROM cv_types")} | set(builtin_ids())
        new_id, n = f"my-{_slug(name)}", 2
        while new_id in taken:
            new_id, n = f"my-{_slug(name)}-{n}", n + 1
        text = _rename(base.source, new_id, name)
        parse(text)
        now = db.now()
        c.execute("INSERT INTO cv_types (id, name, based_on, toml, created, updated) VALUES (?, ?, ?, ?, ?, ?)",
                  (new_id, name, base.based_on or base.id, text, now, now))
        c.commit()
    finally:
        if own:
            c.close()
    return load(new_id, con)


def _rename(text: str, new_id: str, name: str) -> str:
    """The source with its top-level `id` and `name` lines replaced, keeping every other line and comment."""
    out, in_table = [], False
    for line in text.splitlines():
        in_table = in_table or bool(re.match(r"\s*\[", line))
        m = None if in_table else re.match(r"\s*(id|name)\s*=", line)
        out.append(f"{m.group(1)} = {json.dumps(new_id if m.group(1) == 'id' else name)}" if m else line)
    return "\n".join(out) + "\n"


def source(type_id: str, con: sqlite3.Connection | None = None) -> str:
    """A type's TOML as the person edits it."""
    return load(type_id, con).source


def save_source(type_id: str, text: str, con: sqlite3.Connection | None = None) -> CVType:
    """Saves the person's TOML for a copied type; a built-in cannot be saved over."""
    from jobhunt import db

    if (BUILTIN_DIR / f"{type_id}.toml").is_file():
        raise PermissionError("Built-in CV types cannot be changed. Copy it to change it.")
    row = _copy_row(type_id, con)
    if row is None:
        raise LookupError(f"There is no CV type called {type_id!r}.")
    t = parse(text, based_on=row["based_on"] or "")
    if t.id != type_id:
        raise TypeSourceError(f"The id of this copy stays \"{type_id}\".", _line_of(text, "id"))
    c, own = _con(con)
    try:
        c.execute("UPDATE cv_types SET name = ?, toml = ?, updated = ? WHERE id = ?", (t.name, text, db.now(), type_id))
        c.commit()
    finally:
        if own:
            c.close()
    return load(type_id, con)


# Writing it out again (the Writing card) ---------------------------------------------------------------------

def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(int(v)) if float(v).is_integer() else str(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} in a CV type")


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def dump(t: CVType) -> str:
    """The type as TOML that `parse` reads back to the same type."""
    lines = ["# A copy of a CV type, saved by My CV.", f"id = {_value(t.id)}", f"name = {_value(t.name)}",
             f"fits = {_value(t.fits)}", f"note = {_value(t.note)}"]
    if t.order_words:
        lines.append(f"order_words = {_value(t.order_words)}")
    if t.length_words:
        lines.append(f"length_words = {_value(t.length_words)}")
    lines += [f"pages = {t.pages}", f"section_order = {_value(list(t.section_order))}",
              f"optional_sections = {_value(list(t.optional_sections))}", f"tips = {_value(list(t.tips))}"]
    tables = [("suggest", t.suggest_rules)] + [(f"sections.{k}", v) for k, v in t.sections.items()]
    tables += [("writing", t.writing), ("checks", t.checks), ("keywords", t.keywords), ("documents", t.documents),
               ("prompts", t.prompts)]
    for name, table in tables:
        lines += ["", f"[{name}]"] + [f"{_key(k)} = {_value(v)}" for k, v in table.items()]
    return "\n".join(lines) + "\n"


WRITING_FIELDS = ("pages", "tone", "format", "results", "never_use")


def writing_card(t: CVType) -> dict:
    """The Writing card's values for a type."""
    return {"pages": t.pages, "tone": t.writing.get("tone", "plain"),
            "format": "paragraphs" if t.writing.get("style") == "paragraphs" else "bullets",
            "results": t.writing.get("results", "where_have"), "never_use": list(t.writing.get("banned", []))}


def edit_writing(type_id: str, changes: dict, con: sqlite3.Connection | None = None) -> tuple[CVType, str]:
    """Applies the Writing card to a type; a built-in is copied first. Returns (the type saved, a note or "")."""
    unknown = sorted(set(changes) - set(WRITING_FIELDS))
    if unknown:
        raise ValueError(f"The Writing card has no {', '.join(unknown)}.")
    t, note = load(type_id, con), ""
    if t.builtin:
        base_name = t.name
        t = copy(type_id, con=con)
        note = f"Built-in types stay as they are, so Hal made a copy called \"{t.name}\" from {base_name}."
    writing = dict(t.writing)
    pages = changes.get("pages", t.pages)
    if "tone" in changes:
        writing["tone"] = changes["tone"]
    if "format" in changes:
        writing["style"] = "paragraphs" if changes["format"] == "paragraphs" else "bullets"
    if "results" in changes:
        writing["results"] = changes["results"]
    if "never_use" in changes:
        words = changes["never_use"]
        if not _strings(words):
            raise ValueError("Never use must be a list of words and phrases.")
        writing["banned"] = [w.strip() for w in words if w.strip()]
    edited = dataclasses.replace(t, pages=pages, writing=writing,
                                 length_words="" if pages != t.pages else t.length_words)
    saved = save_source(t.id, dump(edited), con)
    return saved, note


# Showing a CV under a type -----------------------------------------------------------------------------------

def _mark(m: Any) -> float | None:
    try:
        return float(str(getattr(m, "mark", "")).strip().rstrip("%"))
    except ValueError:
        return None


def _entry_for(entry: Any, rules: dict) -> Any:
    changes: dict[str, Any] = {}
    most = rules.get("max_bullets")
    if most and len(entry.bullets) > most:
        changes["bullets"] = list(entry.bullets[:most])
    show = rules.get("show_modules")
    if show == "none" and entry.modules:
        changes["modules"] = []
    elif show == "strong" and entry.modules:
        floor = entry.show_marks_from if entry.show_marks_from is not None else rules.get("min_mark", 0)
        changes["modules"] = [m for m in entry.modules if (_mark(m) is None or _mark(m) >= floor)]
    return dataclasses.replace(entry, **changes) if changes else entry


def arrange(doc: Any, t: CVType) -> Any:
    """The document as `t` shows it: sections in the type's order with its titles and limits. Pure."""
    rank = {k: i for i, k in enumerate(t.section_order)}
    later = len(rank)
    sections = []
    for pos, s in enumerate(doc.sections):
        rules = t.sections.get(s.kind) or {}
        entries = [_entry_for(e, rules) for e in s.entries if not e.hidden]
        most = s.max_entries or rules.get("max_entries")
        if most:
            entries = entries[:most]
        title = s.title if s.locked else t.title_of(s.kind) if s.kind != "custom" else s.title
        sections.append((rank.get(s.kind, later), pos, dataclasses.replace(s, title=title, entries=entries)))
    return dataclasses.replace(doc, sections=[s for _, _, s in sorted(sections, key=lambda x: (x[0], x[1]))])


def outline(doc: Any, t: CVType) -> dict:
    """What a CV looks like under `t`, section by section, for comparing two types side by side."""
    shown = arrange(doc, t)
    rows = [{"title": s.title, "kind": s.kind, "entries": len(s.entries),
             "limit": (t.sections.get(s.kind) or {}).get("max_entries")}
            for s in shown.sections if s.visible]
    missing = [t.title_of(k) for k in t.section_order if not any(s.kind == k for s in shown.sections)]
    return {"id": t.id, "name": t.name, "pages": t.summary()["pages"], "sections": rows, "missing": missing}


# Suggesting a type -----------------------------------------------------------------------------------------

def _goal_values(goal: Any) -> tuple[str, list[str], list[str]]:
    if goal is None:
        return "", [], []
    get = goal.get if isinstance(goal, dict) else lambda k, d=None: getattr(goal, k, d)
    kinds = get("kinds", ()) or ()
    return str(get("stage", "") or ""), [str(r) for r in get("roles", ()) or ()], sorted(str(k) for k in kinds)


def _subject(doc: Any, subject: str) -> str:
    if subject:
        return subject
    for s in getattr(doc, "sections", []) or []:
        if s.kind == "education" and s.entries:
            return s.entries[0].heading
    return ""


def _hits(words: list[str], text: str) -> list[str]:
    low = f" {text.lower()} "
    return [w for w in words if w.strip() and re.search(rf"(?<![a-z]){re.escape(w.strip().lower())}", low)]


def _and(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


KIND_WORDS = {"internship": "paid internships", "placement": "placement years",
              "insight": "insight weeks and spring weeks", "graduate_scheme": "graduate schemes",
              "apprenticeship": "apprenticeships", "graduate_job": "graduate jobs"}


def _subject_words(subject: str) -> str:
    """"BA (Hons) History and Politics" -> "History and Politics"."""
    return re.sub(r"^\s*(BA|BSc|BEng|MEng|MA|MSc|MSci|MComp|LLB|BMus|BEd|PhD|MPhil)\b\.?\s*(\(Hons\))?\s*(in\s+)?",
                  "", subject or "", flags=re.I).strip()


def _why(t: CVType, stage: str, subject: str, roles: list[str], kinds: list[str]) -> str:
    subj, wanted = _subject_words(subject), _and(roles[:2])
    if t.id == "student" or stage == "student":
        looking = _and([KIND_WORDS[k] for k in kinds if k in ("internship", "placement", "insight")]) \
            or "paid internships and placement years"
        if subj:
            return f"You are studying {subj} and looking for {looking}."
        return f"You are a student looking for {looking}."
    if stage == "changing":
        return f"You are changing career into {wanted} roles." if wanted else "You are changing career."
    if stage == "experienced":
        return f"You have several years of work and are applying to {wanted} roles." if wanted \
            else "You have several years of work."
    if stage == "school_leaver":
        return "You have not done a degree yet, so school results, work and activities come first."
    who = f"{'an' if subj[:1].lower() in 'aeiou' else 'a'} {subj} graduate" if subj else "a graduate"
    return f"You are {who} applying to {wanted} roles." if wanted else f"You are {who}."


def suggest(goal: Any, doc: Any, *, subject: str = "") -> tuple[str, str]:
    """(type id, one sentence why) from the career stage, the subject studied and the roles wanted."""
    stage, roles, kinds = _goal_values(goal)
    subject = _subject(doc, subject)
    best, best_points = FALLBACK, 0
    for type_id in builtin_ids():
        rules = _builtin(type_id).suggest_rules
        at_stage = bool(stage) and stage in rules.get("stages", [])
        by_subject = bool(_hits(rules.get("subjects", []), subject))
        by_role = bool(_hits(rules.get("roles", []), " ".join(roles)))
        # A type made for one stage needs that stage; any other type needs the subject or the roles to fit.
        if not (at_stage if type_id in STAGE_TYPES else by_subject or by_role):
            continue
        points = STAGE_POINTS * at_stage + SUBJECT_POINTS * by_subject + ROLE_POINTS * by_role
        if points > best_points:
            best, best_points = type_id, points
    return best, _why(_builtin(best), stage, subject, roles, kinds)


def tone_words(t: CVType) -> str:
    """The type's tone as the Writing card shows it."""
    return TONES.get(t.writing.get("tone", "plain"), "Plain")

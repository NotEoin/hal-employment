"""Stand-in for `jobhunt.sites.notes`, installed only while the real module is missing.

It keeps the contract's names and shapes (`Note`, `NoteSet`, `for_page`, `used`, `site_key`, `add`) with the
notes in memory, so the engine hooks can be tested without the notes store. `install()` puts it in place for
the test modules that call it; once the real module exists it is always used and nothing here takes effect.
"""
from __future__ import annotations

import importlib
import itertools
import re
import sys
from dataclasses import asdict, dataclass, field

# The most specific scope first (the most specific active note of a kind wins).
SPECIFIC = {"job": 0, "tenant": 1, "site": 2, "ats": 3, "global": 4}
NOTES: list[Note] = []
USED: list[dict] = []
_ids = itertools.count(1)


@dataclass(frozen=True)
class Note:
    """One note about a site: what kind of hook it drives, where it applies, and the person's words."""

    id: str
    kind: str
    scope_kind: str
    scope_key: str
    cond: dict = field(default_factory=dict)
    act: dict = field(default_factory=dict)
    text: str = ""
    origin: str = "you"


@dataclass
class NoteSet:
    """The notes that apply to one page, most specific first."""

    notes: list[Note]

    def all(self, kind: str, **cond) -> list[Note]:
        """Every note of this kind whose conditions this page meets."""
        return [n for n in self.notes if n.kind == kind
                and all(n.cond.get(k) in (None, v) for k, v in cond.items())]

    def first(self, kind: str, **cond) -> Note | None:
        """The most specific note of this kind, or None."""
        found = self.all(kind, **cond)
        return found[0] if found else None

    def for_field(self, label: str) -> list[Note]:
        """The field notes whose label pattern matches this question."""
        return [n for n in self.notes if n.kind in ("field_transform", "field_answer", "field_skip")
                and re.search(n.act.get("label") or "^$", label or "", re.I)]

    def wire(self) -> list[dict]:
        """The shape sent to the helper with every command."""
        return [asdict(n) for n in self.notes]


def site_key(url: str) -> str:
    """The registrable domain; Workday and Greenhouse keep the employer (the engines' own rule)."""
    from jobhunt import browser

    return browser.site_key(url)


def _covers(note: Note, url: str, ats: str | None, job_id: str | None) -> bool:
    if note.scope_kind == "global":
        return True
    if note.scope_kind == "ats":
        return note.scope_key == ats
    if note.scope_kind in ("site", "tenant"):
        return note.scope_key == site_key(url)
    if note.scope_kind == "job":
        return url.startswith(note.scope_key) or note.scope_key == job_id
    return False


def for_page(url: str, *, ats: str | None = None, job_id: str | None = None, step: int | None = None) -> NoteSet:
    """The notes that apply to this page, most specific first."""
    found = [n for n in NOTES if _covers(n, url or "", ats, job_id)]
    return NoteSet(sorted(found, key=lambda n: SPECIFIC.get(n.scope_kind, 9)))


def used(note_id: str, job_id: str, ok: bool | None = None, detail: str = "") -> None:
    """Records that an engine used a note on a job."""
    USED.append({"note": note_id, "job": job_id, "ok": ok, "detail": detail})


def add(scope_kind: str, scope_key: str, kind: str, cond: dict, act: dict, text: str, *, said: str | None = None,
        origin: str = "you") -> str:
    """Adds a note and returns its id."""
    note = Note(f"n{next(_ids)}", kind, scope_kind, scope_key, dict(cond), dict(act), text, origin)
    NOTES.append(note)
    return note.id


def reset() -> None:
    """Forgets every note and use."""
    NOTES.clear()
    USED.clear()


def install() -> bool:
    """Puts this stand-in in place of `jobhunt.sites.notes` when that module is missing; True when it did."""
    import jobhunt.sites as package

    full = "jobhunt.sites.notes"
    if sys.modules.get(full) is sys.modules[__name__]:
        return True
    try:
        importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name != full:
            raise
        sys.modules[full] = sys.modules[__name__]
        package.notes = sys.modules[__name__]
        return True
    return False

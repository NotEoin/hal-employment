"""The person's goal: what they are looking for, read from the `goal.*` settings.

`current()` builds one frozen `Goal` from the live settings and keeps it until any setting changes, so
scoring ten thousand jobs reads the settings once. Goal versions are rows of `goal_versions`, written by the
settings store in the same transaction as the change; `restore` writes an old version's values back
as one new change, so going back is itself undoable.

Employer names are compared through `company_key`, which drops suffixes such as "Ltd" and "Group", so
"Acme Widgets Ltd" and "acme widgets" are the same employer.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobhunt.core import clock, settings
from jobhunt.core.settings import store
from jobhunt.find import kinds as kinds_mod

log = logging.getLogger(__name__)

REMOTE_UK = "Remote in the UK"
_SUFFIXES = re.compile(r"\b(ltd|limited|plc|inc|llc|group|holdings|technologies|technology|"
                       r"systems|uk|international|corporation|corp)\b")
STAGE_WORDS = {"student": "Student", "new_grad": "New graduate", "early": "Early career",
               "experienced": "Experienced", "changing": "Changing career", "school_leaver": "School leaver"}
PATTERN_WORDS = {"office": "In an office", "hybrid": "Hybrid", "remote": "Remote", "any": "Any pattern"}


def company_key(name: str | None) -> str:
    """An employer name normalised for matching: lower case, no suffixes, letters and digits only."""
    name = re.sub(r"\(.*?\)|https?://\S+", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", _SUFFIXES.sub("", name))


@dataclass(frozen=True)
class Place:
    """One place the person would work, with the names that count as it; rank 0 is the first choice."""

    name: str
    aliases: tuple[str, ...]
    rank: int

    @property
    def remote(self) -> bool:
        """True for the special place "Remote in the UK"."""
        return self.name.lower() == REMOTE_UK.lower()

    def pattern(self) -> str:
        """A regular expression matching the place or any of its aliases as whole words."""
        names = sorted({self.name, *self.aliases}, key=len, reverse=True)
        return "|".join(rf"\b{re.escape(n.lower())}\b" for n in names if n.strip())


@dataclass(frozen=True)
class Goal:
    """Everything about what the person wants that scoring reads, frozen for one goal version."""

    version: int
    stage: str
    study_status: str
    study_end: str | None
    kinds: frozenset[str]
    roles: tuple[str, ...]
    avoid_roles: tuple[str, ...]
    interests: tuple[str, ...]
    places: tuple[Place, ...]
    max_distance_miles: int
    pattern: str
    min_pay: int
    unpaid_ok: bool
    avoid_sectors: tuple[str, ...]
    avoid_employers: frozenset[str]
    experience_years: int
    has_clearance: bool
    agencies: str
    training_schemes: str
    training_providers: frozenset[str]
    training_ok_places: tuple[str, ...]
    mode: str

    def is_student(self, today: date | None = None) -> bool:
        """True while the person is at university or college and their course has not ended."""
        return kinds_mod.is_student(self.study_status, self.study_end, today or clock.local_day())

    def finished_year(self) -> int | None:
        """The year the person's course ends or ended, when they said."""
        return kinds_mod.finished_year(self.study_end)


def _aliases(lines: list[str]) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for line in lines or []:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        out[name.strip().lower()] = tuple(a.strip() for a in rest.split(",") if a.strip())
    return out


def _names(lines: list[str]) -> frozenset[str]:
    """Employer keys from a text list; "# Section" lines are headings, not employers."""
    return frozenset(company_key(line) for line in lines or [] if line.strip() and not line.lstrip().startswith("#"))


def _version() -> int:
    try:
        from jobhunt import db
        con = db.connect()
        try:
            row = con.execute("SELECT MAX(id) FROM goal_versions").fetchone()
        finally:
            con.close()
    except Exception as e:
        log.warning("goal version unreadable: %s", e)
        return 0
    return int(row[0] or 0)


def from_values(v: dict, version: int = 0) -> Goal:
    """A `Goal` from a dict of `goal.*` values (live settings, a goal version, or a test)."""
    aliases = _aliases(v.get("goal.place_aliases", []))
    places = tuple(Place(p, aliases.get(p.lower(), ()), i) for i, p in enumerate(v.get("goal.places", []) or []))
    return Goal(
        version=version, stage=v.get("goal.stage", "new_grad"), study_status=v.get("goal.study_status", "finished"),
        study_end=v.get("goal.study_end") or None, kinds=frozenset(v.get("goal.kinds", []) or []),
        roles=tuple(v.get("goal.roles", []) or []), avoid_roles=tuple(v.get("goal.avoid_roles", []) or []),
        interests=tuple(v.get("goal.interests", []) or []), places=places,
        max_distance_miles=int(v.get("goal.max_distance", 25) or 0), pattern=v.get("goal.pattern", "any"),
        min_pay=int(v.get("goal.min_pay", 0) or 0), unpaid_ok=bool(v.get("goal.unpaid_ok", False)),
        avoid_sectors=tuple(s.lower() for s in v.get("goal.avoid_sectors", []) or []),
        avoid_employers=_names(v.get("goal.avoid_employers", [])),
        experience_years=int(v.get("goal.experience_years", 0) or 0),
        has_clearance=bool(v.get("goal.has_clearance", False)), agencies=v.get("goal.agencies", "flag"),
        training_schemes=v.get("goal.training_schemes", "lower"),
        training_providers=_names(v.get("goal.training_providers", [])),
        training_ok_places=tuple(v.get("goal.training_ok_places", []) or []), mode=v.get("goal.mode", "actively"))


def stored_key(*prefixes: str) -> str:
    """The stored values under `prefixes` as one string, to tell whether they changed since the last read."""
    vals = store.values()
    return json.dumps({k: v for k, v in vals.items() if k.startswith(prefixes)}, sort_keys=True, default=str)


_memo: dict[str, Goal] = {}


def current() -> Goal:
    """The live goal, rebuilt only when a goal setting changes."""
    # keyed on the stored values, not `settings.cached`: that opens the database on every call to read the
    # revision, and scoring calls this once per job
    key = stored_key("goal.")
    found = _memo.get(key)
    if found is None:
        found = from_values(settings.get_many("goal."), _version())
        _memo.clear()
        _memo[key] = found
    return found


def kinds_words(goal: Goal) -> str:
    """The chosen kinds in words, in the order the settings list them."""
    return ", ".join(kinds_mod.LABELS[k] for k in kinds_mod.LABELS if k in goal.kinds) or "No kinds chosen"


def summary_rows(goal: Goal) -> list[tuple[str, str]]:
    """The rows of the goal card on Home: (label, value) in words."""
    places = ", ".join(p.name for p in goal.places) or "Anywhere in the UK"
    pay = f"{goal.min_pay:,} pounds a year" if goal.min_pay else "Any"
    if goal.unpaid_ok:
        pay += ", unpaid roles shown"
    return [
        ("Stage", STAGE_WORDS.get(goal.stage, goal.stage)),
        ("Looking for", kinds_words(goal)),
        ("Roles", ", ".join(goal.roles) or "Any role"),
        ("Places", places),
        ("Working pattern", PATTERN_WORDS.get(goal.pattern, goal.pattern)),
        ("Lowest pay", pay),
    ]


def defaults_for(stage: str) -> dict:
    """The goal values a career stage suggests; the caller asks before writing them."""
    from jobhunt.core.settings.catalog import goal as catalog
    from jobhunt.core.settings.catalog import scoring as scoring_catalog

    if stage not in catalog.KINDS_BY_STAGE:
        raise ValueError(f"unknown career stage {stage!r}; one of {', '.join(catalog.KINDS_BY_STAGE)}")
    return {"goal.kinds": list(catalog.KINDS_BY_STAGE[stage]),
            "goal.experience_years": catalog.YEARS_BY_STAGE.get(stage, 0),
            "goal.study_status": catalog.STUDY_BY_STAGE.get(stage, "finished"),
            "scoring.preset": scoring_catalog.PRESET_BY_STAGE[stage]}


def restore(version: int) -> int:
    """Writes an earlier goal version's values back as one change; returns the new goal version."""
    from jobhunt import db

    con = db.connect()
    try:
        row = con.execute("SELECT data FROM goal_versions WHERE id = ?", (version,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise LookupError(f"There is no goal version {version}.")
    data = json.loads(row[0])
    known = {k: v for k, v in data.items() if k.startswith(("goal.", "scoring.")) and _registered(k)}
    results = settings.set_many(known, by="you")
    versions = [r.goal_version for r in results if r.goal_version]
    return max(versions) if versions else _version()


def _registered(key: str) -> bool:
    try:
        item = settings.setting(key)
    except KeyError:
        return False
    return item.kind not in ("button", "link")


STARTERS = Path(__file__).resolve().parent / "starters"


def starters() -> dict[str, dict]:
    """The starter search terms by career area (`find/starters/<area>.toml`), for the goal step."""
    return {p.stem: tomllib.loads(p.read_text(encoding="utf-8")) for p in sorted(STARTERS.glob("*.toml"))}

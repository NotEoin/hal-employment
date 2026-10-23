"""The goal routes: the goal card and editor's values, its versions and restoring one,
then the setup guide's career-area starters, the quick questions and saving what Hal understood.

Restoring writes the old values as one new change, so it is itself in the history and undoable. The quick
questions use no AI: `POST /goal/quiz` turns the answers into "Here is what I understood" without writing
anything, the person corrects it, and only `POST /goal/understood` saves it, as one change. The goal chat is
not built, so the quiz is the only way in.

Every answer is checked by the settings registry itself, so the quiz cannot store a value the Settings page
would refuse. A career-area starter fills roles, interests and roles to avoid only where the person gave
none; for a new graduate or early-career person it also picks the scoring starting point, because the
technical preset hides any title that does not name a role, which suits programmers and no one else.
"""

from __future__ import annotations

import re

from jobhunt.core import settings
from jobhunt.core.settings.catalog import scoring as scoring_catalog
from jobhunt.core.settings.registry import SettingError
from jobhunt.find import goal as goal_mod
from jobhunt.find import kinds as kinds_mod
from jobhunt.web.router import BadRequest, Request, route

VERSIONS = 50               # versions listed; older ones stay in the database
TECH_STARTER = "technology"
# The scoring starting point a starter picks for these stages; other stages keep the stage's own preset.
STARTER_STAGES = ("new_grad", "early")
PATTERN_ANSWERS = {"office": "office", "in an office": "office", "a mix": "hybrid", "mix": "hybrid",
                   "hybrid": "hybrid", "home": "remote", "at home": "remote", "remote": "remote", "any": "any"}
SPLIT = re.compile(r"\s*(?:,|;|\n)\s*")
# Places may also be joined with words ("Leeds, then York or Hull"); roles may not, as in "learning and
# engagement officer".
PLACE_SPLIT = re.compile(r"\s*(?:,|;|\n|\band\b|\bthen\b|\bor\b)\s*", re.I)
# What the quiz may answer, and the goal setting each one is.
KEYS = {"stage": "goal.stage", "study_status": "goal.study_status", "study_end": "goal.study_end",
        "kinds": "goal.kinds", "roles": "goal.roles", "avoid": "goal.avoid_roles", "interests": "goal.interests",
        "places": "goal.places", "pattern": "goal.pattern", "min_pay": "goal.min_pay"}


@route("GET", "/api/v2/goal")
def goal(req: Request) -> dict:
    """The goal's values, its rows in words, its version and how hard the person is looking."""
    current = goal_mod.current()
    return {"values": settings.get_many("goal."), "rows": [list(r) for r in goal_mod.summary_rows(current)],
            "version": current.version, "mode": current.mode}


@route("GET", "/api/v2/goal/versions")
def versions(req: Request) -> list[dict]:
    """Every goal version, newest first: when, by whom and what changed."""
    rows = req.con.execute("SELECT id, at, by, summary FROM goal_versions ORDER BY id DESC LIMIT ?",
                           (VERSIONS,)).fetchall()
    return [dict(r) for r in rows]


@route("POST", "/api/v2/goal/versions/{version}/restore")
def restore(req: Request) -> dict:
    """Puts an earlier goal back; answers the new version's number."""
    try:
        version = int(req.params["version"])
    except ValueError as e:
        raise BadRequest("version must be a whole number.", field="version") from e
    return {"version": goal_mod.restore(version)}


@route("GET", "/api/v2/goal/starters")
def starters(req: Request) -> list[dict]:
    """The career areas the goal step offers as one-press starting points."""
    return [{"id": key, "name": area.get("label", key), "roles": area.get("roles", []),
             "interests": area.get("interests", []), "avoid": area.get("avoid_roles", [])}
            for key, area in goal_mod.starters().items()]


def _words(value, split: re.Pattern = SPLIT) -> list[str]:
    if value is None:
        return []
    parts = value if isinstance(value, list) else split.split(str(value))
    return list(dict.fromkeys(p.strip() for p in parts if str(p).strip()))


def _checked(key: str, value):
    """`value` as the registry would store it for `key`; a SettingError names the quiz's own field."""
    try:
        return settings.check(KEYS[key], value)
    except SettingError as e:
        raise BadRequest(str(e), field=key) from e


def _places_words(places: list[str]) -> str:
    if not places:
        return "Anywhere in the UK"
    if len(places) == 1:
        return places[0]
    rest = places[1:]
    later = rest[0] if len(rest) == 1 else ", ".join(rest[:-1]) + " and " + rest[-1]
    return f"{places[0]} first, then {later}"


def _preset(stage: str, starter: str | None) -> str | None:
    if stage in STARTER_STAGES and starter:
        return "technical_grad" if starter == TECH_STARTER else "humanities_grad"
    return scoring_catalog.PRESET_BY_STAGE.get(stage)


def understood_rows(u: dict) -> list[list[str]]:
    """"Here is what I understood" as (label, words) rows."""
    kinds = ", ".join(kinds_mod.LABELS[k] for k in kinds_mod.LABELS if k in u["kinds"]) or "No kinds chosen"
    rows = [["Roles", ", ".join(u["roles"]) or "Any role"]]
    if u["avoid"]:
        rows.append(["Avoid", ", ".join(u["avoid"])])
    rows += [["Places", _places_words(u["places"])],
             ["Pattern", goal_mod.PATTERN_WORDS.get(u["pattern"], u["pattern"])],
             ["Stage", goal_mod.STAGE_WORDS.get(u["stage"], u["stage"])],
             ["Looking for", kinds]]
    if u["study_status"] == "at_uni":
        rows.append(["Studying", "Yes" + (f", until {u['study_end']}" if u["study_end"] else "")])
    elif u["study_end"]:
        rows.append(["Course ended", u["study_end"]])
    rows.append(["Lowest pay", f"{u['min_pay']:,} pounds a year" if u["min_pay"] else "Any"])
    return rows


@route("POST", "/api/v2/goal/quiz")
def quiz(req: Request) -> dict:
    """The quick questions' answers as what Hal understood, for the person to check; nothing is saved."""
    answers = (req.body or {}).get("answers")
    if not isinstance(answers, dict):
        raise BadRequest("Send the answers.", field="answers")
    now = settings.get_many("goal.")
    stage = _checked("stage", answers.get("stage") or now["goal.stage"])
    starter_id = answers.get("starter") or None
    area = goal_mod.starters().get(starter_id or "", {}) if starter_id else {}
    if starter_id and not area:
        raise BadRequest("Hal does not know that career area.", field="starter")
    defaults = goal_mod.defaults_for(stage) if stage != now["goal.stage"] else {}
    roles = _words(answers.get("roles")) or list(area.get("roles", [])) or list(now["goal.roles"])
    pattern_in = str(answers.get("pattern") or now["goal.pattern"]).strip().lower()
    u = {
        "stage": stage,
        "study_status": _checked("study_status", answers.get("study_status") or defaults.get("goal.study_status")
                                 or now["goal.study_status"]),
        "study_end": _checked("study_end", str(answers.get("study_end") or now["goal.study_end"] or "").strip()),
        "kinds": _checked("kinds", answers.get("kinds") or defaults.get("goal.kinds") or now["goal.kinds"]),
        "roles": _checked("roles", roles),
        "avoid": _checked("avoid", _words(answers.get("avoid")) or list(area.get("avoid_roles", []))
                          or list(now["goal.avoid_roles"])),
        "interests": _checked("interests", _words(answers.get("interests")) or list(area.get("interests", []))
                              or list(now["goal.interests"])),
        "places": _checked("places", _words(answers.get("places"), PLACE_SPLIT) or list(now["goal.places"])),
        "pattern": _checked("pattern", PATTERN_ANSWERS.get(pattern_in, pattern_in)),
        "min_pay": _checked("min_pay", answers.get("min_pay") if answers.get("min_pay") not in (None, "")
                            else now["goal.min_pay"]),
    }
    preset = _preset(stage, starter_id)
    if preset and (starter_id or defaults):
        u["preset"] = preset
    return {"understood": u, "rows": understood_rows(u)}


@route("POST", "/api/v2/goal/understood")
def save_understood(req: Request) -> dict:
    """Saves what Hal understood, as the person corrected it, in one change; answers the new goal version."""
    u = (req.body or {}).get("understood")
    if not isinstance(u, dict):
        raise BadRequest("Send what Hal understood.", field="understood")
    unknown = set(u) - set(KEYS) - {"preset"}
    if unknown:
        raise BadRequest(f"Hal does not know {sorted(unknown)[0]!r}.", field=sorted(unknown)[0])
    values = {KEYS[k]: _checked(k, v) for k, v in u.items() if k in KEYS}
    if u.get("preset"):
        if u["preset"] not in dict(scoring_catalog.PRESETS):
            raise BadRequest("Hal does not know that starting point.", field="preset")
        values["scoring.preset"] = u["preset"]
    if "goal.stage" in values and values["goal.stage"] != settings.get("goal.stage"):
        years = goal_mod.defaults_for(values["goal.stage"])["goal.experience_years"]
        values.setdefault("goal.experience_years", years)
    session = req.session.id if req.session else None
    results = settings.set_many(values, by="you", session_id=session)
    versions = [r.goal_version for r in results if r.goal_version]
    return {"version": max(versions) if versions else goal_mod.current().version}

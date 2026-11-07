"""What the advert requires that the person plainly does not meet, and what they plainly do.

`blockers(requirements, person)` reads the advert's must-haves and conditions, with no AI, for three things
the person's settings and checked facts settle: a degree level above theirs, the right to work without
sponsorship when they need it, and more years of experience than they have. Each comes back as an
"ELIGIBILITY: ..." line for the top of "Check before sending"; a draft's rewrite never touches them.
`where_you_live_settles(text, person)` is the other way round: a requirement to live in or come to the
advert's town is met when the person lives there, will move, or is happy in an office.

Design decisions, each with its reason:

- Only what is plain: a requirement that allows "or equivalent", calls itself desirable, or names a lower
  degree too ("a BSc or MSc") is not a blocker, and nothing is said when the person's own level or years
  are unknown. An advert requiring a Master's or PhD was drafted for a BSc holder with no warning
  (2026-11-07), but a guess would warn people away from jobs they can apply for.
- The person's level is the highest of their checked education facts and the degree in their settings,
  read with `profile.merge.level`; years are `goal.experience_years` and are read from the advert with
  `find.rules.min_years`.
- A fit map called office attendance in London unmet for a person who lives in London (2026-11-07), so
  `where_you_live_settles` is checked before a location gap or a location eligibility note is shown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Degree levels in order (`profile.merge.level` names them); others say nothing about a degree requirement.
DEGREE_RANK = {"bachelor": 1, "master": 2, "pgce": 2, "doctorate": 3}
DEGREE_WORDS = {"bachelor": "a bachelor's degree", "master": "a Master's degree", "pgce": "a PGCE",
                "doctorate": "a PhD"}
SOFT = re.compile(r"\b(or equivalent|or similar|equivalent experience|or relevant experience|desirable|preferred|"
                  r"ideally|a plus|advantage|nice to have|working towards|studying for|or working)\b", re.I)
DEGREE_NAMES = re.compile(r"\b(ph ?d|d ?phil|doctorate|m(sc|a|res|phil|eng|st)|master'?s?|b(sc|a|eng)|"
                          r"bachelor'?s?|undergraduate|postgraduate|pgce)\b", re.I)
NO_SPONSOR = re.compile(r"right to work in the uk|(no|not|unable to|cannot|can't|do not|does not|will not|won't) "
                        r"(offer |provide |be able to )?(visa )?sponsor", re.I)
WHERE = re.compile(r"\b(based in|located in|live in|living in|within commut\w*|commutable|relocat\w*|on[- ]?site|"
                   r"in[- ]office|office[- ](attendance|based)|in the office|days? (a|per) week|hybrid|in person|"
                   r"attend (the|our) office)\b", re.I)
OFFICE = re.compile(r"\b(on[- ]?site|in[- ]office|office|in person|hybrid|days? (a|per) week)\b", re.I)


@dataclass(frozen=True)
class Person:
    """What eligibility is judged on: degree level, need for sponsorship, years, where they live and will go."""

    level: str = ""
    needs_sponsorship: bool = False
    years: int | None = None
    places: tuple[str, ...] = ()
    relocate: bool = False
    office: bool = False


def _highest(levels: list[str]) -> str:
    known = [lv for lv in levels if lv in DEGREE_RANK]
    return max(known, key=DEGREE_RANK.get) if known else ""


def person(con=None) -> Person:
    """The person as their settings and checked education facts say (Hal's database unless `con`)."""
    from contextlib import closing

    from jobhunt import db
    from jobhunt.core import settings
    from jobhunt.profile import merge, store
    from jobhunt.profile.writing import USED_STATES

    def checked(c) -> list[str]:
        return [" ".join(str(it["fields"].get(k) or "") for k in ("qualification", "subject"))
                for it in store.items(c, "education", hidden=False) if it["state"] in USED_STATES]

    if con is None:
        with closing(db.connect()) as own:
            quals = checked(own)
    else:
        quals = checked(con)
    get = settings.get
    quals.append(str(get("you.education.degree") or ""))
    places = tuple(p for p in (str(get("you.city") or "").strip(), str(get("you.location_line") or "").strip()) if p)
    years = get("goal.experience_years")
    return Person(level=_highest([merge.level(q) for q in quals if q.strip()]),
                  needs_sponsorship=get("you.right_to_work") == "need_sponsorship",
                  years=int(years) if isinstance(years, int) else None, places=places,
                  relocate=bool(get("you.relocate")), office=bool(get("you.office")))


def _degree(requirement: str, who: Person) -> str:
    from jobhunt.profile import merge

    asked = [merge.level(m.group(0)) for m in DEGREE_NAMES.finditer(requirement)]
    asked = [lv for lv in asked if lv in DEGREE_RANK]
    if not asked or not who.level:
        return ""
    lowest = min(asked, key=DEGREE_RANK.get)
    if DEGREE_RANK[lowest] <= DEGREE_RANK[who.level]:
        return ""
    return f"ELIGIBILITY: the advert requires {DEGREE_WORDS[lowest]} (\"{requirement}\"); your highest is " \
           f"{DEGREE_WORDS[who.level]}"


def _years(requirement: str, who: Person) -> str:
    from jobhunt.find import rules

    wanted = rules.min_years(requirement)
    if wanted is None or who.years is None or wanted <= who.years:
        return ""
    return f"ELIGIBILITY: the advert requires {wanted} years of experience (\"{requirement}\"); your settings " \
           f"say {who.years}"


def blockers(requirements: list[str], who: Person) -> list[str]:
    """The advert's must-haves the person plainly does not meet, as "ELIGIBILITY: ..." lines, each once."""
    out = []
    for requirement in (str(r).strip() for r in requirements if str(r).strip()):
        if SOFT.search(requirement):
            continue
        out += [found for found in (_degree(requirement, who), _years(requirement, who)) if found]
        if who.needs_sponsorship and NO_SPONSOR.search(requirement):
            out.append(f"ELIGIBILITY: the advert requires the right to work in the UK without sponsorship "
                       f"(\"{requirement}\"); your settings say you need sponsorship")
    return list(dict.fromkeys(out))


def where_you_live_settles(text: str, who: Person, towns: list[str], facts: str = "") -> bool:
    """True when `text` asks the person to live in or come to the advert's place and they plainly can: they
    will move, the place (`towns`) is where their settings or CV (`facts`) say they live, or it asks for office
    days and they are happy in an office."""
    if not WHERE.search(text or ""):
        return False
    if who.relocate:
        return True
    home = " ".join(who.places) + "\n" + (facts or "")
    if any(t and re.search(rf"\b{re.escape(t)}\b", home, re.I) for t in towns):
        return True
    return who.office and OFFICE.search(text) is not None and not re.search(r"\b(live|living|based|located)\b",
                                                                              text, re.I)

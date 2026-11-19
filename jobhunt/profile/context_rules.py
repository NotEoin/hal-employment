"""The contexts a card can have and the context a shaped item shows (`shown`); `profile.contexts` explains them.

Kept apart from `profile.contexts`, which reads and writes the store, because the store shapes every item with
`shown`: this module imports nothing that imports the store, so the imports run one way.

Design decisions, each with its reason:

- A course is degree work unless it is plainly a school qualification (`is_school`): GCSEs were called
  "degree work" in every prompt (2026-11-19). Only words that name a school qualification or a school count;
  anything else, a BTEC at a college among them, stays degree work, as a wrong "school" would belittle a
  degree in every letter.
- Hal's stored reading of a course is read again when it is the kind's own ("degree"), so cards stored before
  school was a context show it without rewriting their rows; the next sort stores it (`contexts.refresh`).
"""
from __future__ import annotations

import re

from jobhunt.profile import merge
from jobhunt.writing import stage

BY_HAL, BY_PERSON = "hal", "person"
CONTEXT_KINDS = ("job", "education", "project", "achievement", "certificate")   # as `tiers.TIERED_KINDS`
# The person's choice on a card, in the words the Check your record page shows; the keys are
# `writing.stage.CONTEXTS`, written out because `stage` is still loading when the profile package is.
CHOICE_WORDS = {"degree": "Part of my degree", "team": "University team project", "work": "At work",
                "personal": "In my own time", "school": "At school"}
BY_KIND = {"job": "work", "education": "degree"}   # unless the person says otherwise (or `is_school`)
# `merge.level` names: school qualifications, and the levels past school, which no school word overrides
# (a "BSc" at a "School of Computing" is a degree).
SCHOOL_LEVELS = {"gcse", "a_level"}
HIGHER_LEVELS = {"pgce", "doctorate", "master", "bachelor", "foundation", "access"}
# School qualifications `merge.level` does not name, as a qualification's words say them (`merge.text_key`):
# O levels, Scottish Nationals, Highers and Advanced Highers, the Irish Leaving and Junior Certificate, the
# International Baccalaureate, a high school diploma, and the BTEC Firsts and level 1 and 2 BTECs taken at school.
SCHOOL_QUALIFICATION = re.compile(r"\b(o levels?|national [345]s?|sqa nationals?|highers|advanced highers?|"
                                  r"sqa (higher|advanced higher)s?|leaving cert(ificate)?|junior cert(ificate)?|"
                                  r"junior cycle|international baccalaureate|ib diploma|ibdp|"
                                  r"(high|secondary) school|btec firsts?|btec level [12])\b")
LONE_HIGHER = re.compile(r"(advanced )?higher")    # a Scottish Higher said alone, as `merge.text_key` gives it
# A school as an education card names it; "school" alone is also a university's "School of Computing".
SCHOOL_PLACE = re.compile(r"\b(high school|secondary school|grammar school|sixth form)\b")


def is_school(fields: dict) -> bool:
    """True when an education card's qualification or institution is plainly a school's; unsure is False."""
    qualification = merge.text_key(str(fields.get("qualification") or ""))
    level = merge.level(qualification)
    if level in HIGHER_LEVELS:
        return False
    return level in SCHOOL_LEVELS or bool(SCHOOL_QUALIFICATION.search(qualification)) \
        or bool(LONE_HIGHER.fullmatch(qualification)) \
        or bool(SCHOOL_PLACE.search(merge.text_key(str(fields.get("institution") or ""))))


def default(item: dict) -> str:
    """The context a shaped `item` has by its kind alone: a job is work, a school qualification school, any
    other course degree work; "" for the other kinds."""
    if item["kind"] == "education" and is_school(item["fields"]):
        return stage.SCHOOL
    return BY_KIND.get(item["kind"], "")


def shown(item: dict, stored) -> dict:
    """The context a shaped `item` carries: {context, context_by, context_words}; `stored` holds its columns.

    The person's choice wins, then Hal's stored reading, then the kind's own (`default`); Hal's stored
    reading that is the kind's own is read again, so a school qualification stored as degree work shows
    school. A kind with no context has none.
    """
    if item["kind"] not in CONTEXT_KINDS:
        return {"context": "", "context_by": "", "context_words": ""}
    context, by = stored["context"], stored["context_by"]
    if context not in stage.CONTEXTS or by not in (BY_HAL, BY_PERSON) \
            or (by == BY_HAL and context == BY_KIND.get(item["kind"])):
        context = default(item)
        by = BY_HAL if context else ""
    return {"context": context, "context_by": by, "context_words": CHOICE_WORDS.get(context, "")}

"""Stand-in for `jobhunt.cvstudio.types`, installed only while the real module is missing.

Three of the seven built-in types, with the fields the suggestions read: section order and limits, writing rules,
checks and keywords. The values follow the built-in humanities type.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BANNED = ("passionate", "hard-working", "team player", "go-getter", "references available on request")


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
    keywords: dict = field(default_factory=dict)
    documents: dict = field(default_factory=dict)
    prompts: dict = field(default_factory=dict)


def _writing(**over) -> dict:
    return {"person": "none", "style": "bullets", "bullet_words": [8, 30], "verb_first": True,
            "results": "where_have", "spelling": "en-GB", "tone": "plain", "banned": list(BANNED), **over}


TYPES = {
    "humanities_grad": CVType(
        "humanities_grad", "Humanities and social science graduate",
        "Recent graduates in history, politics, English, languages and similar",
        "Shows the dissertation and strong modules. Short paragraphs or bullets.", 2,
        ("summary", "education", "volunteering", "experience", "skills", "interests"),
        {"summary": {"title": "Profile", "max_words": 70},
         "education": {"title": "Education", "max_entries": 2, "show_modules": "strong", "min_mark": 65,
                       "dissertation": True},
         "volunteering": {"title": "Volunteering", "max_entries": 3, "max_bullets": 3},
         "experience": {"title": "Work experience", "max_entries": 4, "max_bullets": 3},
         "skills": {"title": "Skills", "style": "groups"},
         "interests": {"title": "Interests", "optional": True}},
        _writing(),
        {"profile_evidence": 2, "result_bullets": 2, "dissertation_shown": 1, "dates_one_format": 1,
         "no_personal_data": 2, "pages_within": 1, "one_spelling": 1, "entry_says_what": 1, "contact_at_top": 1},
        {"skills": ["research", "archival research", "report writing", "Excel", "NVivo"]}),
    "technical_grad": CVType(
        "technical_grad", "Technical graduate", "Graduates in computing, engineering and the sciences",
        "Projects with evidence come early. One page preferred.", 1,
        ("summary", "education", "projects", "experience", "skills"),
        {"summary": {"title": "Profile", "max_words": 60}, "education": {"title": "Education", "max_entries": 2},
         "projects": {"title": "Projects", "max_entries": 4, "max_bullets": 3},
         "experience": {"title": "Experience", "max_entries": 3, "max_bullets": 3},
         "skills": {"title": "Skills", "style": "groups"}},
        _writing(), {"profile_evidence": 2, "result_bullets": 2, "one_spelling": 1}),
    "career_changer": CVType(
        "career_changer", "Career changer", "People moving into a new kind of work",
        "Skills first, each backed by an example from earlier jobs.", 2,
        ("summary", "skills", "experience", "education"),
        {"summary": {"title": "Profile", "max_words": 70}, "skills": {"title": "Key skills"},
         "experience": {"title": "Experience", "max_entries": 4, "max_bullets": 3},
         "education": {"title": "Education", "max_entries": 2}},
        _writing(), {"profile_evidence": 2, "skills_backed": 2}),
}


def load(type_id: str) -> CVType:
    """One type by id; LookupError when there is none."""
    try:
        return TYPES[type_id]
    except KeyError:
        raise LookupError(f"There is no CV type {type_id!r}.") from None


def all_types() -> list[CVType]:
    """Every type."""
    return list(TYPES.values())


def suggest(goal, doc) -> tuple[str, str]:
    """A type for this goal, with one sentence why."""
    return "humanities_grad", "You are a graduate applying to research and heritage roles."

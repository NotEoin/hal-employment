"""Which kind of job a document is for, and the conventions of that field the writer should follow.

`resolve(title, advert, person)` reads the advert first and the person's goal and background second, by
counting the words of each field in `writing.rules.FIELDS`. `conventions(resolved, kind)` gives the lines a
prompt receives with the facts. No AI is asked: the same advert always gets the same field, and a field
Hal cannot tell is the general one, never a guess.

Design decisions, each with its reason:

- The conventions go in the request, not the prompt: they depend on the job, and a prompt reads the same
  for every job of one person (see `cvstudio.context`).
- The advert decides the field; the person's background only adds its own advice when it differs (a
  humanities graduate applying to the Civil Service gets the Civil Service conventions and the advice to
  name transferable skills with a concrete piece of work).
- A title word counts more than a word in the body, since a title names the job and a body mentions many
  things in passing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobhunt.writing import rules

TITLE_WEIGHT = 3     # a field word in the advert's title counts as this many in its body
MIN_SCORE = 2        # fewer points than this and the advert does not show its field
# The built-in CV types whose people come from one field; the others say nothing about a field.
TYPE_FIELDS = {"humanities_grad": "humanities", "creative": "arts", "academic": "academia",
               "technical_grad": "tech"}


@dataclass(frozen=True)
class Resolved:
    """The field a document is written for, and the person's own field when it is a different one."""

    field: str = rules.GENERAL.id
    background: str = ""

    @property
    def name(self) -> str:
        """The field's name in words."""
        return rules.BY_FIELD[self.field].name


def _pattern(word: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(word)}\b", re.I)


_PATTERNS = {f.id: [_pattern(w) for w in f.words] for f in rules.FIELDS}


def scores(title: str, text: str) -> dict[str, int]:
    """Points per field for an advert or a goal: each field word found, title words counted more."""
    out = {}
    for field_id, patterns in _PATTERNS.items():
        points = sum(TITLE_WEIGHT * bool(p.search(title or "")) + bool(p.search(text or "")) for p in patterns)
        if points:
            out[field_id] = points
    return out


def _best(found: dict[str, int]) -> str:
    if not found:
        return ""
    field_id, points = max(found.items(), key=lambda kv: (kv[1], kv[0]))
    return field_id if points >= MIN_SCORE else ""


def person_field(person: dict) -> str:
    """The field the person comes from or aims at: their CV type, else their roles and subject."""
    typed = TYPE_FIELDS.get(str(person.get("cv_type_id") or ""))
    if typed:
        return typed
    words = " ".join([str(person.get("target_roles") or ""), str(person.get("field_line") or "")])
    return _best(scores(words, ""))


def resolve(title: str, advert: str, person: dict | None = None) -> Resolved:
    """The field of a job from its advert, else the general field; the person's own field is only background.

    The advert decides: a computing graduate's letter for a complaints role came out in the tech field's
    voice when an advert with no clear field fell back to the person's (2026-11-05).
    """
    own = person_field(person or {})
    if not (title or advert).strip():
        # no job at all (a CV looked over on its own): the person's field is the only one there is
        return Resolved(own or rules.GENERAL.id, "")
    found = _best(scores(title, advert)) or rules.GENERAL.id
    return Resolved(found, own if own and own != found else "")


def person_now() -> dict:
    """What the person's field is read from, from their settings: CV type, roles and subject."""
    from jobhunt.core import settings
    from jobhunt.cvstudio import context
    from jobhunt.find import goal as goal_mod

    kind = context.cv_type()
    subject = (settings.get("you.education.subject") or "").strip()
    return {"cv_type_id": kind.id if kind is not None else "", "target_roles": ", ".join(goal_mod.current().roles),
            "field_line": subject}


def for_job(job, person: dict | None = None) -> Resolved:
    """The field of one job, from its advert and the person (their settings when `person` is not given)."""
    def value(key: str) -> str:
        try:
            return str(job[key] or "")
        except (KeyError, IndexError, TypeError):
            return ""

    return resolve(value("title"), value("description"), person if person is not None else person_now())


def conventions(resolved: Resolved, kind: str) -> list[str]:
    """The conventions for `kind` (letter, cv or answer) in this field, with the background's own advice."""
    field = rules.BY_FIELD.get(resolved.field, rules.GENERAL)
    lines = list(getattr(field, kind, ()) or ()) or list(getattr(rules.GENERAL, kind, ()))
    if resolved.background and resolved.background in rules.BY_FIELD:
        lines += list(rules.BY_FIELD[resolved.background].background)
    return lines


def block(resolved: Resolved, kind: str) -> str:
    """The conventions as a request section: a heading naming the field, then one line each."""
    lines = conventions(resolved, kind)
    if not lines:
        return ""
    return f"Conventions for this kind of job ({resolved.name}):\n" + "\n".join(f"- {line}" for line in lines)

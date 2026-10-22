"""Three made-up people for tests: a humanities graduate, a technical graduate and a career changer.

Each person is a set of `you.*` and `goal.*` settings plus a LEAKS list: words that belong to
other kinds of career and must never appear in this person's prompts. `as_person(p)` saves the settings in
the current temporary home for the length of a block. Every name, place of study, employer and address
here is invented; the email addresses use the reserved `example.org` domain.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field

from jobhunt.core import settings


@dataclass(frozen=True)
class Person:
    """One made-up person: who they are (`you`), what they look for (`goal`) and what must not leak."""

    key: str
    you: dict
    goal: dict
    leaks: tuple[str, ...]
    names: tuple[str, ...] = field(default=())

    @property
    def settings(self) -> dict:
        """Every setting this person has, by key."""
        return {**{f"you.{k}": v for k, v in self.you.items()}, **{f"goal.{k}": v for k, v in self.goal.items()}}


HUMANITIES = Person(
    key="humanities_grad",
    you={"name": "Marnie Quillfeather", "preferred_name": "Marnie", "email": "marnie.quillfeather@example.org",
         "phone": "07700 900461", "city": "Harrowgate Vale",
         "education.degree": "BA (Hons)", "education.subject": "History and Imaginary Studies",
         "education.university": "University of Nowhere Fen", "education.grade": "2:1"},
    goal={"stage": "new_grad", "roles": ["museum assistant", "archive trainee"], "study_end": "2026-06"},
    leaks=("modules by title", "engineering lead", "engineering manager", "coding practice", "software engineer",
           "computer science", "KTP"),
    names=("Marnie", "Quillfeather"),
)
TECHNICAL = Person(
    key="technical_grad",
    you={"name": "Tobias Wrenfield", "preferred_name": "Toby", "email": "toby.wrenfield@example.org",
         "phone": "07700 900462", "city": "Lowmere",
         "education.degree": "BSc (Hons)", "education.subject": "Software Studies",
         "education.university": "Made-up Institute of Technology", "education.grade": "First"},
    goal={"stage": "new_grad", "roles": ["software developer", "data analyst"], "study_end": "2026-07"},
    leaks=("museum", "archive", "retail", "store manager", "dissertation on"),
    names=("Tobias", "Toby", "Wrenfield"),
)
CHANGER = Person(
    key="career_changer",
    you={"name": "Priya Ashdown-Vell", "preferred_name": "Priya", "email": "priya.ashdown@example.org",
         "phone": "07700 900463", "city": "Saltmarsh Cross"},
    goal={"stage": "changing", "roles": ["project coordinator"], "study_status": "none"},
    leaks=("modules by title", "engineering lead", "coding practice", "computer science", "graduated in", "KTP"),
    names=("Priya", "Ashdown-Vell"),
)
PEOPLE = (HUMANITIES, TECHNICAL, CHANGER)


@contextlib.contextmanager
def as_person(person: Person) -> Iterator[Person]:
    """`person`'s settings are saved for the length of the block, then put back to their defaults."""
    keys = list(person.settings)
    try:
        for key, value in person.settings.items():
            settings.set(key, value, by="setup")
        yield person
    finally:
        for key in keys:
            settings.reset(key, by="setup")

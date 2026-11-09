"""Courses and modules from a dump's folders: the courses a sort proposes as education cards, and the module
each lecture or coursework row belongs to, so the review shows one row a module.

Coursework and lecture material need their course's education card before they can be added
(`sorter.needs_card`). On a real 10,000-file dump (2026-11-09) no card existed yet, so all 376 coursework and
6,417 lecture rows waited, one row a file. The folders already name the courses: a degree folder ("BSc
Computing"), year folders ("Year 2") and module folders ("Databases - CS2041", "HIS2040 Archival Methods").

Design decisions, each with its reason:

- A course is found from a row's folders (`course_of`), top down: the first folder named as a qualification
  (`merge.level`, or `QUALIFICATION`); else the folder holding year folders (`YEAR_FOLDER`); else the folder
  holding module folders (a module code, `merge.module_code`), passing over a folder that only holds them
  ("Modules", `CONTAINER`). The folder sorted counts as the course only when nothing below it names one.
- Courses are grouped by their name, so one degree split over two places in a dump is one proposal with all
  its modules (`courses_found`). It is only a proposal: the person accepts or renames it, and only then is a
  card made (`sorts.accept_course`).
- A card made from folders needs where or when it was studied (`profile.store`), and the folders rarely name
  the institution, so its start and end are the months of its oldest and newest files: dates Hal computes,
  never guesses.
- A row's module group (`group_key`) is its course's folder and module folder; with no module folder, the
  file's own folder. The review shows one row a group, accepted together.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from jobhunt.profile import merge

QUALIFICATION = re.compile(r"^(?:access|foundation|pg ?dip|pg ?cert|hn[cd]|btec|diploma|certificate|degree)\b",
                           re.I)
YEAR_FOLDER = re.compile(r"^(?:year|stage|level|semester|term)[ _-]?\d", re.I)
MODULE_DASH = re.compile(r"^(?P<name>.+?)\s+-\s+(?P<code>[A-Za-z]{2,5}\s?\d{3,5}[A-Za-z]?)$")
UNI = re.compile(r"(?<![a-z])(?:uni|universit(?:y|ies)|college|school|academy|polytechnic|conservatoire)"
                 r"(?![a-z])", re.I)
# A folder that only holds the modules, which names no course: the course is the folder above it.
CONTAINER = re.compile(r"^(?:my )?(?:modules?|courses?|units?|subjects?|classes|lectures?|coursework|study|"
                       r"studies)$", re.I)


@dataclass(frozen=True)
class Course:
    """The course a row's folders name: its folder (`key`), the card's words, and the row's module with its
    folder (`module_key`)."""

    key: str
    qualification: str = ""
    subject: str = ""
    institution: str = ""
    module: str = ""
    module_key: str = ""


def _qualification(name: str) -> bool:
    return bool(merge.level(name) or QUALIFICATION.match(name.strip())) and not merge.module_code(name)


def _module(name: str) -> str:
    """A module folder's name as a module ("CS2041 Databases"), or "" when it names no module."""
    if m := MODULE_DASH.match(name.strip()):
        return f"{m['code'].upper().replace(' ', '')} {m['name'].strip()}"
    return name.strip() if merge.module_code(name) and not _qualification(name) else ""


def _module_in(parts: tuple[str, ...], start: int) -> dict:
    """The first module folder in `parts` from `start`, as Course fields; {} with none."""
    for j in range(start, len(parts)):
        if module := _module(parts[j]):
            return {"module": module, "module_key": "/".join(parts[:j + 1])}
    return {}


def course_of(rel: str) -> Course | None:
    """The course a row at `rel` (starting with the folder sorted) is in, by its folders; None when they name
    none."""
    parts = PurePosixPath(rel).parent.parts
    for i, part in enumerate(parts[1:], 1):
        if _qualification(part):
            first, _, rest = part.strip().partition(" ")
            qualification, subject = (first, rest.strip()) if merge.level(first) or QUALIFICATION.match(first) \
                else (part.strip(), "")
            return Course("/".join(parts[:i + 1]), qualification, subject, **_module_in(parts, i + 1))
    for i, part in enumerate(parts):
        if i and (YEAR_FOLDER.match(part.strip()) or _module(part)):
            top = i - 1
            while top > 0 and CONTAINER.match(parts[top].strip()):
                top -= 1
            name = parts[top]
            words = {"institution": name} if UNI.search(name) else {"subject": name}
            return Course("/".join(parts[:top + 1]), **words, **_module_in(parts, i))
    return None


def group_key(rel: str) -> str:
    """The folder a row at `rel` is grouped by in the review: its course and module, else its own folder."""
    course = course_of(rel)
    return course.module_key if course and course.module_key else str(PurePosixPath(rel).parent)


def _title(c: Course) -> str:
    return " ".join(p for p in (c.qualification, c.subject, c.institution) if p)


def courses_found(rows: Iterable[tuple[str, str]]) -> list[dict]:
    """The courses that rows waiting for a card name, from (rel, ISO mtime) pairs: one a name, with its
    folders (`keys`), modules, file count and the months of its oldest and newest files."""
    out: dict[str, dict] = {}
    for rel, mtime in rows:
        c = course_of(rel)
        if c is None:
            continue
        found = out.setdefault(merge.text_key(_title(c)), {
            "key": c.key, "keys": [], "qualification": c.qualification, "subject": c.subject,
            "institution": c.institution, "modules": [], "files": 0, "start": "", "end": ""})
        if c.key not in found["keys"]:
            found["keys"].append(c.key)
        if c.module and c.module not in found["modules"]:
            found["modules"].append(c.module)
        found["files"] += 1
        month = (mtime or "")[:7]
        if month:
            found["start"] = min(found["start"] or month, month)
            found["end"] = max(found["end"], month)
    for found in out.values():
        found["title"] = " ".join(p for p in (found["qualification"], found["subject"], found["institution"]) if p)
        found["modules"].sort()
    return sorted(out.values(), key=lambda f: (-f["files"], f["title"]))

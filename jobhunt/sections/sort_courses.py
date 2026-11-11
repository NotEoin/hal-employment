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
- An academic-year folder ("2023-24", "2024-2025", "Year 1", "Semester 2", "Term 1", `year_folder`) is never
  a course, and is passed over like a container: a real dump's "2023-24", "2024-25" and "2025-26" were
  proposed as three courses (2026-11-09).
- Courses are grouped by their normalised name (`course_words`: no case, punctuation, "(Hons)" or
  institution words, and "BsC" or "B.Sc." read as "BSc"), so one degree split over several places is one
  proposal with all its modules (`courses_found`). A name that only adds up to `PLACE_WORDS_MAX` words at its
  end ("Computer Science BsC Newcastle" beside "Computer Science BSc") is the same course, as those words
  name where it was studied; one adding "and" or "with" is a joint degree and stays apart.
- A course named by no qualification (the folder holding year or module folders) joins the one qualification
  the dump's folders name; with none or several, the first of `known` (the settings, then a transcript, then
  a CV in the sort). A course that matches a known qualification is named from it ("BSc (Hons) Computer
  Science, Newcastle University"): the person's settings and a transcript outrank folder names. It is only a
  proposal: the person accepts or renames it, and only then is a card made (`sorts.accept_course`).
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
# "Master" alone names a main copy or a notes vault ("obsidian/Master" in a real dump, 2026-11-09); a master's
# degree's folder says "Masters", "MSc" or "MA".
NOT_QUALIFICATION = re.compile(r"^master$", re.I)
YEAR_FOLDER = re.compile(r"^(?:year|stage|level|semester|term)[ _-]?\d", re.I)
ACADEMIC_YEAR = re.compile(r"^(?:academic[ _-]?year[ _-]?)?(?:19|20)\d\d[ _/-]+(?:\d\d|(?:19|20)\d\d)$", re.I)
# Degree abbreviations as written in folder names, by their letters without dots or case ("B.Sc.", "BsC").
DEGREES = {"bsc": "BSc", "ba": "BA", "beng": "BEng", "bed": "BEd", "bmus": "BMus", "llb": "LLB", "msc": "MSc",
           "ma": "MA", "meng": "MEng", "mres": "MRes", "mphil": "MPhil", "mba": "MBA", "phd": "PhD",
           "pgce": "PGCE", "hnd": "HND", "hnc": "HNC"}
HONS = re.compile(r"\(?\b(?:hons|honours)\b\)?", re.I)
NAME_FILLER = {"in", "of", "the"}
WORDS = ("qualification", "subject", "institution")
JOINT = {"and", "with"}      # "Computer Science and Maths" is another degree than "Computer Science"
PLACE_WORDS_MAX = 2            # "Newcastle", "Fenwick Vale": a place's name, not another subject
# A degree in a CV's or transcript's text: "BSc (Hons) Computer Science", "BA History". Capitals only, so
# "ma" or "ba" inside a sentence is no degree.
DEGREE_IN_TEXT = re.compile(r"(?<![A-Za-z])(?P<q>B\.?Sc|B\.?A|B\.?Eng|LLB|M\.?Sc|M\.?A|M\.?Eng|M\.?Res|M\.?Phil|MBA|"
                            r"Ph\.?D|PGCE|HN[CD])\.?(?P<hons>\s*\((?:Hons|Honours|hons)\))?\s+(?:in\s+)?"
                            r"(?P<s>[A-Z][A-Za-z]+(?:\s+(?:and\s+|&\s+|of\s+|with\s+)?[A-Z][A-Za-z]+)*)")
INSTITUTION_IN_TEXT = re.compile(r"(?:University|College) of [A-Z][A-Za-z]+(?:\s+(?:upon\s+)?[A-Z][A-Za-z]+)*|"
                                 r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+(?:University|College)")
MODULE_DASH = re.compile(r"^(?P<name>.+?)\s+-\s+(?P<code>[A-Za-z]{2,5}\s?\d{3,5}[A-Za-z]?)$")
CODE_DASH = re.compile(r"^(?P<code>[A-Za-z]{2,5}\s?\d{3,5}[A-Za-z]?)\s+-\s+(?P<name>.+)$")
MODULE_CODES = re.compile(r"\b[A-Za-z]{2,5}\s?\d{3,5}\b")
# A folder named with a student number ("c3046604 CS1032 practical report") holds one piece of work, not a module
STUDENT_NUMBER = re.compile(r"\b[a-z]?\d{7,9}\b", re.IGNORECASE)
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


def year_folder(name: str) -> bool:
    """True when a folder is named as a year of study: "2023-24", "2024-2025", "Year 1", "Semester 2"."""
    return bool(YEAR_FOLDER.match(name.strip()) or ACADEMIC_YEAR.match(name.strip()))


def _qualification(name: str) -> bool:
    return bool(merge.level(name) or QUALIFICATION.match(name.strip())) and not merge.module_code(name) \
        and not NOT_QUALIFICATION.match(name.strip()) and not year_folder(name)


def _degree(word: str) -> str:
    """A word as a degree abbreviation ("BSc"), or ""."""
    return DEGREES.get(word.replace(".", "").lower(), "")


def split_qualification(name: str) -> tuple[str, str]:
    """A qualification folder's name as (qualification, subject): "Computer Science BsC (Hons)" is
    ("BSc (Hons)", "Computer Science"), "Access Humanities" is ("Access", "Humanities")."""
    name = " ".join(name.split())
    first, _, rest = name.partition(" ")
    if not _degree(first) and (merge.level(first) or QUALIFICATION.match(first)):
        return first, rest.strip()
    words = HONS.sub(" ", name).split()
    found = next((_degree(w) for w in words if _degree(w)), "")
    if not found:
        return name, ""
    hons = " (Hons)" if HONS.search(name) else ""
    return found + hons, " ".join(w for w in words if not _degree(w))


def course_words(qualification: str, subject: str, places: Iterable[str] = ()) -> tuple[str, tuple[str, ...]]:
    """A course as compared: its degree ("bsc", else its level) and its subject's words, without case,
    punctuation, "(Hons)", filler or the words of an institution (`UNI`, or any of `places`)."""
    drop = {w for place in places for w in merge.text_key(place).split()}
    text = HONS.sub(" ", f"{qualification} {subject}").replace(".", "")
    words = merge.text_key(text).split()
    degree = next((w for w in words if w in DEGREES), "") or merge.level(text) or \
        next(iter(merge.text_key(HONS.sub(" ", qualification)).split()), "")
    return degree, tuple(w for w in words if w not in DEGREES and w not in NAME_FILLER and w not in drop
                         and not UNI.fullmatch(w) and not QUALIFICATION.fullmatch(w))


def same_course(a: tuple[str, tuple[str, ...]], b: tuple[str, tuple[str, ...]]) -> bool:
    """True when two `course_words` are one course: equal, or one adds only a place's name at its end."""
    if a[0] != b[0] or not a[0]:
        return False
    short, long = sorted((a[1], b[1]), key=len)
    extra = long[len(short):]
    return bool(short) and long[:len(short)] == short and len(extra) <= PLACE_WORDS_MAX \
        and not JOINT & set(extra)


def qualification_in(text: str) -> dict | None:
    """The first degree a CV's or transcript's text names, as {"qualification", "subject", "institution"};
    None when it names none."""
    m = DEGREE_IN_TEXT.search(text or "")
    if m is None:
        return None
    subject = re.split(r"\s+(?:University|College)\b", m["s"])[0].strip()
    line_end = text.find("\n", m.end())
    place = INSTITUTION_IN_TEXT.search(text[m.end():line_end if line_end >= 0 else len(text)]) or \
        INSTITUTION_IN_TEXT.search(text)
    hons = " (Hons)" if m["hons"] else ""
    return {"qualification": _degree(m["q"]) + hons, "subject": subject,
            "institution": place.group().strip() if place else ""}


def _module(name: str) -> str:
    """A module folder's name as a module ("CS2041 Databases"), or "" when it names no module."""
    if m := MODULE_DASH.match(name.strip()):
        return f"{m['code'].upper().replace(' ', '')} {m['name'].strip()}"
    if m := CODE_DASH.match(name.strip()):
        return f"{m['code'].upper().replace(' ', '')} {m['name'].strip()}"
    if not merge.module_code(name) or _qualification(name) or STUDENT_NUMBER.search(name):
        return ""
    return name.strip()


def _one_a_code(modules: list[str]) -> list[str]:
    """`modules` with one name a module code: the longest, so a bare "CSC2031" folds into "CSC2031 Security
    Programming"; names with no single code are kept as they are."""
    best: dict[str, str] = {}
    out = []
    for name in modules:
        code = merge.module_code(name)
        if not code or name.count(code) != 1 or len(MODULE_CODES.findall(name)) > 1:
            out.append(name)
        elif code not in best or len(name) > len(best[code]):
            best[code] = name
    return sorted(out + list(best.values()))


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
            qualification, subject = split_qualification(part)
            return Course("/".join(parts[:i + 1]), qualification, subject, **_module_in(parts, i + 1))
    for i, part in enumerate(parts):
        if i and (year_folder(part) or _module(part)):
            top = i - 1
            while top > 0 and (CONTAINER.match(parts[top].strip()) or year_folder(parts[top])):
                top -= 1
            name = parts[top]
            words = {} if year_folder(name) else {"institution": name} if UNI.search(name) else {"subject": name}
            return Course("/".join(parts[:top + 1]), **words, **_module_in(parts, i))
    return None


def group_key(rel: str) -> str:
    """The folder a row at `rel` is grouped by in the review: its course and module, else its own folder."""
    course = course_of(rel)
    return course.module_key if course and course.module_key else str(PurePosixPath(rel).parent)


def _title(words: dict) -> str:
    """A course's name as the review shows it: "BSc (Hons) Computer Science, Newcastle University"."""
    name = " ".join(p for p in (words.get("qualification"), words.get("subject")) if p)
    return ", ".join(p for p in (name, words.get("institution")) if p)


def _add(found: dict, c: Course, mtime: str) -> None:
    if c.key not in found["keys"]:
        found["keys"].append(c.key)
    if c.module and c.module not in found["modules"]:
        found["modules"].append(c.module)
    found["files"] += 1
    month = (mtime or "")[:7]
    if month:
        found["start"] = min(found["start"] or month, month)
        found["end"] = max(found["end"], month)


def _join(into: dict, other: dict) -> None:
    """Adds course `other`'s folders, modules, files and months to `into`."""
    into["keys"] += [k for k in other["keys"] if k not in into["keys"]]
    into["modules"] += [m for m in other["modules"] if m not in into["modules"]]
    into["files"] += other["files"]
    for side, pick in (("start", min), ("end", max)):
        months = [m for m in (into[side], other[side]) if m]
        into[side] = pick(months) if months else ""



def _by_folder(rows: Iterable[tuple[str, str]]) -> list[dict]:
    """The courses rows name, one a name as their folders give it, most files first."""
    folders: dict[str, dict] = {}
    for rel, mtime in rows:
        c = course_of(rel)
        if c is None:
            continue
        words = {"qualification": c.qualification, "subject": c.subject, "institution": c.institution}
        found = folders.setdefault(merge.text_key(_title(words)) or c.key, {
            "key": c.key, "keys": [], **words, "modules": [], "files": 0, "start": "", "end": ""})
        _add(found, c, mtime)
    return sorted(folders.values(), key=lambda f: (-f["files"], f["key"]))


def _merge_named(folders: list[dict], places: list[str]) -> list[dict]:
    """The courses that name a qualification, those of one normalised name merged (`same_course`)."""
    named: list[dict] = []
    for found in (f for f in folders if f["qualification"]):
        key = course_words(found["qualification"], found["subject"], places)
        into = next((n for n in named if same_course(n["_words"], key)), None)
        if into is None:
            named.append({**found, "_words": key})
            continue
        _join(into, found)
        into["institution"] = into["institution"] or found["institution"]
        # the shorter name is the one without a place's words added
        if len(found["subject"].split()) < len(into["subject"].split()):
            into.update(qualification=found["qualification"], subject=found["subject"], _words=key)
    return named


def _home_of_unnamed(named: list[dict], unnamed: list[dict], joins: list[dict], join_words: list) -> dict | None:
    """The course that folders naming no qualification join: the only one named, else the first of `joins`
    (one named, or a new one from the first unnamed folder); None when nothing says which."""
    if len(named) == 1:
        return named[0]
    if not joins:
        return None
    if named:
        return next((n for n in named if same_course(n["_words"], join_words[0])), None)
    return {**unnamed.pop(0), **{w: joins[0].get(w, "") for w in WORDS}}


def courses_found(rows: Iterable[tuple[str, str]], known: Iterable[dict] = (),
                  cvs: Iterable[dict] = ()) -> list[dict]:
    """The courses that rows waiting for a card name, from (rel, ISO mtime) pairs: one a course, with its
    folders (`keys`), modules, file count and the months of its oldest and newest files.

    `known` are qualifications from outside the folders, best first, each {"qualification", "subject",
    "institution"}: the person's settings, then a transcript. They name a course they match. With `cvs`
    after them, they take the modules of folders that name no qualification (see the module notes)."""
    known = [k for k in known if k.get("qualification")]
    joins = known + [k for k in cvs if k.get("qualification")]
    folders = _by_folder(rows)
    places = [f["institution"] for f in (*joins, *folders) if f.get("institution")]
    named = _merge_named(folders, places)
    join_words = [course_words(k["qualification"], k.get("subject", ""), places) for k in joins]
    for found in named:
        names = (k for k, w in zip(known, join_words, strict=False) if same_course(found["_words"], w))
        if match := next(names, None):
            found.update({w: match.get(w, "") for w in WORDS})
    unnamed = [f for f in folders if not f["qualification"]]
    out = list(named)
    if unnamed:
        home = _home_of_unnamed(named, unnamed, joins, join_words)
        if home is not None and all(home is not n for n in named):
            out.append(home)
        for found in unnamed:
            if home is None:
                out.append(found)
            else:
                _join(home, found)
    for found in out:
        found.pop("_words", None)
        found["title"] = _title(found)
        found["modules"] = _one_a_code(found["modules"])
    return sorted(out, key=lambda f: (-f["files"], f["title"]))

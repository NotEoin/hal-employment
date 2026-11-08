"""Where the person is today, as one plain line every writing request carries, and the kind of each project.

`current(con, today)` works out the person's stage from the settings and the education facts they checked:
"Finished BSc Computer Science in 2026-06; available now" or "Final year of BSc Computer Science, finishing
2027-06; available from 2027-07-01". `today_lines()` is the "Today" and "Stage" lines a request starts its facts
with, and `ended_course(text, stage)` finds the sentences of a draft that speak of finishing a course that
has ended. `project_kind` says whether a project was degree work, done at work or a personal project, and
whether it was a team's; `context_of` turns that into one of the four contexts a profile card keeps
(`CONTEXTS`: degree, team, work, personal), and `CONTEXT_WORDS` says each in a prompt's words.

Design decisions, each with its reason:

- Dates decide, the "Are you studying now?" setting only when no end date is known: a letter written in
  November 2026 said "available to start in 2026 upon completing my degree" for a degree that ended in June
  (2026-11-06). An end date given as a year alone ends in December, as `find.kinds.is_student` reads it.
- The end date is the most precise one given: the setting `goal.study_end` or the newest education fact the
  person checked (`profile.writing.USED_STATES`), a month before a bare year. Facts Hal found and nobody
  checked never decide.
- Future or pending words about an ended course count too: "available to start immediately following my
  graduation in June 2026" was written on 2026-11-07, after the person graduated. "Following my graduation"
  also opens true sentences about the past, so it counts only beside words of what is to come and none of
  what has been.
- Calling the person a student today counts too (`_still_student`): "a Computer Science student", "I am a
  student", "As a final-year student, I am ..." in a sentence that speaks of now ("I am", "currently", or
  opening "As a ... student") and says nothing the person did ("I built", "I was"). Messages called a person
  who had graduated "a Computer Science student" in every graded run (2026-11-08); "As a student, I built
  ..." is about the past and stays.
- A draft that speaks of finishing an ended course is found by words, with no AI, so the same draft always
  gets the same answer; the sentences found are treated as unsupported claims (`pack.unsupported_claims`).
- A project's kind comes from its evidence: coursework, a dissertation or a module code make it degree work;
  a placement or a job's employer named in it make it work; a repository or project with neither is a
  personal project. A letter called a personal project "degree work" (2026-11-06), so the prompts say never
  to change a kind. With no sign either way the kind is left unsaid rather than guessed.
- A team project with no sign of work is a university team project: "group project", "team project", "team
  of 7", "7 developers", or a university named with a team word in one quote, are how courses and CVs name
  their team work, and a university team project was called a personal project (2026-11-07). The person can
  change any card's context (`profile.contexts`).
- What the words say comes before what kind of file it is: a README saying it extends the author's
  dissertation in their own time was called degree work, so own-time words ("own time", "side project",
  "personal project", "extends my dissertation") make a personal project whatever else it names. A module
  code joined to a word ("IMAG2031blog") still names a module, as repositories are often named that way
  (2026-11-07). The document type (coursework, repository) decides only when no words do.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date

from jobhunt.profile import merge

MONTHS_IN_YEAR = 12
FINAL_YEAR_MONTHS = 12    # a course ending within this many months is in its final year
# How each `you.start` choice reads in the stage line (`core/settings/catalog/you.py`).
START_WORDS = {"now": "available now", "1w": "available 1 week after an offer",
               "1m": "available 1 month after an offer"}
# Words that speak of a course still to finish. Each needs the course to have ended to be wrong.
FINISHING = re.compile(
    r"\b((upon|on|after|once|when) (completing|finishing|completion of|graduating|I (complete|finish|graduate))|"
    r"(will|due to|expect(ed|ing)? to|about to|set to) (graduate|complete (my|the)|finish (my|the))|"
    r"final[- ]year (student|of)|(am|I'm) (currently |now )?(studying|in (my|the) final year)|"
    r"currently (studying|enrolled)|graduating (in|this|on)|"
    r"once I('ve| have) (graduated|completed|finished)|(once|when|after) my (degree|course|studies) "
    r"(ends?|finish(es)?|is (complete|completed|finished|over)))\b", re.I)
# A graduation spoken of as an event to come: "available to start immediately following my graduation in June".
# The same words also open a sentence about the past ("Following my graduation, I have worked ..."), so they
# count only beside words of what is to come (`PENDING`) and with none of what has been (`DONE`).
AFTER_GRADUATION = re.compile(r"\b(following|after|upon|on|from|post[- ]?) ?(my |the )?(graduation|"
                              r"completion of (my|the) (degree|course|studies))\b", re.I)
PENDING = re.compile(r"\b(will|shall|available|availability|start|starting|begin|beginning|commence|join|"
                     r"joining|ready to|able to|free to)\b|'ll\b", re.I)
DONE = re.compile(r"\b(have|has|had|since|was|were|spent|went)\b|'ve\b", re.I)
# "a Computer Science student", "an MSc student", "a final-year student": the person called a student.
STUDENT = re.compile(r"\b(?:a|an)\s+(?:[\w&/-]+\s+){0,4}?student\b", re.I)
# Words that put a sentence in the present: "I am", "currently", or a sentence opening "As a ... student".
NOW_WORDS = re.compile(r"\b(I am|I'm|currently|presently|at present|right now|now)\b|^\W*as an?\s", re.I)
# "I built", "I was", "I learnt": the sentence tells what the person did, as a student then.
I_DID = re.compile(r"\bI\s+(?:\w+ly\s+)?(?:\w+ed|was|had|built|wrote|led|made|ran|took|won|spent|went|did|got|"
                   r"found|learnt|taught|gave|began|kept|met|held|chose|drew|became|knew|saw)\b", re.I)
DEGREE_TYPES = ("coursework", "dissertation")
PERSONAL_TYPES = ("repository", "project")
DEGREE_WORDS = re.compile(r"\b(coursework|dissertation|thesis|module|assignment|lab report|final[- ]year project|"
                          r"semester|class project|student project|submitted (for|as)|practicals? \d+|"
                          r"(lab|weekly|module|course|assessed|marked) practicals?|"
                          r"for (a|the|my|this) practical(?=[.,;:)]|\s+(in|on|for|at)\b|$))\b", re.I)
# Words that put a project in the person's own time, whatever else it names: "extends my dissertation in my own
# time" is a personal project built on degree work, not degree work.
PERSONAL_WORDS = re.compile(r"\b(own time|spare time|side project|personal project|hobby project|for fun|"
                            r"(extend(s|ed|ing)?|build(s|ing)? on|built on|continu(es|ed|ing)|follow[- ]?up to) "
                            r"(my|the|her|his|their|the author's) (dissertation|thesis|final[- ]year project))\b",
                            re.I)
COURSEWORK_PATH = "Sources/Coursework/"   # the ingest's category folder for coursework (`ingest/notes.py`)
MODULE_CODE = re.compile(r"\b[A-Z]{2,5}\d{3,5}[A-Z]?\b")   # "COMP2001", "CS310": how courses name a module
GLUED = re.compile(r"(?<=\d)(?=[a-z])")    # where a code meets a word: "IMAG2031blog"
WORK_WORDS = re.compile(r"\b(placement|internship|intern|at work|in my job|for (my|the) employer|client project)\b",
                        re.I)
FEW = r"(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
TEAM_WORDS = re.compile(rf"\b(team project|group project|in a team of|(team|group) of {FEW}|with {FEW} "
                        rf"(others|teammates|classmates)|{FEW}[- ](developers|engineers|programmers|students|"
                        rf"members|people))\b", re.I)
PLACE_OF_STUDY = re.compile(r"\b(university|college)\b", re.I)
TEAM_WORD = re.compile(r"\b(team|group|teammates|squad|developers)\b", re.I)
EMPLOYER_MIN = 3          # an employer's name shorter than this matches ordinary words
# Where an experience was done, as a profile card keeps it (`profile.contexts`), and as a prompt says it.
DEGREE, TEAM, WORK, PERSONAL = "degree", "team", "work", "personal"
CONTEXTS = (DEGREE, TEAM, WORK, PERSONAL)
CONTEXT_WORDS = {DEGREE: "degree work", TEAM: "university team project", WORK: "at work",
                 PERSONAL: "personal project"}


@dataclass(frozen=True)
class Stage:
    """The person's course and availability today; `finished` is None when no course is known."""

    today: date
    course: str
    end: str
    finished: bool | None
    available: str

    @property
    def words(self) -> str:
        """The stage as one line: "Finished BSc Computer Science in 2026-06; available now"."""
        course = self.course or "their course"
        if self.finished is None:
            where = "Not studying"
        elif self.finished:
            where = f"Finished {course}" + (f" in {self.end}" if self.end else "")
        elif self.end and _months(self.end) - _index(self.today) < FINAL_YEAR_MONTHS:
            where = f"Final year of {course}, finishing {self.end}"
        else:
            where = f"Studying {course}" + (f", finishing {self.end}" if self.end else "")
        return f"{where}; {self.available}"


def _index(day: date) -> int:
    return day.year * MONTHS_IN_YEAR + day.month - 1


def _months(end: str) -> int:
    found = merge.month_index(end, "end")
    return found if found is not None else 0


def _precise(end: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}", end or ""))


def _checked_education(con: sqlite3.Connection) -> dict:
    """The newest education fact the person confirmed or edited; {} when there is none."""
    from jobhunt.profile import store
    from jobhunt.profile.writing import USED_STATES

    return next((it["fields"] for it in store.items(con, "education", hidden=False) if it["state"] in USED_STATES), {})


def _available(get, today: date) -> str:
    start = get("you.start") or "now"
    if start == "date":
        when = str(get("you.start_date") or "")
        return f"available from {when}" if when and when > today.isoformat() else "available now"
    words = START_WORDS.get(start, START_WORDS["now"])
    notice = str(get("you.notice") or "").strip()
    return f"{words} (notice: {notice})" if start != "now" and notice else words


def current(con: sqlite3.Connection | None = None, today: date | None = None) -> Stage:
    """The person's stage from the settings and their checked education facts (Hal's database unless `con`)."""
    from jobhunt import db
    from jobhunt.core import clock, settings

    today = today or clock.local_day()
    if con is None:
        with closing(db.connect()) as own:
            education = _checked_education(own)
    else:
        education = _checked_education(con)
    get = settings.get
    course = " ".join(p for p in (education.get("qualification"), education.get("subject")) if p) or \
        " ".join(p for p in (get("you.education.degree"), get("you.education.subject")) if p)
    ends = [e for e in (str(get("goal.study_end") or "").strip(), str(education.get("end") or "")) if e]
    end = next((e for e in ends if _precise(e)), ends[0] if ends else "")
    if end and merge.month_index(end, "end") is not None:
        finished = _months(end) < _index(today)
    elif education.get("current"):
        finished = False
    elif course:
        finished = (get("goal.study_status") or "") != "at_uni"
    else:   # no course and no date: only a student says anything
        finished = False if get("goal.study_status") == "at_uni" else None
    return Stage(today, course.strip(), end, finished, _available(get, today))


def today_lines(stage: Stage | None = None) -> str:
    """The "Today" and "Stage" lines a writing request carries."""
    stage = stage or current()
    return f"Today: {stage.today.isoformat()}\nStage: {stage.words}."


def ended_course(text: str, stage: Stage) -> list[str]:
    """The sentences of a draft that speak of finishing a course that has already ended; [] while it runs."""
    if not stage.finished:
        return []
    from jobhunt.writing import check

    return [s for s in check.sentences(text) if FINISHING.search(s) or _pending_graduation(s) or _still_student(s)]


def _still_student(sentence: str) -> bool:
    """True when a sentence calls the person a student now ("As a Computer Science student, I am keen ..."),
    not when it tells what they did as one ("As a student, I built ...")."""
    return bool(STUDENT.search(sentence)) and bool(NOW_WORDS.search(sentence)) and not I_DID.search(sentence)


def _pending_graduation(sentence: str) -> bool:
    """True when a sentence speaks of a graduation still to come ("available to start following my
    graduation"), not of one that has happened ("Since my graduation I have ...")."""
    return bool(AFTER_GRADUATION.search(sentence)) and bool(PENDING.search(sentence)) and not DONE.search(sentence)


def project_kind(texts: list[str], doc_types: list[str] = (), employers: list[str] = ()) -> str:
    """What kind of project the words and document types of its evidence show: "degree work", "at work" or
    "personal project", with ", team project" for a team's; "" when nothing shows it.

    Own-time words first, then degree words or a module code (or a coursework file), then work, then a
    repository or project file; a team's when one text says so or names a university with a team word.
    """
    parts = [t for t in texts if t]
    text = " ".join(parts)
    named = [e for e in employers if len(e) >= EMPLOYER_MIN]
    kinds = []
    if PERSONAL_WORDS.search(text):
        kinds.append("personal project")
    elif any(d in DEGREE_TYPES for d in doc_types) or DEGREE_WORDS.search(text) \
            or MODULE_CODE.search(GLUED.sub(" ", text)) or COURSEWORK_PATH in text:
        kinds.append("degree work")
    elif WORK_WORDS.search(text) or any(re.search(rf"\b{re.escape(e)}\b", text, re.I) for e in named):
        kinds.append("at work")
    elif any(d in PERSONAL_TYPES for d in doc_types):
        kinds.append("personal project")
    if TEAM_WORDS.search(text) or any(PLACE_OF_STUDY.search(p) and TEAM_WORD.search(p) for p in parts):
        kinds.append("team project")
    return ", ".join(kinds)


def context_of(kind: str) -> str:
    """The context (`CONTEXTS`) a `project_kind` answer names: work first, then a team's, then degree work,
    then a personal project; "" when it names none."""
    if "at work" in kind:
        return WORK
    if "team project" in kind:
        return TEAM
    if "degree work" in kind:
        return DEGREE
    return PERSONAL if "personal project" in kind else ""


def team_words(text: str) -> str:
    """The words of `text` that say a project was a team's ("team of 7", "group project"); "" when none.
    "in a team of 7" reads as "team of 7", so a card and its quote say a team the same way."""
    found = TEAM_WORDS.search(text or "")
    if not found:
        return ""
    said = found.group(0)
    if said.lower() == "in a team of":
        size = re.match(rf"\s+{FEW}\b", text[found.end():], re.I)
        said = "team of" + (size.group(0) if size else "")
    return said

"""The message's opening: who is writing and why, built from the person's settings and stage with no AI.

`Opening.of(name, stage, title, contact)` is the greeting ("Hello Ada Penhallow," or "Hello Hiring Manager,")
and the first sentence ("I'm Avery Quillon, a first-class Imaginary Studies graduate from Made-up University
(2026), applying for the Graduate Analyst role."). The writer is asked for the body alone (`Opening.note`);
`body_of(draft)` takes out a greeting or an introduction it wrote all the same, and `Opening.put`
puts the opening before the body, so the message always starts with it (`Opening.starts`).

Design decisions, each with its reason:

- All 15 "message to the hiring manager" drafts of the graded packs lacked an introduction: a bare question,
  a dangling "I also ...", or "I am available to start immediately" (2026-11-08). Who is writing is a fact
  of the settings, not something to write, so it is built here and never checked or rewritten.
- The sentence says only what the settings and the checked course say: the name, the course with its
  subject, institution and year, and the grade only when it is a class a reader knows ("first-class",
  "2:1", "2:2", or "with Distinction"); a mark or a percentage is left out rather than turned into a class.
  A course still running says "final-year" only in its last year (`stage.FINAL_YEAR_MONTHS`).
- The greeting names the advert's hiring contact as the advert gives the name, never with a title, else
  "Hiring Manager"; the writer's own greeting is taken out, so no rewrite for a greeting is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobhunt.writing import cites, stage

HIRING_MANAGER = "Hiring Manager"     # whom the greeting names when the advert names no hiring contact
# Grades a reader knows as a class, as the words before "graduate", and those said after it.
CLASSES = ((re.compile(r"\b(?:first|1st)\b", re.I), "first-class"),
           (re.compile(r"\b2[:.]1\b|upper second", re.I), "2:1"),
           (re.compile(r"\b2[:.]2\b|lower second", re.I), "2:2"))
AFTER = ((re.compile(r"\bdistinction\b", re.I), "with Distinction"), (re.compile(r"\bmerit\b", re.I), "with Merit"))
YEAR = re.compile(r"^\d{4}")
# A letter that is said with a vowel sound when it stands alone: "an MSc", "an LLB", "an HND".
VOWEL_LETTERS = "AEFHILMNORSX"
ROLE_WORDS = re.compile(r"\b(?:role|position|post|job|vacancy)$", re.I)
# The writer's own greeting: "Hello Ada," "Dear Hiring Team," "Hi," at the very start.
GREETING = re.compile(r"^\s*(?:dear|hi|hello|hey|good (?:morning|afternoon|evening))\b[^,\n!]{0,60}[,!]?[ \t]*\n*",
                      re.I)
# The writer's own introduction: "I'm Avery ...", "My name is ...", "I am a ... graduate", "I am writing to ...".
INTRO = re.compile(r"^(?:my name is\b|(?:I am|I'm)\s+(?:writing|applying|reaching out|getting in touch)\b|"
                   r"(?:I am|I'm)\s+(?:an?\s+)?(?:[\w&/:-]+\s+){0,6}?(?:graduate|student)\b)", re.I)
# "I'm Avery Quillon, ...": the writer naming the applicant (capitals matter, so not case-blind like `INTRO`).
NAMED = re.compile(r"^(?:I am|I'm)\s+[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3}\s*(?:[,.\[]|$)")
NOTE = ("Hal starts the message with these words, which you must not write again:\n{opening}\nWrite only what "
        "follows them: no greeting, and no sentence saying who the applicant is or which role they apply for.")


def _article(word: str) -> str:
    """ "a" or "an" before `word`: by the sound of its first letter when it is an abbreviation ("an MSc")."""
    if re.match(r"[A-Z][a-z]?[A-Z]", word) or re.fullmatch(r"[A-Z]+", word):
        return "an" if word[0] in VOWEL_LETTERS else "a"
    return "an" if re.match(r"[aeiou]", word, re.I) and not re.match(r"(?:uni|eu|one)", word, re.I) else "a"


def _grade(grade: str) -> tuple[str, str]:
    """The grade as words before "graduate" and words after it; ("", "") for a grade no reader knows as a class."""
    before = next((words for rx, words in CLASSES if rx.search(grade or "")), "")
    after = next((words for rx, words in AFTER if rx.search(grade or "")), "")
    return before, after


def described(now: stage.Stage) -> str:
    """Who the person is by their course: "a first-class Imaginary Studies graduate from Made-up University
    (2026)", "a final-year BSc Imaginary Studies student at Made-up University"; "" when no course is known."""
    subject = now.subject or now.course
    if now.finished is None or not subject:
        return ""
    if now.finished:
        before, after = _grade(now.grade)
        year = YEAR.match(now.end or "")
        words = " ".join(w for w in (before, subject, "graduate", after) if w)
        words += (f" from {now.institution}" if now.institution else "") + (f" ({year.group(0)})" if year else "")
    else:
        final = bool(now.end) and stage.final_year(now)
        words = " ".join(w for w in ("final-year" if final else "", subject, "student") if w)
        words += f" at {now.institution}" if now.institution else ""
    return f"{_article(words)} {words}"


@dataclass(frozen=True)
class Opening:
    """A message's greeting and first sentence."""

    greeting: str
    sentence: str

    @classmethod
    def of(cls, name: str, now: stage.Stage, title: str, contact: str = "") -> Opening:
        """The opening for the person `name` (or "" when unknown) at `now`, applying for `title`, to `contact`."""
        role = title.strip() if ROLE_WORDS.search(title.strip()) else f"{title.strip()} role"
        who = ", ".join(p for p in (name.strip(), described(now)) if p)
        sentence = f"I'm {who}, applying for the {role}." if who else f"I'm applying for the {role}."
        return cls(f"Hello {contact.strip() or HIRING_MANAGER},", sentence)

    @property
    def note(self) -> str:
        """What the message request tells the writer: the opening Hal writes, and to write only the body."""
        return NOTE.format(opening=f"{self.greeting} {self.sentence}")

    def put(self, body: str) -> str:
        """The message: the greeting, then the first sentence starting the body's first paragraph."""
        body = (body or "").strip()
        return f"{self.greeting}\n\n{self.sentence}" + (f" {body}" if body else "")

    def starts(self, message: str) -> bool:
        """True when `message` starts with this opening."""
        return (message or "").startswith(f"{self.greeting}\n\n{self.sentence}")


def body_of(draft: str) -> str:
    """A drafted message without a greeting or an introduction of the applicant at its start, which the opening
    says instead; its evidence markers are kept."""
    text = GREETING.sub("", draft or "", count=1).lstrip()
    said = cites.sentences(text)
    first = cites.strip(said[0]).strip() if said else ""
    if first and (INTRO.match(first) or NAMED.match(first)):
        found = re.search(r"\s+".join(map(re.escape, said[0].split())), text)
        text = text[found.end():].lstrip() if found else text
    return text

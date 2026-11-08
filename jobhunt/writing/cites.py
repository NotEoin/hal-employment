"""Evidence markers: each sentence of a draft says which items of the evidence plan (`writing.plan`) it rests
on, and the markers are checked and taken out before anyone sees the draft.

Drafts are written freely and their markers put in afterwards by `writing.attribute`; a writer is asked to
mark its own sentences (`MARK_NOTE`) only when that attribution fails.

A sentence that states something about the person ends with the ids of its evidence, "[E2]" or "[E2,E5]",
before its full stop; a reason for wanting the job or a closing request ends with "[M]", which states no fact
about the person. `cited(text)` reads each sentence with its marker, `strip(text)` is the draft as the person
sees it, and `about_person(sentence, allowed)` says whether a sentence states something about the person, so
needs evidence; `writing.grounded` checks each sentence against what it cites.

Design decisions, each with its reason:

- Each sentence is marked, so a check can tie a sentence to its card whatever words it uses: projects
  described rather than named ("a personal project included in my degree") slipped past the checks that look
  for a card's name (2026-11-07). A writer marking as it wrote made choppy one-fact sentences, so the marks
  come from `writing.attribute` after the draft is written (2026-11-08).
- A sentence is about the person when it is in the first person and has a verb in the past tense, or holds a
  number that neither the employer's words nor the person's stage line hold. An "-ed" word after "am", "be",
  "feel" and the like is an adjective ("I am interested"), and words of being drawn to an employer
  (`FEELINGS`) are never a fact, so a reason for the employer needs no evidence.
- A marker written after the full stop ("... results. [E2]") is moved before it (`normalise`), so a sentence
  keeps its own marker when a draft is split or cut to its last full sentence.
- A marker ends its sentence. A local model often left out the full stop after one ("on one consumer GPU
  [E1] This project shows"), so taking the markers out joined two sentences and every check read them as
  one (2026-11-07). `normalise` puts the stop in when a marker run is followed by a new sentence (a capital,
  a quote or a digit), a line break or the end of the text, and `sentences` splits after every marker run
  with its stop, also before a digit or a small letter.
- Every check of style reads the draft without its markers, so "[E2]" is never read as a number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobhunt.writing import check

_ONE = r"\[\s*(?:M|E\d+(?:\s*[,;]\s*E?\d+)*)\s*\]"
MARKER = re.compile(rf"[ \t]*{_ONE}", re.I)
AFTER_STOP = re.compile(rf"([.!?])((?:[ \t]*{_ONE})+)", re.I)
# A marker run after a word (no full stop), then a new sentence, a line break or the end of the text.
# Case matters after the run, so only the markers are read without it: "[E1] and" goes on with the sentence.
UNSTOPPED = re.compile(rf"(?<=[^.!?\s])((?i:[ \t]*{_ONE})+)(?=\s+[A-Z\"'\d\u2018\u201c]|[ \t]*(?:\n|$))")
STOPPED = re.compile(rf"{_ONE}[.!?]\s+", re.I)    # a marker run's end with its stop: a sentence ends there
MOTIVE = "M"
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'd|me|my|mine|we|our|us)\b", re.I)
# Past tenses with no "-ed" ending that claim something done.
IRREGULAR = frozenset("""built rebuilt led made wrote rewrote ran won taught began became did found gave got grew held
kept knew met sold spent took brought chose fought went spoke lost paid undertook oversaw drove""".split())
# Words before an "-ed" word that make it an adjective: "I am interested", "I feel prepared".
LINKING = frozenset("am 'm be been being is are was were feel felt very really so most more".split())
# "-ed" words that say why the person wants the job, or are not verbs at all.
FEELINGS = frozenset("""interested excited inspired impressed appealed attracted intrigued motivated pleased delighted
encouraged prompted struck hundred kindred sacred naked wicked""".split())
PAST_ED = 5                # an "-ed" word at least this long ("based" and up) can be a past tense
NUMBER = re.compile(r"\d[\d,.]*\d|\d|\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
                    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|dozen)\b", re.I)
NO_EVIDENCE = "states something about you but cites no evidence"
# The writer's own marking, asked for only when attributing a free draft fails (`writing.attribute`).
MARK_NOTE = ("Write the draft again with the same words, and end each sentence that says anything about the "
             "applicant with the ids of the evidence plan's items it rests on, in square brackets before its full "
             "stop: [E2] or [E2,E5]. End a sentence about why this employer, a question or the closing request "
             "with [M]. Hal checks and removes these markers.")
NOT_IN_PLAN = "cites {ids}, which the evidence plan does not hold"


@dataclass(frozen=True)
class Cited:
    """One sentence of a draft with its markers."""

    raw: str                   # as written, markers included
    text: str                  # as the person sees it
    ids: tuple[str, ...]       # the evidence it cites, in order
    motive: bool               # it carries [M]

    @property
    def marked(self) -> bool:
        """True when the sentence carries any marker."""
        return bool(self.ids) or self.motive


def normalise(text: str) -> str:
    """The draft with each marker written after a full stop moved before it, and a full stop after each marker
    run that ends a sentence without one (see the module notes)."""
    moved = AFTER_STOP.sub(lambda m: m.group(2) + m.group(1), text or "")
    return UNSTOPPED.sub(lambda m: m.group(1) + ".", moved)


def strip(text: str) -> str:
    """The draft as the person sees it: no markers."""
    return MARKER.sub("", normalise(text))


def has_markers(text: str) -> bool:
    """True when a draft carries any marker."""
    return MARKER.search(text or "") is not None


def _ids(marker: str) -> list[str]:
    inner = marker.strip().strip("[]").strip()
    if inner.upper() == MOTIVE:
        return [MOTIVE]
    return [f"E{n}" for n in re.findall(r"\d+", inner)]


def sentences(text: str) -> list[str]:
    """The sentences of a draft, markers kept: `writing.check.sentences` of the normalised draft, each also
    split after a marker run with its stop."""
    out = []
    for said in check.sentences(normalise(text)):
        start = 0
        for m in STOPPED.finditer(said):
            out.append(said[start:m.end()].strip())
            start = m.end()
        if said[start:].strip():
            out.append(said[start:].strip())
    return out


def cited(text: str) -> list[Cited]:
    """Each sentence of a draft (`sentences`) with the evidence its markers cite."""
    out = []
    for raw in sentences(text):
        said = [i for m in MARKER.findall(raw) for i in _ids(m)]
        ids = tuple(dict.fromkeys(i for i in said if i != MOTIVE))
        out.append(Cited(raw, MARKER.sub("", raw).strip(), ids, MOTIVE in said))
    return out


def _past_tense(sentence: str) -> bool:
    words = re.findall(r"[A-Za-z']+", sentence)
    for before, word in zip(["", *words], words, strict=False):
        lower = word.lower()
        if lower in IRREGULAR:
            return True
        if (lower.endswith("ed") and len(lower) >= PAST_ED and not lower.endswith("eed") and lower not in FEELINGS
                and before.lower() not in LINKING):
            return True
    return False


def numbers(sentence: str) -> list[str]:
    """The numbers of a sentence, in figures or in words ("three"), lower case."""
    return [m.group(0).lower().rstrip(".,") for m in NUMBER.finditer(sentence or "")]


def about_person(sentence: str, allowed: str = "") -> bool:
    """True when a sentence states something about the person: the first person with a past tense, or a number
    that `allowed` (the employer's words and the stage line) does not hold."""
    if FIRST_PERSON.search(sentence) and _past_tense(sentence):
        return True
    held = set(numbers(allowed))
    return any(n not in held for n in numbers(sentence))


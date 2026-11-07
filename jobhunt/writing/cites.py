"""Evidence markers: each sentence of a draft says which items of the evidence plan (`writing.plan`) it rests
on, and the markers are checked and taken out before anyone sees the draft.

A sentence that states something about the person ends with the ids of its evidence, "[E2]" or "[E2,E5]",
before its full stop; a reason for wanting the job or a closing request ends with "[M]", which states no fact
about the person. `cited(text)` reads each sentence with its marker, `strip(text)` is the draft as the person
sees it, and `failures(text, ids, allowed)` lists the sentences that state something about the person with no
evidence, or cite an id the plan does not hold.

Design decisions, each with its reason:

- The model marks each sentence as it writes it, so a check can tie a sentence to its card whatever words it
  uses: projects described rather than named ("a personal project included in my degree") slipped past the
  checks that look for a card's name (2026-11-07).
- A sentence is about the person when it is in the first person and has a verb in the past tense, or holds a
  number that neither the employer's words nor the person's stage line hold. An "-ed" word after "am", "be",
  "feel" and the like is an adjective ("I am interested"), and words of being drawn to an employer
  (`FEELINGS`) are never a fact, so a reason for the employer needs no evidence.
- A marker written after the full stop ("... results. [E2]") is moved before it (`normalise`), so a sentence
  keeps its own marker when a draft is split or cut to its last full sentence.
- Every check of style reads the draft without its markers, so "[E2]" is never read as a number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobhunt.writing import check

_ONE = r"\[\s*(?:M|E\d+(?:\s*[,;]\s*E?\d+)*)\s*\]"
MARKER = re.compile(rf"[ \t]*{_ONE}", re.I)
AFTER_STOP = re.compile(rf"([.!?])((?:[ \t]*{_ONE})+)", re.I)
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
    """The draft with each marker written after a full stop moved before it."""
    return AFTER_STOP.sub(lambda m: m.group(2) + m.group(1), text or "")


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


def cited(text: str) -> list[Cited]:
    """Each sentence of a draft (`writing.check.sentences`) with the evidence its markers cite."""
    out = []
    for raw in check.sentences(normalise(text)):
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


def failures(text: str, ids: set[str], allowed: str = "") -> list[str]:
    """The sentences of a marked draft that state something about the person with no evidence (no marker, or
    only [M]), or cite an id not in `ids`, each with what is wrong."""
    out = []
    for c in cited(text):
        unknown = [i for i in c.ids if i not in ids]
        if unknown:
            out.append(f"{c.text} ({NOT_IN_PLAN.format(ids=', '.join(unknown))})")
        elif not c.ids and about_person(c.text, allowed):
            out.append(f"{c.text} ({NO_EVIDENCE})")
    return out


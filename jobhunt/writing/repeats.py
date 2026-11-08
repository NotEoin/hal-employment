"""No repeats: an answer saying what an earlier one said, a sentence said twice, and a gap listed twice.

`repeated(answers)` finds, with no AI, each sentence of a later answer that says nearly what a sentence of an
earlier one says; `pack._answers` has the answers rewritten once so the later one uses other evidence.
`same(a, b)` is the comparison, which `writing.paragraphs` also uses for a sentence a paragraph says twice.
`unique_gaps(gaps)` keeps the first of the fit map's gaps that say the same thing.

Design decisions, each with its reason:

- Two answers of one pack repeated the same pipeline details nearly word for word, and a gap was listed
  twice (2026-11-07). A reader of a form sees every answer, so each should add something.
- Sentences are near duplicates when the shorter one's own words (little words aside, `drafts.LITTLE_WORDS`)
  number at least `REPEAT_MIN_WORDS` and at least `REPEAT_SHARE` of them are in the other: a shared project
  name in two different sentences is not a repeat, the same three claims in the same order are.
- One project is told once per pack by how drafts are made (`writing.plan.Story`, 2026-11-08), so no
  sentence is compared across drafts any more: that compare-and-rewrite pass was one of the mechanical
  patches whose packs a grader still capped at 2 of 5 for readability (2026-11-08).
- Gaps are the same when their words are, case, punctuation, order and the "(no evidence found)" note
  aside (`pack.MISSING_GAP`), as the second ask for a missing must-have can list a gap the first gave.
"""
from __future__ import annotations

import re

from jobhunt.writing import check
from jobhunt.writing.drafts import LITTLE_WORDS

REPEAT_SHARE = 0.7         # share of the shorter sentence's own words the other must hold to repeat it
REPEAT_MIN_WORDS = 5       # a sentence needs this many own words before it can repeat another
NO_EVIDENCE_NOTE = re.compile(r"\(no evidence found\)", re.I)


def _own(sentence: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", (sentence or "").lower()) if w not in LITTLE_WORDS}


def same(a: str, b: str) -> bool:
    """True when two sentences say nearly the same thing (see the module notes)."""
    one, two = _own(a), _own(b)
    shorter = min(len(one), len(two))
    return shorter >= REPEAT_MIN_WORDS and len(one & two) >= REPEAT_SHARE * shorter


def repeated(answers: list[str]) -> list[tuple[int, str, int]]:
    """Each sentence of a later answer that repeats a sentence of an earlier one, as (its answer's index, the
    sentence, the earlier answer's index)."""
    said = [check.sentences(a) for a in answers]
    out = []
    for i, sentences in enumerate(said):
        for sentence in sentences:
            earlier = next((j for j in range(i) if any(same(sentence, s) for s in said[j])), None)
            if earlier is not None:
                out.append((i, sentence, earlier))
    return out


def unique_gaps(gaps: list) -> list:
    """The gaps with each one that says the same as an earlier one left out."""
    seen, out = set(), []
    for gap in gaps:
        key = tuple(sorted(re.findall(r"[a-z0-9+#]+", NO_EVIDENCE_NOTE.sub("", str(gap)).lower())))
        if key not in seen:
            seen.add(key)
            out.append(gap)
    return out

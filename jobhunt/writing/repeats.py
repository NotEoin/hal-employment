"""No repeats: the same sentence twice in one draft or in two of a pack's drafts, and the same gap listed twice.

`repeated(answers)` finds, with no AI, each sentence of a later answer that says nearly what a sentence of an
earlier one says; `pack._answers` has the answers rewritten once so the later one uses other evidence.
`first_said(sentence, earlier)` is the earlier sentence one repeats, which `writing.grounded.unrepeat` uses for
every sentence of the letter, the message and the answers, against the sentences before it in its own draft
and in the drafts before it, and `card_again(ids, far, card_of)` the project a sentence tells again with an
item a paragraph two or more before it already cites. `unique_gaps(gaps)` keeps the first of the fit map's
gaps that say the same thing.

Design decisions, each with its reason:

- Two answers of one pack repeated the same pipeline details nearly word for word, and a gap was listed
  twice (2026-11-07). A reader of a form sees every answer, so each should add something.
- Packs said the same sentence twice back to back, and reused one CV line in the letter, the message and both
  answers (15 graded packs, 2026-11-07), so a sentence is compared with every sentence before it in the
  pack's order: the letter, the message, then the answers in turn.
- Sentences are near duplicates when the shorter one's own words (little words aside, `drafts.LITTLE_WORDS`)
  number at least `REPEAT_MIN_WORDS` and at least `REPEAT_SHARE` of them are in the other: a shared project
  name in two different sentences is not a repeat, the same three claims in the same order are.
- One project, once per draft: a letter told the same pipeline three times, each time in other words, so no
  sentence repeated another (2026-11-08). A sentence citing an item of a project card that a paragraph two or
  more before it already cites (`card_again`) says that project's story again; the paragraph just before is
  left alone, as a letter goes on about one project from one paragraph to the next.
- Gaps are the same when their words are, case, punctuation, order and the "(no evidence found)" note
  aside (`pack.MISSING_GAP`), as the second ask for a missing must-have can list a gap the first gave.
"""
from __future__ import annotations

import re

from jobhunt.writing import check, cites
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


def first_said(sentence: str, earlier: list[str]) -> str | None:
    """The first of the `earlier` sentences that `sentence` says nearly again (`same`), or None."""
    return next((e for e in earlier if same(sentence, e)), None)


def card_again(ids: tuple[str, ...], far: list[str], card_of: dict[str, str]) -> str:
    """The card a sentence (citing `ids`) speaks of again: one whose item it cites is also cited in one of the
    `far` paragraphs of its draft (those before the paragraph just before its own); "" when none is."""
    said = {i for para in far for c in cites.cited(para) for i in c.ids}
    return next((card_of[i] for i in ids if i in said and card_of.get(i)), "")


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

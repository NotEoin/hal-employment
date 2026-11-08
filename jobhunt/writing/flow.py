"""How a draft reads once sentences are taken out: sentences that refer back to nothing, and letters left short.

`back_reference(sentence)` reads a sentence that opens by pointing back ("This project", "That RAG
experience", "It"); `refers_to_nothing(sentence, before)` says whether what it points at is missing from the
text before it; `reads_without(paragraph, sentence)` says whether a paragraph still reads when one sentence is
taken out; `dangling(text)` lists the paragraphs that open by referring back to nothing; `short(text, words)`
says whether a letter is under `LENGTH_FLOOR` of the person's preferred length.

Design decisions, each with its reason:

- The sentence checks took failing sentences out and left "This project allowed me ...", "that RAG
  experience" and "I am ready to apply this experience" pointing at nothing, and letters of 105 to 216 words
  against a preferred 250 (15 graded packs, 2026-11-07). A sentence the next one points back at is kept,
  and a short letter gets one more paragraph from evidence it has not used (`writing.grounded`).
- A back reference is "This", "That", "These", "Those", "Such" or "It" at the start of a sentence. With a
  noun after it ("This project") it points at that noun, so it refers to nothing when none of the noun's
  words (by their first `STEM` letters) is in the text before it; a bare pronoun ("It runs", "This
  shows") points at the sentence before, so it refers to nothing only when there is no text before it.
  "It would" and "It will" open a sentence without pointing anywhere.
- Nouns that name the job itself ("This role", "this position") always have something to point at: the
  letter is about it.
- `LENGTH_FLOOR` is 0.8: the letter prompt asks for about the preferred length and not much fewer, a letter
  within a fifth of it reads as that length, and the short letters graders marked down were 105 to 216
  words against 250, all but one of them under 200 (2026-11-07).
"""
from __future__ import annotations

import re

from jobhunt.writing import check, cites
from jobhunt.writing.drafts import LITTLE_WORDS

LENGTH_FLOOR = 0.8          # share of the preferred length a letter must reach (see the module notes)
STEM = 5                    # letters compared, so "project" meets "projects"
PHRASE_WORDS = 3            # words after the opener read as the noun it points at
OPENER = re.compile(r"^[\"'(]*(this|that|these|those|such|it)\b(?!\s+(?:would|will|could|might|may)\b)\s*(.*)",
                    re.I | re.S)
# Words after an opener that start its verb, so the opener is a bare pronoun ("This shows", "It runs").
VERBS = frozenset("""is was are were has had have will would can could may might should must means meant gives gave
allows allowed helps helped shows showed taught teaches led leads made makes let lets runs ran works worked
proved proves took takes kept keeps""".split())
JOB_NOUNS = frozenset("role position job post opportunity vacancy application".split())


def _stem(word: str) -> str:
    return word.lower()[:STEM]


def back_reference(sentence: str) -> tuple[str, ...] | None:
    """The words a sentence's opening points back at: None when it does not open by pointing back, () for a
    bare pronoun ("It runs", "This shows"), else the noun's words ("project", "rag experience")."""
    found = OPENER.match(cites.strip(sentence or "").strip())
    if not found:
        return None
    if found.group(1).lower() == "it":
        return ()
    phrase = []
    for word in re.findall(r"[A-Za-z][A-Za-z'-]*", found.group(2))[:PHRASE_WORDS]:
        lower = word.lower()
        if lower in VERBS or lower in cites.IRREGULAR or (lower.endswith("ed") and len(lower) >= cites.PAST_ED):
            break
        if lower not in LITTLE_WORDS:
            phrase.append(lower)
    return tuple(phrase)


def refers_to_nothing(sentence: str, before: str) -> bool:
    """True when `sentence` opens by pointing back at something `before` (the text ahead of it) does not hold."""
    phrase = back_reference(sentence)
    if phrase is None:
        return False
    if not phrase:
        return not cites.strip(before or "").strip()
    if phrase[0] in JOB_NOUNS:
        return False
    held = {_stem(w) for w in check.words_of(cites.strip(before or ""))}
    return not any(_stem(w) in held for w in phrase)


def reads_without(paragraph: str, sentence: str) -> bool:
    """True when `paragraph` still reads with `sentence` taken out: it keeps another sentence, and the sentence
    after it does not point back at it (a bare pronoun, or a noun the rest before it does not hold)."""
    said = cites.sentences(paragraph)
    if sentence not in said or len(said) <= 1:
        return False
    at = said.index(sentence)
    if at == len(said) - 1:
        return True
    after = said[at + 1]
    phrase = back_reference(after)
    if phrase is None:
        return True
    return bool(phrase) and not refers_to_nothing(after, " ".join(said[:at]))


def paragraphs(text: str) -> list[str]:
    """The paragraphs of a draft, each stripped; empty ones left out."""
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def dangling(text: str) -> list[str]:
    """The first sentence of each paragraph that opens by pointing back at nothing in the paragraph before."""
    out, before = [], ""
    for para in paragraphs(text):
        said = cites.sentences(para)
        if said and refers_to_nothing(said[0], before):
            out.append(cites.strip(said[0]))
        before = para
    return out


def word_count(text: str) -> int:
    """The words of a draft as the person sees it."""
    return len(cites.strip(text or "").split())


def short(text: str, words: int | None) -> bool:
    """True when a letter is under `LENGTH_FLOOR` of the person's preferred length `words`."""
    return bool(words) and word_count(text) < LENGTH_FLOOR * words

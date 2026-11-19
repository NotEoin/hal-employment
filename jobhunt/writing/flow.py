"""How a draft reads: paragraphs that refer back to nothing or open alike, and letters left short.

`back_reference(sentence)` reads a sentence that opens by pointing back ("This project", "That RAG
experience", "It"); `refers_to_nothing(sentence, before)` says whether what it points at is missing from the
text before it; `dangling(text)` lists the paragraphs that open by referring back to nothing;
`alike_openings(text)` the paragraphs that open like an earlier one; `relevance_closers(text)` the middle
paragraphs that end on a sentence only claiming relevance; `short(text, words)` says whether a letter is under
`LENGTH_FLOOR` of the person's preferred length.

Design decisions, each with its reason:

- Letters left "This project allowed me ...", "that RAG experience" and "I am ready to apply this
  experience" pointing at nothing, and came to 105 to 216 words against a preferred 250 (15 graded packs,
  2026-11-07): each is listed, and a short letter is redrafted once (`pack.checks.settle`).
- Letters all read alike: three paragraphs each opening "I built <project>, a personal project that ..."
  (owner, 2026-11-19). A paragraph whose first `OPENING_WORDS` words are an earlier paragraph's, or a second
  paragraph opening with "I", asks for the letter's one redraft; it is never listed, as it is a matter of
  style. So does a middle paragraph ending on a sentence that only claims relevance (`RELEVANCE`: "These
  experiences prepared me ...", "..., which is relevant to ..."), which every paragraph of those letters did
  and the first one-call letters still did (2026-11-19).
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

from jobhunt.writing import check
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
# Past tenses with no "-ed" ending, which start a bare pronoun's verb ("It ran", "This led").
IRREGULAR = frozenset("""built rebuilt led made wrote rewrote ran won taught began became did found gave got grew held
kept knew met sold spent took brought chose fought went spoke lost paid undertook oversaw drove""".split())
PAST_ED = 5                 # an "-ed" word at least this long ("based" and up) can be a past tense
OPENING_WORDS = 2           # words that make two paragraph openings alike ("I built", "During my")
# A paragraph's closing claim of relevance: "This experience prepared me ...", "These projects show I can ...",
# "..., which is relevant to ...", "... gave me experience that is useful for ..." (owner, 2026-11-19).
RELEVANCE = re.compile(r"^(?:this|these|that|those|both|all|such)\b[^.]{0,60}\b(?:shows?|showed|demonstrates?|"
                       r"proves?|prepared|prepares|equips?|equipped|gave me|gives me|taught me|means)\b|"
                       r"\b(?:is|are|was|were) (?:directly |highly |very )?(?:relevant|useful|applicable|transferable)"
                       r"\b|\bwhich (?:is|are) relevant\b|\brelevant to (?:this|the|your)\b", re.I)


def _stem(word: str) -> str:
    return word.lower()[:STEM]


def back_reference(sentence: str) -> tuple[str, ...] | None:
    """The words a sentence's opening points back at: None when it does not open by pointing back, () for a
    bare pronoun ("It runs", "This shows"), else the noun's words ("project", "rag experience")."""
    found = OPENER.match((sentence or "").strip())
    if not found:
        return None
    if found.group(1).lower() == "it":
        return ()
    phrase = []
    for word in re.findall(r"[A-Za-z][A-Za-z'-]*", found.group(2))[:PHRASE_WORDS]:
        lower = word.lower()
        if lower in VERBS or lower in IRREGULAR or (lower.endswith("ed") and len(lower) >= PAST_ED):
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
        return not (before or "").strip()
    if phrase[0] in JOB_NOUNS:
        return False
    held = {_stem(w) for w in check.words_of(before or "")}
    return not any(_stem(w) in held for w in phrase)


def paragraphs(text: str) -> list[str]:
    """The paragraphs of a draft, each stripped; empty ones left out."""
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def dangling(text: str) -> list[str]:
    """The first sentence of each paragraph that opens by pointing back at nothing in the paragraph before."""
    out, before = [], ""
    for para in paragraphs(text):
        said = check.sentences(para)
        if said and refers_to_nothing(said[0], before):
            out.append(said[0])
        before = para
    return out


def alike_openings(text: str) -> list[str]:
    """The first sentences of the paragraphs that open like another: the same first `OPENING_WORDS` words as an
    earlier paragraph, or "I" again after an earlier paragraph opened with it (see the module notes)."""
    out, seen, by_i = [], set(), 0
    for para in paragraphs(text):
        said = check.sentences(para)
        first = said[0] if said else para
        words = tuple(w.lower() for w in re.findall(r"[A-Za-z']+", first)[:OPENING_WORDS])
        by_i += bool(words) and words[0] == "i"
        if words and (words in seen or (words[0] == "i" and by_i > 1)):
            out.append(first)
        seen.add(words)
    return out


def relevance_closers(text: str) -> list[str]:
    """The last sentences of a letter's middle paragraphs (neither the first nor the last) that only claim the
    work is relevant ("This experience prepared me ...", "..., which is relevant to ...", `RELEVANCE`)."""
    middle = paragraphs(text)[1:-1]
    last = [check.sentences(p)[-1] for p in middle if check.sentences(p)]
    return [s for s in last if RELEVANCE.search(s)]


def word_count(text: str) -> int:
    """The words of a draft as the person sees it."""
    return len((text or "").split())


def short(text: str, words: int | None) -> bool:
    """True when a letter is under `LENGTH_FLOOR` of the person's preferred length `words`."""
    return bool(words) and word_count(text) < LENGTH_FLOOR * words

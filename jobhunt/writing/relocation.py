"""No offer to relocate to the place the person already lives.

`without_own_move(text, who)` takes out of a draft, with no AI, every offer to relocate or move to a town the
person's settings (or the CV's location line, which comes from them) say they live in: the clause when it
is joined to another by "and" or "but", the whole sentence when the offer is all it says.

Design decisions, each with its reason:

- A letter for a person living in London said "happy to relocate to your London office" (2026-11-07). The
  fix needs no judgement, so it is made in place rather than by asking the writer again, as with spelling.
- Only an offer counts: "relocate" or "move" (or "relocating", "moving") followed within a few words by
  "to", "into", "in" or "near" and the person's own town. "I moved to London in 2020" is a fact and is left
  alone.
- A clause is cut only at "and", "but" or "while", so what is left is still a sentence; an offer with no
  such join is its sentence, which goes.
"""
from __future__ import annotations

import re

from jobhunt.writing import check, eligibility

MOVE_GAP = 40              # characters between "relocate" and "to" ("relocate with my partner to")
PLACE_GAP = 25             # characters between "to" and the town ("to your new London office")
JOIN = re.compile(r",?\s+(and|but|while)\s+", re.I)
CLAUSE_END = re.compile(r"[,;:]|[.!?]?\s*$")


def own_places(who: eligibility.Person) -> list[str]:
    """The towns the person lives in, from each part of their settings' places."""
    return [p.strip() for place in who.places for p in place.split(",") if len(p.strip()) >= eligibility.PLACE_MIN]


def _offer(places: list[str]) -> re.Pattern:
    towns = "|".join(re.escape(p) for p in places)
    return re.compile(rf"\b(relocat(e|ing)|mov(e|ing))\b[^,.;!?]{{0,{MOVE_GAP}}}?\b(to|into|in|near)\b"
                      rf"[^,.;!?]{{0,{PLACE_GAP}}}?\b({towns})\b", re.I)


def _rewritten(sentence: str, offer: re.Pattern) -> str:
    """The sentence without its offer: the joined clause cut, or "" when the offer is the sentence."""
    m = offer.search(sentence)
    joins = list(JOIN.finditer(sentence, 0, m.start()))
    if not joins:
        return ""
    end = CLAUSE_END.search(sentence, m.end()).start()
    tail = sentence[end:]
    return sentence[:joins[-1].start()] + (tail if tail.strip() else ".")


def without_own_move(text: str, who: eligibility.Person) -> str:
    """The draft with every offer to relocate to the person's own town taken out (see the module notes)."""
    places = own_places(who)
    if not places or not text:
        return text
    offer, changed = _offer(places), False
    for sentence in check.sentences(text):
        if not offer.search(sentence):
            continue
        found = re.search(r"\s+".join(re.escape(w) for w in sentence.split()), text)
        if not found:
            continue
        new = _rewritten(sentence, offer)
        end = found.end()
        if not new:
            end += len(text[end:]) - len(text[end:].lstrip(" "))
        text, changed = text[:found.start()] + new + text[end:], True
    return re.sub(r"\n{3,}", "\n\n", text).strip() if changed else text

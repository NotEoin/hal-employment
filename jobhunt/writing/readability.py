"""What makes a checked draft read badly, found with no AI: the readability problems graders marked packs down for.

`findings(text, kind, items, cards, known, earlier)` reads one marked draft (a letter, a message or an answer,
`KINDS`) with the evidence plan's `items` and the person's cards, and lists each `Finding`: a sentence framed
as the advert's requirement and its evidence (`plain.LEAD_IN`, `plain.REQUIREMENT_TALK`), a fact said again
(`repeated`), a project spoken of before it is named (`unnamed`), a message with no introduction
(`no_introduction`), a stray fact in a paragraph about something else (`strays`) and a letter ending on a list
of tools (`tool_list_ending`). `writing.polish` asks for one polish only when a draft has findings, and keeps
the polish only when it has fewer.

Design decisions, each with its reason:

- Free and grounded drafts both scored about 2.1 of 5 on a strict rubric, and 14 of 15 packs of each were held
  there by readability, not by false claims. The commonest problems, in order: the fit table's requirement
  framing in the prose, the same fact or sentence in two paragraphs or drafts, a sentence about a project that
  never names it ("I wrote a 113-test suite ..."), a message with no introduction, a stray fact ("I have the
  right to work in the UK." in a paragraph on teamwork) and a letter ending on a tool list (30 packs,
  2026-11-08). Each is a rule a pattern can see, so whether a draft needs polishing, and whether a polish
  fixed it, is decided with no AI.
- Only the letter, the message and the answers are read, never the fit table, whose rows are meant to pair a
  requirement with its evidence.
- A fact is said again when a sentence says nearly what an earlier one said (`repeats.same`), in its draft or
  an `earlier` one, or when every item it cites was cited in an earlier paragraph of its draft.
- A project is unnamed when the first sentence citing a card's item neither names the card
  (`drafts.names_card`) nor follows a sentence that did; a skills list is tied to no card, so it never counts.
- A message introduces the person when its first sentence after the greeting says who they are or that
  they are applying (`INTRO`): "I am available now." opened a graded message (2026-11-08), so being in the
  first person is not enough.
- A sentence is a stray when at least `STRAY_MIN_OTHERS` other sentences of its paragraph cite items of one
  card, and it cites items, none of them that card's, and does not name the card.
- A stray is moved, with no AI, to the start of the closing paragraph (`placed`), where a fact about the
  person that belongs to no project reads as part of the close; one already in the closing paragraph, or in
  a draft of one paragraph, is left for the polish.
- A letter ends on a tool list when one of its last `ENDING_SENTENCES` sentences names at least
  `TOOL_LIST_MIN` known tools: a letter should end on the person's interest in the work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jobhunt.writing import cites, drafts, flow, plain, repeats
from jobhunt.writing.plan import Item

LETTER, MESSAGE, ANSWER = "letter", "message", "answer"
KINDS = (LETTER, MESSAGE, ANSWER)
REQUIREMENT, REPEAT, UNNAMED, NO_INTRO, STRAY, TOOL_LIST = (
    "requirement", "repeat", "unnamed", "no_introduction", "stray", "tool_list")
STRAY_MIN_OTHERS = 2       # sentences on one card that make a paragraph about it
TOOL_LIST_MIN = 3          # known tools in one sentence that make it a list of tools
ENDING_SENTENCES = 2       # the letter's last sentences read for a tool list (the close and the one before it)
# Words that open a message by saying who the person is or why they write.
INTRO = re.compile(r"\b(?:I am an?|I'm an?|my name is|as an?|I recently|I graduated|I have just|I've just|"
                   r"I am (?:writing|applying|reaching out|getting in touch|studying|currently|in my)|"
                   r"I'm (?:writing|applying|reaching out|getting in touch|studying|currently|in my)|"
                   r"I (?:would|'d) like to apply|I have applied|I've applied|I applied|I study|I work as)\b",
                   re.I)
# What each finding asks of the polish, in the words the request gives it.
ASKS = {
    REQUIREMENT: "frames itself as the advert's requirement and its evidence; say what was done instead",
    REPEAT: "says a fact the draft or an earlier draft already says; say it once, or vary it with what came of it",
    UNNAMED: "speaks of a project, job or module without naming it; name it here, where it is first mentioned",
    NO_INTRO: "the message does not open with who the applicant is (their course or work) and why they write",
    STRAY: "is a fact about something other than its paragraph's subject; move it to the closing paragraph or to "
           "where it fits",
    TOOL_LIST: "the letter ends on a list of tools; end on the applicant's interest in the work instead",
}


@dataclass(frozen=True)
class Finding:
    """One readability problem of a draft: its kind (`ASKS`) and the sentence it is in, as the person sees it."""

    code: str
    sentence: str

    def words(self) -> str:
        """The finding as the polish request lists it."""
        return f'- "{self.sentence}": {ASKS[self.code]}'


def _said(text: str) -> list[tuple[int, cites.Cited]]:
    """Each sentence of a marked draft with the number of its paragraph."""
    return [(p, c) for p, para in enumerate(flow.paragraphs(text)) for c in cites.cited(para)]


def _named(text: str, cards: list[dict]) -> set[str]:
    return {c["name"] for c in cards if c["name"].strip() and drafts.names_card(text, c, cards)}


def requirement_talk(text: str) -> list[Finding]:
    """The sentences framed as the advert's requirement and its evidence."""
    return [Finding(REQUIREMENT, c.text) for _, c in _said(text)
            if plain.LEAD_IN.search(c.text) or plain.REQUIREMENT_TALK.search(c.text)]


def repeated(text: str, earlier: tuple[str, ...] = ()) -> list[Finding]:
    """The sentences that say a fact again (see the module notes)."""
    seen = [cites.strip(s) for e in earlier for s in cites.sentences(e)]
    cited_in: dict[int, set[str]] = {}
    out = []
    for p, c in _said(text):
        before = set().union(*(ids for q, ids in cited_in.items() if q < p))
        if repeats.first_said(c.text, seen) or (c.ids and set(c.ids) <= before):
            out.append(Finding(REPEAT, c.text))
        seen.append(c.text)
        cited_in.setdefault(p, set()).update(c.ids)
    return out


def unnamed(text: str, items: dict[str, Item], cards: list[dict]) -> list[Finding]:
    """The first sentence about each card that neither names it nor follows a sentence that did."""
    named: set[str] = set()
    out = []
    for _, c in _said(text):
        named |= _named(c.text, cards)
        for card in dict.fromkeys(items[i].card for i in c.ids if i in items and items[i].card and not items[i].skills):
            if card not in named and not drafts.names_tool(c.text, card):
                out.append(Finding(UNNAMED, c.text))
            named.add(card)
    return out


def no_introduction(text: str) -> list[Finding]:
    """A message whose first sentence after its greeting does not say who the person is or why they write."""
    body = [c for _, c in _said(text) if not drafts.GREETING.match(c.text)]
    return [Finding(NO_INTRO, body[0].text)] if body and not INTRO.search(body[0].text) else []


def strays(text: str, items: dict[str, Item], cards: list[dict]) -> list[tuple[int, cites.Cited]]:
    """Each stray sentence (see the module notes) with the number of its paragraph."""
    out = []
    for p, para in enumerate(flow.paragraphs(text)):
        said = cites.cited(para)
        for k, c in enumerate(said):
            others = [o for j, o in enumerate(said) if j != k and o.ids]
            theirs = {items[i].card if i in items else "" for o in others for i in o.ids}
            if not c.ids or len(others) < STRAY_MIN_OTHERS or len(theirs) != 1 or "" in theirs:
                continue
            card = next(iter(theirs))
            if card not in {items[i].card for i in c.ids if i in items} and card not in _named(c.text, cards):
                out.append((p, c))
    return out


def placed(text: str, items: dict[str, Item], cards: list[dict]) -> tuple[str, list[str]]:
    """`text` with each stray sentence (`strays`) of a paragraph before the last moved to the start of the last,
    and the sentences moved, as the person sees them."""
    paras = flow.paragraphs(text)
    moved = []
    for p, c in strays(text, items, cards) if len(paras) > 1 else []:
        if p == len(paras) - 1:
            continue
        found = re.compile(r"\s+".join(map(re.escape, c.raw.split()))).search(paras[p])
        if not found:
            continue
        paras[p] = re.sub(r"[ \t]{2,}", " ", paras[p][:found.start()] + paras[p][found.end():]).strip()
        paras[-1] = f"{c.raw} {paras[-1]}"
        moved.append(c.text)
    return "\n\n".join(p for p in paras if p), moved


def tool_list_ending(text: str, known: list[str]) -> list[Finding]:
    """The last sentences of a letter that list `TOOL_LIST_MIN` or more known tools."""
    last = [c for _, c in _said(text)][-ENDING_SENTENCES:]
    return [Finding(TOOL_LIST, c.text) for c in last
            if sum(drafts.names_tool(c.text, t) for t in dict.fromkeys(known)) >= TOOL_LIST_MIN]


def findings(text: str, kind: str, items: dict[str, Item], cards: list[dict], known: list[str],
             earlier: tuple[str, ...] = ()) -> list[Finding]:
    """Every readability problem of one marked draft of `kind` (see the module notes); `earlier` are the drafts
    of the pack before it."""
    out = requirement_talk(text) + repeated(text, earlier) + unnamed(text, items, cards)
    out += [Finding(STRAY, c.text) for _, c in strays(text, items, cards)]
    if kind == MESSAGE:
        out += no_introduction(text)
    if kind == LETTER:
        out += tool_list_ending(text, known)
    return out

"""One polish pass over checked drafts, asked for only when they read badly and kept only when it helped.

`polish(drafts, facts, earlier)` reads each checked, marked `Draft` (a letter, a message or an answer) for the
problems of `writing.readability`. When none has any, nothing is asked. Else one call (prompt `draft_polish`)
gets every draft as the person sees it, with its findings, and replies with them all polished; the polished
drafts are attributed again (`writing.attribute`), and each is kept only when `kept` says so.

Design decisions, each with its reason:

- The sentence checks fix a sentence at a time, so a draft can pass every check and still read as a list of
  facts; graders held 14 of 15 packs at about 2 of 5 for readability (2026-11-08). A pass over the whole
  draft can merge sentences, vary a repeated fact and end on the person's interest, which no sentence fix
  can.
- One call for the drafts that need it, never a loop: the letter is polished on its own path
  (`pack.write_letter`), the message and the answers together, and each polished draft costs one
  attribution with the others of its call.
- A polish is guarded with no AI, as it may add what the checks took out: a polished draft is kept only when
  every sentence passes the checks with no AI (`grounded.problems`: numbers, tools, contexts, roles, what a
  module taught, the stage) at least as well as before, it opens no paragraph by pointing back at nothing
  (`flow.dangling`), it names no number, known tool or card the draft did not (`additions`), its findings
  are fewer and none of them is new. Else the pre-polish draft stays.
- Only drafts with findings are polished; the reply's other drafts are ignored, so a clean answer is never
  rewritten because its neighbour needed it.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import attribute, cites, drafts, flow, grounded, readability, spelling

log = logging.getLogger(__name__)

PROMPT = "draft_polish"
DANGLING = "opens a paragraph by pointing back at nothing"
KIND_WORDS = {readability.LETTER: "Cover letter", readability.MESSAGE: "Message to the person hiring",
              readability.ANSWER: "Answer"}


@dataclass(frozen=True)
class Draft:
    """One checked draft, still marked, of a kind of `readability.KINDS`; `label` is an answer's question."""

    kind: str
    text: str
    label: str = ""


@dataclass
class Polished:
    """The drafts after the polish pass, still marked, how many were polished and the findings left."""

    texts: list[str]
    polished: int = 0
    left: list[readability.Finding] = field(default_factory=list)


def _findings(text: str, kind: str, facts: grounded.Facts, earlier: tuple[str, ...]) -> list[readability.Finding]:
    return readability.findings(text, kind, facts.items, facts.cards, facts.known, earlier)


def failures(text: str, facts: grounded.Facts) -> list[str]:
    """What the checks with no AI find in a marked draft: each sentence's `grounded.problems`, and each paragraph
    that opens by pointing back at nothing."""
    return [p for c in cites.cited(text) for p in grounded.problems(c, facts)] + [DANGLING for _ in flow.dangling(text)]


def additions(new: str, old: str, facts: grounded.Facts) -> list[str]:
    """The numbers and known tools `new` names that `old` and the employer's words do not, and the cards it names
    that `old` neither names nor cites an item of (naming a project `old` spoke of is what a polish is for)."""
    spoken = {facts.items[i].card for c in cites.cited(old) for i in c.ids if i in facts.items}
    new, old = cites.strip(new), cites.strip(old)
    out = [f"adds the number {n}" for n in sorted(grounded.numbers(new) - grounded.numbers(old)
                                                  - grounded.numbers(facts.allowed))]
    out += [f"adds {t}" for t in dict.fromkeys(facts.known)
            if drafts.names_tool(new, t) and not drafts.names_tool(old, t)]
    return out + [f"adds {c['name']}" for c in facts.cards if c["name"].strip() and c["name"] not in spoken
                  and drafts.names_card(new, c, facts.cards) and not drafts.names_card(old, c, facts.cards)]


def kept(new: str, old: str, kind: str, facts: grounded.Facts, earlier: tuple[str, ...] = ()) -> bool:
    """True when the polished `new` may replace `old` (see the module notes)."""
    worse = Counter(failures(new, facts)) - Counter(failures(old, facts))
    before, after = _findings(old, kind, facts, earlier), _findings(new, kind, facts, earlier)
    fresh = Counter(f.code for f in after) - Counter(f.code for f in before)
    added = additions(new, old, facts)
    if worse or added or fresh or len(after) >= len(before):
        log.info("polish: kept the draft as it was (new failures %s, additions %s, new findings %s, findings %d -> "
                 "%d)", dict(worse), added, dict(fresh), len(before), len(after))
        return False
    return True


def request(batch: list[Draft], found: list[list[readability.Finding]], facts: grounded.Facts) -> str:
    """The polish request: each draft as the person sees it with what reads badly in it, then the plan."""
    parts = []
    for n, (d, f) in enumerate(zip(batch, found, strict=True), 1):
        head = f"Draft {n} ({KIND_WORDS[d.kind]}{': ' + d.label if d.label else ''}):"
        listed = "\n".join(x.words() for x in f) or "- nothing; return it as it is"
        parts.append(f"{head}\n{cites.strip(d.text)}\n\nWhat reads badly:\n{listed}")
    plan = "\n".join(i.words() for i in facts.items.values()) or "none"
    return "\n\n".join(parts) + f"\n\nEvidence plan (for reference; add nothing from it):\n{plan}"


def polish(batch: list[Draft], facts: grounded.Facts, earlier: tuple[str, ...] = ()) -> Polished:
    """`batch` after one guarded polish pass (see the module notes); `earlier` are the pack's drafts before
    them, each still marked."""
    texts = [d.text for d in batch]
    found, before = [], list(earlier)
    for d in batch:
        found.append(_findings(d.text, d.kind, facts, tuple(before)))
        before.append(d.text)
    if not any(found):
        return Polished(texts)
    reply = llm.chat_json(prompts.text(PROMPT, spelling=facts.values.get("spelling", "")),
                          request(batch, found, facts), role="writer")
    said = reply.get("drafts") if isinstance(reply, dict) else None
    if not (isinstance(said, list) and len(said) == len(batch)):
        log.info("polish: the reply gave no drafts to compare; kept them as they were")
        return Polished(texts, left=[f for fs in found for f in fs])
    wanted = [k for k, f in enumerate(found) if f and str(said[k] or "").strip()]
    new = [spelling.respell(llm.undash(str(said[k]).strip()), facts.values.get("spelling_code")) for k in wanted]
    marked = attribute.mark(new, list(facts.items.values()), facts.motive, facts.allowed) if new else None
    out = Polished(list(texts))
    for k, text in zip(wanted, marked or [], strict=False):
        if kept(text, texts[k], batch[k].kind, facts, (*earlier, *out.texts[:k])):
            out.texts[k] = text
            out.polished += 1
    previous = list(earlier)
    for d, text in zip(batch, out.texts, strict=True):
        out.left += _findings(text, d.kind, facts, tuple(previous))
        previous.append(text)
    return out

"""Attribution: which items of the evidence plan each sentence of a freely written draft rests on, asked for
once the draft is written, checked with no AI and written into the draft as evidence markers.

`mark(texts, items, motive, allowed)` numbers the sentences of one or more drafts (a letter, a message, or a
set of answers), asks the AI once (prompt `sentence_attribute`) for each sentence's items and kind, keeps a
reply that holds (`read_reply`) and gives the drafts back with the markers `writing.cites` reads ("[E2]",
"[E2,E5]", "[M]"), so `writing.grounded` checks each sentence against only what it rests on. It gives None when
the reply does not hold, and the caller falls back to asking the writer for a marked draft
(`cites.MARK_NOTE`).

Design decisions, each with its reason:

- Write freely, attribute afterwards. Asked to end every sentence with a marker, a 27B local model wrote one
  fact per sentence, repeated facts across paragraphs and never named the project a sentence was about;
  graders held every pack at 2 of 5 for readability, though the markers cut misframings by half
  (2026-11-08). Drafts written freely read well but misframed more (2026-11-07), so the draft is written
  freely and its sentences are tied to the plan after.
- One call per document, its answers together: the reply is a few tokens per sentence, and one call keeps a
  pack's count down (`pack` counts them).
- The reply is checked with no AI, so a model cannot pass a sentence by how it labels it: an id the plan does
  not hold is dropped, and a sentence about the person (`cites.about_person`: the first person with a past
  tense, or a number the employer's words do not hold) is a fact even when the reply calls it a courtesy.
  A fact citing nothing then fails its check as citing no evidence, as a marked one did.
- A reply that leaves out any sentence, or is not JSON, fails as a whole: one that skips a sentence has
  lost count of the numbering, so its ids may sit on the wrong sentences.
- A motivation or courtesy sentence with no closing stop ("Hello,") gets no marker, as a marker there would
  make `cites.normalise` add a full stop to a greeting.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import check, cites
from jobhunt.writing.plan import Item

log = logging.getLogger(__name__)

PROMPT = "sentence_attribute"
FACT, MOTIVATION, COURTESY = "fact", "motivation", "courtesy"
KINDS = (FACT, MOTIVATION, COURTESY)
ID = re.compile(r"^E\d+$")
STOP = re.compile(r"[.!?]+[\"'\u2019\u201d)\]]*$")


@dataclass(frozen=True)
class Attribution:
    """What one sentence rests on: its plan items and its kind (`KINDS`)."""

    ids: tuple[str, ...]
    kind: str

    @property
    def marker(self) -> str:
        """The marker's inside for this sentence: "E2,E5", "M", or "" for a fact citing nothing."""
        if self.ids:
            return ",".join(self.ids)
        return "" if self.kind == FACT else cites.MOTIVE


def sentences(texts: list[str]) -> list[list[str]]:
    """The sentences of each draft as the request numbers them: `writing.check.sentences` of the draft as the
    person sees it."""
    return [check.sentences(cites.strip(t)) for t in texts]


def request(said: list[list[str]], items: list[Item], motive: Item | None = None) -> str:
    """The attribution request: the plan, the motivation pairing's item, then the sentences numbered across
    every draft."""
    lines, n = [], 0
    for d, draft in enumerate(said, 1):
        if len(said) > 1:
            lines.append(f"Draft {d}:")
        for sentence in draft:
            n += 1
            lines.append(f"{n}. {sentence}")
    plan = "\n".join(i.words() for i in items) or "none"
    pairing = f"\n\nThe reason for this employer rests on {motive.id}." if motive is not None else ""
    return f"Evidence plan:\n{plan}{pairing}\n\nSentences:\n" + "\n".join(lines)


def validated(ids: object, kind: object, sentence: str, known: set[str], allowed: str = "") -> Attribution:
    """One sentence's attribution as the checks with no AI allow it (see the module notes)."""
    given = [str(i).strip().upper() for i in ids] if isinstance(ids, list) else []
    kept = tuple(dict.fromkeys(i for i in given if ID.match(i) and i in known))
    dropped = [i for i in given if i not in kept]
    if dropped:
        log.info("attribution: dropped ids the plan does not hold: %s", ", ".join(dropped))
    said = str(kind or "").strip().lower()
    said = said if said in KINDS else FACT
    if said == COURTESY and (kept or cites.about_person(sentence, allowed)):
        said = FACT
    return Attribution(kept, said)


def _number(entry: object) -> int:
    """The sentence number an entry of a reply gives, 0 when it gives none."""
    said = str(entry.get("n") if isinstance(entry, dict) else "").strip()
    return int(said) if said.isdigit() else 0


def read_reply(reply: object, said: list[list[str]], known: set[str], allowed: str = "") -> list[Attribution] | None:
    """Each sentence's attribution, in order, when the reply gives every sentence; else None."""
    flat = [s for draft in said for s in draft]
    entries = reply.get("sentences") if isinstance(reply, dict) else None
    by_n: dict[int, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        n = _number(entry)
        if 1 <= n <= len(flat) and n not in by_n:
            by_n[n] = entry
    if len(by_n) != len(flat):
        log.info("attribution: the reply gave %d of %d sentences; falling back", len(by_n), len(flat))
        return None
    return [validated(by_n[n].get("ids"), by_n[n].get("kind"), s, known, allowed)
            for n, s in enumerate(flat, 1)]


def with_marker(sentence: str, marker: str) -> str:
    """`sentence` with "[marker]" before its closing stop; with none, after it for a fact and not at all for a
    motivation or courtesy (see the module notes)."""
    if not marker:
        return sentence
    stop = STOP.search(sentence)
    if stop:
        return f"{sentence[:stop.start()].rstrip()} [{marker}]{sentence[stop.start():]}"
    return sentence if marker == cites.MOTIVE else f"{sentence} [{marker}]"


def marked(text: str, said: list[str], marks: list[Attribution]) -> str | None:
    """`text` with each of its sentences `said` given its marker in place, its paragraphs and lines kept; None
    when a sentence cannot be found in it."""
    plain = cites.strip(text)
    out, at = [], 0
    for sentence, mark in zip(said, marks, strict=True):
        found = re.compile(r"\s+".join(map(re.escape, sentence.split()))).search(plain, at)
        if not found:
            return None
        out.append(plain[at:found.start()] + with_marker(found.group(0), mark.marker))
        at = found.end()
    return cites.normalise("".join(out) + plain[at:])


def mark(texts: list[str], items: list[Item], motive: Item | None = None, allowed: str = "") -> list[str] | None:
    """`texts` (the drafts of one document) with each sentence marked with what it rests on: one AI call, then
    `read_reply`; None when the reply does not hold. A draft with no sentence comes back as it is."""
    said = sentences(texts)
    if not any(said):
        return list(texts)
    reply = llm.chat_json(prompts.text(PROMPT), request(said, items, motive), role="main")
    marks = read_reply(reply, said, {i.id for i in items}, allowed)
    if marks is None:
        return None
    out, at = [], 0
    for text, draft in zip(texts, said, strict=True):
        done = marked(text, draft, marks[at:at + len(draft)])
        if done is None:
            return None
        out.append(done)
        at += len(draft)
    return out

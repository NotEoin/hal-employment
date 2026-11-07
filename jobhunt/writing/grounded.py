"""Each sentence of a marked draft checked against only the evidence it cites, then rewritten, cut or listed.

`problems(c, facts)` finds, with no AI, what is wrong with one sentence (`writing.cites.Cited`) given the
evidence plan's items it cites; `verdict(c, facts)` asks the AI about that sentence and those items alone
(prompt `sentence_check`); `check(c, facts)` is both. `ground(draft, facts, essential)` checks every sentence
of a draft, rewrites each that fails once (prompt `sentence_fix`), checks the rewrite, and takes out a
sentence still failing unless the draft needs it, when it is listed instead.

Design decisions, each with its reason:

- One sentence and its cited items per call, never a batch: the single verify call over a whole pack, with
  every fact beside every sentence, gave about 20 false alarms for 10 real problems in five packs, mostly a
  fact of one project held against a sentence about another (2026-11-07). A sentence sees only its own
  evidence, so a batch would put other sentences' evidence back beside it. The calls are short (one
  sentence, a few items, a few words back), and only sentences that pass the checks with no AI and do not
  restate their evidence (`drafts.stated_in`) are sent.
- The checks with no AI come first, and a sentence that fails one is not sent to the AI: a number or a
  known tool the cited items do not hold, degree or own-time words the cited cards' contexts disagree with
  (as `drafts.wrong_context`, but through the markers, so a project described rather than named is
  caught), and "I built" of a team's card with no word of the team or of the person's part. The checks the
  whole draft had (a finished course spoken of as to come, a tool on the wrong project, a project in the
  wrong place by name, what a module covered) run on each sentence too.
- A number counts in figures or words ("seven" is "7"), so "a team of seven" meets "team of 7".
- The AI's "unsupported" words are dropped when the cited items or the employer's words hold them: a reply
  quoting what the evidence says is a false alarm. "supported": false with every quoted word dropped is
  read as supported; a reply that is not JSON says nothing, so it fails no sentence.
- A reason for the employer ([M]) that states something about the person rests on the letter's motivation
  pairing's item (`writing.motive`), and is checked against it as if it cited it.
- A failing sentence is rewritten once with its own items (or the whole plan when it cited none), then
  checked again. One still failing is taken out unless the draft needs it (`essential`): a paragraph's only
  sentence, or an answer's first, which answers the question. A needed one is kept and listed.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import check as writing_check
from jobhunt.writing import cites, drafts, spelling, stage, taught
from jobhunt.writing.plan import Item

log = logging.getLogger(__name__)

CHECK_PROMPT, FIX_PROMPT = "sentence_check", "sentence_fix"
WORD_NUMBERS = {w: str(n) for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                                "thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
                                                "twenty".split())}
WORD_NUMBERS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
                     "ninety": "90"})
# "I built X" and its kin: the whole of a piece of work claimed by one person.
SOLO = re.compile(r"\bI (?:have |had )?(?:single-handedly |alone |myself |personally )?(built|designed|led|developed|"
                  r"created|wrote|made|implemented|programmed|coded|engineered|delivered|architected|produced|"
                  r"ran|launched|founded)\b", re.I)
# Words that say the work was shared, or name the person's part of it.
SHARED = re.compile(r"\b(we|our|us|team|teams|group|together|jointly|classmates|teammates|colleagues|others|"
                    r"as part of|part of a|contributed|my part|my role|my share|alongside)\b", re.I)
PART_SHARE = 0.5           # share of the person's part's own words a sentence must hold to name that part
NO_EVIDENCE = cites.NO_EVIDENCE
NOT_IN_PLAN = cites.NOT_IN_PLAN
ALONE = "says you did {card} alone; it was a team's work{part}"
NOT_SUPPORTED = "its evidence does not say: {spans}"
NOT_SUPPORTED_ANY = "its evidence does not say this"
WRONG_PLACE = "places the work where its evidence does not"
TOO_MUCH = "gives you more of the work than its evidence does"
REASON = re.compile(r"\(([^()]*)\)$")
Essential = Callable[[str, str], bool]


@dataclass(frozen=True)
class Facts:
    """What every sentence of one job's drafts is checked against."""

    items: dict[str, Item]             # the evidence plan, by id
    cards: list[dict] = field(default_factory=list)       # `profile.writing.cards`
    known: list[str] = field(default_factory=list)        # every tool a card or checked skill names
    allowed: str = ""                  # the employer's words and the stage line: numbers there are not claims
    now: stage.Stage | None = None
    modules: list[str] = field(default_factory=list)
    sources: tuple[str, ...] = ()      # the CV and the passages, for what a module covered
    values: dict = field(default_factory=dict)            # the person's prompt values (spelling)
    motive: Item | None = None         # the letter's motivation pairing's item (`writing.motive`), if any


@dataclass
class Grounded:
    """A draft after its sentence checks: its text (still marked), what is still wrong, and what was done."""

    text: str
    failed: list[str] = field(default_factory=list)
    rewrites: int = 0
    removed: int = 0
    marked: bool = True        # False for a draft with no markers, checked whole instead

    def key(self) -> tuple[bool, int, int]:
        """How a draft ranks against another of the same document, lowest best: marked first, then fewer
        sentences still failing, then fewer rewrites."""
        return (not self.marked, len(self.failed), self.rewrites)


def numbers(text: str) -> set[str]:
    """The numbers of a text, in figures, with words read as figures ("seven" is "7")."""
    return {WORD_NUMBERS.get(n, n).replace(",", "") for n in cites.numbers(text)}


def _evidence_text(items: list[Item]) -> str:
    return "\n".join(f"{i.text}\n{i.card}\n{i.role}\n{', '.join(i.tools)}" for i in items)


def _reason(found: list[str]) -> list[str]:
    return [m.group(1) for m in (REASON.search(f) for f in found) if m]


def _context_problem(text: str, items: list[Item]) -> str:
    """What is wrong with where a sentence places the work, given the contexts of the cards it cites."""
    contexts = {i.context for i in items if i.context in drafts.CLASHES}
    if not contexts or any(drafts.SAID[a].search(text) for c in contexts for a in drafts.AGREES[c]):
        return ""
    for word in ("degree", "personal"):
        if drafts.SAID[word].search(text) and all(word in drafts.CLASHES[c] for c in contexts):
            card = next((i.card for i in items if i.card), "the work")
            said = " or ".join(sorted(drafts.IS_WORDS[c] for c in contexts))
            return f"calls {card} {drafts.NOT_WORDS[word]}; its evidence says {said}"
    return ""


def _names_part(text: str, role: str) -> bool:
    """True when a sentence holds the person's part: at least `PART_SHARE` of the role's own words."""
    part = stage.TEAM_WORDS.sub("", role)
    own = {w for w in writing_check.words_of(part) if w not in drafts.LITTLE_WORDS}
    return bool(own) and len(own & set(writing_check.words_of(text))) >= PART_SHARE * len(own)


def _alone_problem(text: str, items: list[Item]) -> str:
    """What is wrong when a sentence says the person did a team's work alone."""
    team = [i for i in items if i.context == stage.TEAM or stage.team_words(i.role)]
    if not team or not SOLO.search(text) or SHARED.search(text) or any(_names_part(text, i.role) for i in team):
        return ""
    first = team[0]
    part = next((p for p in first.role.split("; ") if p and not stage.team_words(p)), "")
    return ALONE.format(card=first.card or "it", part=f"; your part: {part}" if part else "")


def cited_items(c: cites.Cited, facts: Facts) -> list[Item]:
    """The items a sentence rests on: those it cites, or for a reason sentence ([M]) that states something
    about the person, the motivation pairing's item."""
    items = [facts.items[i] for i in c.ids if i in facts.items]
    if not items and c.motive and facts.motive is not None and cites.about_person(c.text, facts.allowed):
        return [facts.motive]
    return items


def problems(c: cites.Cited, facts: Facts) -> list[str]:
    """What is wrong with one sentence, with no AI (see the module notes); [] when nothing is."""
    unknown = [i for i in c.ids if i not in facts.items]
    if unknown:
        return [NOT_IN_PLAN.format(ids=", ".join(unknown))]
    items = cited_items(c, facts)
    text = c.text
    out = []
    if not items and cites.about_person(text, facts.allowed):
        out.append(NO_EVIDENCE)
    if items:
        held = numbers(_evidence_text(items))
        out += [f"the number {n} is not in its evidence" for n in sorted(numbers(text) - held)]
        out += [f"{t} is not in its evidence" for t in dict.fromkeys(facts.known)
                if drafts.names_tool(text, t) and not drafts.names_tool(_evidence_text(items), t)]
        out += [p for p in (_context_problem(text, items), _alone_problem(text, items)) if p]
    if facts.now is not None and stage.ended_course(text, facts.now):
        out.append(f"the course ended in {facts.now.end or 'the past'}")
    out += _reason(drafts.misplaced_tools(text, facts.cards, facts.known))
    out += _reason(drafts.wrong_context(text, facts.cards))
    out += _reason(taught.unsupported(text, facts.modules, facts.sources))
    return list(dict.fromkeys(out))


def _held(span: str, where: str) -> bool:
    words = re.findall(r"\S+", span.strip().strip("\"'").rstrip(".,;:"))
    return bool(words) and re.search(r"\s+".join(map(re.escape, words)), where, re.I) is not None


def _request(c: cites.Cited, items: list[Item]) -> str:
    return f"Sentence: {c.text}\n\nEvidence:\n" + "\n".join(i.words() for i in items)


def verdict(c: cites.Cited, facts: Facts) -> list[str]:
    """What the AI finds wrong with one sentence, given only the items it rests on; [] when nothing is."""
    items = cited_items(c, facts)
    reply = llm.chat_json(prompts.text(CHECK_PROMPT), _request(c, items), role="main")
    if not isinstance(reply, dict):
        return []
    out = []
    if reply.get("supported") is False:
        given = [str(s) for s in reply.get("unsupported") or [] if str(s).strip()]
        where = f"{_evidence_text(items)}\n{facts.allowed}"
        spans = [s for s in given if not _held(s, where)]
        if spans:
            out.append(NOT_SUPPORTED.format(spans="; ".join(f'"{s}"' for s in spans)))
        elif not given:
            out.append(NOT_SUPPORTED_ANY)
    if reply.get("context_ok") is False:
        out.append(WRONG_PLACE)
    if reply.get("role_ok") is False:
        out.append(TOO_MUCH)
    return out


def check(c: cites.Cited, facts: Facts) -> list[str]:
    """What is wrong with one sentence: the checks with no AI, then, when they find nothing and the sentence
    rests on evidence (`cited_items`) it does not simply restate, one AI call."""
    found = problems(c, facts)
    items = cited_items(c, facts)
    if found or not items:
        return found
    if drafts.stated_in(c.text, "\n".join(i.text for i in items)):
        return []
    return verdict(c, facts)


def _pattern(sentence: str) -> re.Pattern:
    return re.compile(r"\s+".join(map(re.escape, sentence.split())))


def replace(text: str, sentence: str, new: str) -> str:
    """`text` with one sentence (as `writing.check.sentences` gives it) replaced by `new`, or taken out when
    `new` is ""; the paragraphs keep their breaks."""
    found = _pattern(sentence).search(text)
    if not found:
        return text
    if new:
        return text[:found.start()] + new + text[found.end():]
    out = text[:found.start()].rstrip(" \t") + (" " if text[:found.start()].strip() else "") \
        + text[found.end():].lstrip(" \t")
    return re.sub(r"\n[ \t]+\n", "\n\n", re.sub(r"[ \t]+\n", "\n", out)).strip()


def paragraph_of(text: str, sentence: str) -> str:
    """The paragraph of `text` that holds `sentence`."""
    for para in re.split(r"\n\s*\n", text or ""):
        if _pattern(sentence).search(para):
            return para.strip()
    return ""


def only_in_paragraph(text: str, sentence: str) -> bool:
    """True when `sentence` is the only sentence of its paragraph: a letter or message needs it."""
    return len(writing_check.sentences(paragraph_of(text, sentence))) <= 1


def first_of_answer(text: str, sentence: str) -> bool:
    """True when `sentence` is an answer's first, which answers the question, or its only one."""
    said = writing_check.sentences(cites.normalise(text))
    return not said or said[0] == sentence or len(said) == 1


def fix(c: cites.Cited, found: list[str], text: str, facts: Facts) -> str:
    """One sentence rewritten once by the AI with its evidence (the whole plan when it cited none); "" when the
    reply says its evidence supports nothing it was there to say."""
    items = cited_items(c, facts) or list(facts.items.values())
    request = (f"Sentence: {c.raw}\nWhat is wrong: {'; '.join(found)}\n\nEvidence:\n"
               + "\n".join(i.words() for i in items) + f"\n\nThe paragraph:\n{cites.strip(paragraph_of(text, c.raw))}")
    reply = llm.chat_json(prompts.text(FIX_PROMPT, spelling=facts.values.get("spelling", "")), request, role="writer")
    said = str(reply.get("sentence") or "").strip() if isinstance(reply, dict) else ""
    said = cites.normalise(spelling.respell(llm.undash(said), facts.values.get("spelling_code")))
    if said and not cites.has_markers(said) and c.ids:
        said = re.sub(r"([.!?]?)$", lambda m: f" [{','.join(c.ids)}]{m.group(1)}", said, count=1)
    return said


def ground(draft: str, facts: Facts, essential: Essential) -> Grounded:
    """`draft` with each sentence checked against its own evidence (`check`): a failing one is rewritten once
    (`fix`) and checked again, then taken out, or kept and listed when `essential(text, sentence)` says the
    draft needs it."""
    out = Grounded(draft)
    for c in cites.cited(draft):
        found = check(c, facts)
        if not found:
            continue
        new = fix(c, found, out.text, facts)
        out.rewrites += 1
        again = [p for n in cites.cited(new) for p in check(n, facts)] if new else found
        if new and not again:
            out.text = replace(out.text, c.raw, new)
        elif not essential(out.text, c.raw):
            out.text = replace(out.text, c.raw, "")
            out.removed += 1
        else:
            kept = new or c.raw
            out.text = replace(out.text, c.raw, kept)
            out.failed.append(f"{cites.strip(kept)} ({'; '.join(again)})")
        log.info("sentence check: %r failed (%s); now %r", c.text, "; ".join(found), new)
    return out

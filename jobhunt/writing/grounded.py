"""Each sentence of a marked draft checked against only the evidence it cites, then rewritten, cut or listed.

`problems(c, facts)` finds, with no AI, what is wrong with one sentence (`writing.cites.Cited`) given the
evidence plan's items it cites; `verdict(c, facts)` asks the AI about that sentence and those items alone
(prompt `sentence_check`); `check(c, facts)` is both. `ground(draft, facts, essential)` checks every sentence
of a draft, rewrites each that fails once in place (prompt `sentence_fix`), checks the rewrite, and lists
one still failing; a sentence is taken out only when no evidence supports it, and a paragraph that changed
is smoothed once (`smooth`). `lengthen(done, facts, words, essential)` adds a paragraph to a short letter, and
`unrepeat(draft, facts, essential, earlier)` treats a sentence saying again what an earlier one said (in its
own draft or in an `earlier` one) as `ground` treats a failing one, its rewrite resting on a plan item none of
them cites.

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
- A tool claimed for a project needs that project's own evidence (`_tool_problems`): a cited item tied to its
  card and not a skills list (`plan.Item.skills`), or the card's own names, summary and tools. "I used NumPy
  and pandas to build" a project passed on the CV's skills line though the project's card never names pandas
  (2026-11-07). A skills list may still support "I know pandas", which names no project.
- Wording that shows how a draft was made (an advert's requirement as a lead-in, "my CV lists", the person
  in the third person, a settings label pasted in: `writing.plain`) is a problem of the sentence like any
  other, so it gets the same one rewrite.
- Planned work is never done: a sentence in the first person and the past tense fails when every item it
  cites says the work was planned (`plan.Item.planned`), or a known tool it names is held only by its planned
  items ("I used Azure" from "we will use Azure", 2026-11-08). A module and its mark tied to a project or
  activity the module's own sources never name fails as `taught.joined` says.
- A number counts in figures or words ("seven" is "7"), so "a team of seven" meets "team of 7".
- The AI's "unsupported" words are dropped when the cited items or the employer's words hold them: a reply
  quoting what the evidence says is a false alarm. "supported": false with every quoted word dropped is
  read as supported; a reply that is not JSON says nothing, so it fails no sentence.
- A reason for the employer ([M]) that states something about the person rests on the letter's motivation
  pairing's item (`writing.motive`), and is checked against it as if it cited it.
- A failing sentence is fixed in place: rewritten once with its own items (or the whole plan when it cited
  none) and its whole paragraph, numbered, as context ("rewrite only sentence 3 ... keeping the paragraph
  flowing; you may name the project"), and the reply says which items it rests on, kept only when they are
  among those given (`attribute.validated`). It is then checked again. Taking failing sentences out left
  fact lists and paragraphs that jumped (graded packs, 2026-11-08), so a sentence is taken out only when the
  rewrite says its evidence supports nothing it was there to say, and then only when the draft does not
  need it (`essential`: a paragraph's only sentence, or an answer's first, which answers the question) and
  the paragraph reads without it (the next sentence does not point back at it, `flow.reads_without`). A
  rewrite still failing is kept and listed. A sentence still repeating an earlier one after its rewrite
  (`unrepeat`) is taken out instead, as it says nothing new.
- Rewrite, then smooth, never just delete: sentences taken out left "This project allowed me ...", one-sentence
  paragraphs and fragments in 15 graded packs, which graders marked down more than the claims the checks
  caught (2026-11-07). A paragraph that lost or changed a sentence is rewritten once from its kept sentences
  (prompt `paragraph_smooth`), keeping its evidence ids and adding none, and the rewrite is kept only when it
  cites the same ids and every sentence passes the checks with no AI (`holds`); else the paragraph stays as
  the sentence checks left it. A paragraph keeping a sentence that is listed is not smoothed, so the listed
  words stay the ones the person reads.
- A letter left under `flow.LENGTH_FLOOR` of the person's preferred length gets one paragraph more from the
  plan items it does not cite (`lengthen`, prompt `paragraph_add`), checked sentence by sentence like any
  other; letters were 105 to 216 words against a preferred 250 (2026-11-07).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import attribute, cites, drafts, flow, plain, repeats, spelling, stage, taught
from jobhunt.writing import check as writing_check
from jobhunt.writing.plan import Item

log = logging.getLogger(__name__)

CHECK_PROMPT, FIX_PROMPT = "sentence_check", "sentence_fix"
SMOOTH_PROMPT, ADD_PROMPT = "paragraph_smooth", "paragraph_add"
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
NOT_PROJECTS = "{tool} is not in the evidence for {card}; a skills list says what you know, not where you used it"
ONLY_SKILLS = "rests only on a skills list, which says nothing about {card}"
TOO_MUCH = "gives you more of the work than its evidence does"
REASON = re.compile(r"\(([^()]*)\)$")
REPEATS = 'already said as "{sentence}"'   # a sentence repeating an earlier one (`unrepeat`)
REPEATS_FACT = "says again what an earlier paragraph says ({ids})"   # its items all cited in one before it
REPEATS_LINE = 'uses your CV line "{line}" word for word, as an earlier draft does'
PLANNED = "says it was done; its evidence says it was only planned"
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
    names: tuple[str, ...] = ()        # the person's names, never a sentence's subject (`writing.plain`)
    labels: tuple[str, ...] = ()       # the settings wording a draft must never paste (`plain.settings_labels`)


@dataclass
class Grounded:
    """A draft after its sentence checks: its text (still marked), what is still wrong, and what was done."""

    text: str
    failed: list[str] = field(default_factory=list)
    rewrites: int = 0
    removed: int = 0
    marked: bool = True        # False for a draft with no markers, checked whole instead
    smoothed: int = 0          # paragraphs rewritten to read well after a sentence was rewritten or cut
    added: bool = False        # a paragraph was added to a short letter (`lengthen`)


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


def _projects(text: str, items: list[Item], facts: Facts) -> list[str]:
    """The cards a sentence is about: those its items are tied to and those it names (`drafts.names_card`)."""
    named = [c["name"] for c in facts.cards if c["name"].strip() and drafts.names_card(text, c, facts.cards)]
    return list(dict.fromkeys([*(i.card for i in items if i.card), *named]))


def _tool_problems(text: str, items: list[Item], facts: Facts) -> list[str]:
    """The tools of `facts.known` a sentence names that its evidence does not hold. For a sentence about a
    project, only that project's own evidence counts: its items that are not a skills list, and its card's
    names, summary and tools; a skills list alone never supports it."""
    projects = _projects(text, items, facts)
    if not projects:
        held = _evidence_text(items)
        return [f"{t} is not in its evidence" for t in dict.fromkeys(facts.known)
                if drafts.names_tool(text, t) and not drafts.names_tool(held, t)]
    own = [i for i in items if not i.skills and i.card in projects]
    cards = [c for c in facts.cards if c["name"] in projects]
    held = _evidence_text(own) + "\n" + "\n".join(" ".join([*drafts.card_names(c), c["summary"], *c["tools"]])
                                                     for c in cards)
    out = [ONLY_SKILLS.format(card=projects[0])] if items and all(i.skills for i in items) else []
    return out + [NOT_PROJECTS.format(tool=t, card=" or ".join(projects)) for t in dict.fromkeys(facts.known)
                  if drafts.names_tool(text, t) and not drafts.names_tool(held, t)]


def _planned_problem(text: str, items: list[Item], facts: Facts) -> str:
    """What is wrong when a sentence says planned work was done (see the module notes)."""
    planned = [i for i in items if i.planned]
    if not planned or not (cites.FIRST_PERSON.search(text) and cites.past_tense(text)):
        return ""
    done = [i for i in items if not i.planned]
    if not done:
        return PLANNED
    plans, dones = _evidence_text(planned), _evidence_text(done)
    only_planned = [t for t in dict.fromkeys(facts.known) if drafts.names_tool(text, t)
                    and drafts.names_tool(plans, t) and not drafts.names_tool(dones, t)]
    return PLANNED if only_planned else ""


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
        out += _tool_problems(text, items, facts)
        out += [p for p in (_context_problem(text, items), _alone_problem(text, items),
                            _planned_problem(text, items, facts)) if p]
    if facts.now is not None and stage.ended_course(text, facts.now):
        out.append(f"the course ended in {facts.now.end or 'the past'}")
    out += _reason(drafts.misplaced_tools(text, facts.cards, facts.known))
    out += _reason(drafts.wrong_context(text, facts.cards))
    out += _reason(taught.unsupported(text, facts.modules, facts.sources))
    out += _reason(taught.joined(text, facts.modules, facts.sources, facts.cards))
    out += plain.problems(text, list(facts.names), list(facts.labels))
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
    return len(cites.sentences(paragraph_of(text, sentence))) <= 1


def first_of_answer(text: str, sentence: str) -> bool:
    """True when `sentence` is an answer's first, which answers the question, or its only one."""
    said = cites.sentences(text)
    return not said or said[0] == sentence or len(said) == 1


def _numbered(paragraph: str, raw: str) -> tuple[int, str]:
    """`paragraph`'s sentences numbered as the person sees them, and the number of `raw` among them (0 when it
    is not there)."""
    said = cites.sentences(paragraph)
    at = said.index(raw) + 1 if raw in said else 0
    return at, "\n".join(f"{n}. {cites.strip(s)}" for n, s in enumerate(said, 1))


def fix(c: cites.Cited, found: list[str], text: str, facts: Facts, items: list[Item] | None = None) -> str:
    """One sentence rewritten once by the AI in place in its paragraph, with `items` (by default its own
    evidence, or the whole plan when it cited none), and attributed again (`attribute.validated`: the reply's
    ids that are among `items`); "" when the reply says its evidence supports nothing it was there to say."""
    items = items if items is not None else cited_items(c, facts) or list(facts.items.values())
    at, numbered = _numbered(paragraph_of(text, c.raw), c.raw)
    ids = ", ".join(i.id for i in items)
    request = (f"Sentence: {c.text}\nWhat is wrong: {'; '.join(found)}\n"
               f"Rewrite only sentence {at} of the paragraph so it states only what items {ids} support, keeping "
               "the paragraph flowing; you may name the project.\n\nEvidence:\n"
               + "\n".join(i.words() for i in items) + f"\n\nThe paragraph:\n{numbered}")
    reply = llm.chat_json(prompts.text(FIX_PROMPT, spelling=facts.values.get("spelling", "")), request, role="writer")
    reply = reply if isinstance(reply, dict) else {}
    said = str(reply.get("sentence") or "").strip()
    said = cites.normalise(spelling.respell(llm.undash(said), facts.values.get("spelling_code")))
    if said and not cites.has_markers(said):
        given = reply.get("ids") if "ids" in reply else list(c.ids)
        kind = attribute.FACT if given else attribute.MOTIVATION if c.motive else attribute.COURTESY
        mark = attribute.validated(given, kind, said, {i.id for i in items} | set(c.ids), facts.allowed)
        said = attribute.with_marker(said, mark.marker)
    return said


def _ids(text: str) -> set[str]:
    return {i for c in cites.cited(text) for i in c.ids}


def _ask_paragraph(prompt_id: str, request: str, facts: Facts) -> str:
    """One paragraph from the AI (`{"paragraph": ...}`), undashed, respelt and with its markers normalised; ""
    when the reply gives none."""
    reply = llm.chat_json(prompts.text(prompt_id, spelling=facts.values.get("spelling", "")), request, role="writer")
    said = str(reply.get("paragraph") or "").strip() if isinstance(reply, dict) else ""
    return cites.normalise(spelling.respell(llm.undash(said), facts.values.get("spelling_code"))) if said else ""


def holds(paragraph: str, facts: Facts) -> list[str]:
    """What is wrong with a paragraph, with no AI: each sentence's `problems`; [] when nothing is."""
    return [p for c in cites.cited(paragraph) for p in problems(c, facts)]


def smooth(paragraph: str, facts: Facts) -> str:
    """`paragraph` rewritten once by the AI (prompt `paragraph_smooth`) to read well from its kept sentences, or
    as it was when the reply changes its evidence ids or a sentence fails the checks with no AI (`holds`)."""
    ids = dict.fromkeys(i for c in cites.cited(paragraph) for i in c.ids)
    items = [facts.items[i] for i in ids if i in facts.items]
    request = f"Paragraph:\n{paragraph}\n\nEvidence:\n" + ("\n".join(i.words() for i in items) or "none")
    said = _ask_paragraph(SMOOTH_PROMPT, request, facts)
    if not said or said == paragraph:
        return paragraph
    if _ids(said) != _ids(paragraph) or holds(said, facts):
        log.info("paragraph smoothing: kept the paragraph; the rewrite changed its evidence or failed a check: %r",
                 said)
        return paragraph
    return said


Judge = Callable[[cites.Cited, str], list[str]]     # what is wrong with a sentence, given the text before it
Evidence = Callable[[cites.Cited, str], list[Item]]  # the items its rewrite may rest on, given the text before it


@dataclass(frozen=True)
class _Asks:
    """What `_settle` does with each sentence: how it is judged, how its rewrite is judged, the evidence its
    rewrite may rest on, and whether a rewrite still failing is taken out (`cut_failing`) or kept and listed."""

    judge: Judge
    recheck: Judge
    evidence: Evidence
    cut_failing: bool = False


def _replace_nth(text: str, sentence: str, nth: int, new: str) -> str:
    """`replace` of the `nth` (from 0) time `sentence` stands in `text`: a sentence written twice is two."""
    found = list(_pattern(sentence).finditer(text))
    if nth >= len(found):
        return text
    at = found[nth]
    head, tail = text[:at.start()], text[at.end():]
    marker = "\x00"
    return replace(head + marker + tail, marker, new)


def _settle_paragraph(paras: list[str], n: int, facts: Facts, essential: Essential, out: Grounded,
                      asks: _Asks) -> str:
    """Paragraph `n` of `paras` with each sentence `asks.judge` finds wrong rewritten once (`fix` with
    `asks.evidence`) and `asks.recheck`ed, else taken out or kept and listed (see `ground`), then smoothed once
    when a sentence was rewritten or taken out and none is kept failing."""
    judge, recheck, evidence = asks.judge, asks.recheck, asks.evidence
    said = cites.sentences(paras[n])
    now = list(said)                   # each sentence as it stands; "" once taken out
    text, changed, failing = paras[n], False, False
    for k, raw in enumerate(said):
        c = cites.cited(raw)[0]
        before = "\n\n".join([*paras[:n], " ".join(x for x in now[:k] if x)])
        found = judge(c, before)
        if not found:
            continue
        nth = sum(x == raw for x in now[:k])
        whole = "\n\n".join([*paras[:n], text, *paras[n + 1:]])
        items = evidence(c, before)
        new = fix(c, found, whole, facts, items) if items else ""
        out.rewrites += bool(items)
        again = [p for m in cites.cited(new) for p in recheck(m, before)] if new else found
        if new and not again:
            text, now[k], changed = _replace_nth(text, raw, nth, new), new, True
        elif (not new or asks.cut_failing) and not essential(whole, raw) and flow.reads_without(text, raw):
            text, now[k], changed = _replace_nth(text, raw, nth, ""), "", True
            out.removed += 1
        else:
            kept = new or raw
            text, now[k], failing = _replace_nth(text, raw, nth, kept), kept, True
            out.failed.append(f"{cites.strip(kept)} ({'; '.join(again)})")
        log.info("sentence check: %r failed (%s); now %r", c.text, "; ".join(found), new)
    if changed and not failing and text:
        smoothed = smooth(text, facts)
        out.smoothed += smoothed != text
        text = smoothed
    return text


def _settle(draft: str, facts: Facts, essential: Essential, asks: _Asks) -> Grounded:
    paras = flow.paragraphs(cites.normalise(draft))
    out = Grounded("")
    for n in range(len(paras)):
        paras[n] = _settle_paragraph(paras, n, facts, essential, out, asks)
    out.text = "\n\n".join(p for p in paras if p)
    return out


def ground(draft: str, facts: Facts, essential: Essential) -> Grounded:
    """`draft` with each sentence checked against its own evidence (`check`): a failing one is rewritten once in
    place (`fix`) and checked again, and kept and listed when it still fails. One whose evidence supports
    nothing is taken out when `essential(text, sentence)` says the draft does not need it and its paragraph
    reads without it (`flow.reads_without`), else kept and listed. A paragraph that lost or
    changed a sentence is smoothed once (`smooth`)."""
    def judge(c: cites.Cited, before: str) -> list[str]:
        return check(c, facts)

    return _settle(draft, facts, essential,
                   _Asks(judge, judge, lambda c, before: cited_items(c, facts) or list(facts.items.values())))


@dataclass(frozen=True)
class _Repeats:
    """What `unrepeat` asks of each sentence: the sentences of the `earlier` drafts, and the plan."""

    facts: Facts
    earlier: tuple[str, ...]

    def judge(self, c: cites.Cited, before: str) -> list[str]:
        """What `c` says again, as a problem: an earlier sentence of its draft or an earlier draft, the items of
        an earlier paragraph of its draft, or a CV line an earlier draft also copies word for word."""
        seen = [cites.strip(x) for e in (*self.earlier, before) for x in cites.sentences(e)]
        hit = repeats.first_said(c.text, seen)
        if hit:
            return [REPEATS.format(sentence=hit)]
        paragraphs_before = before.rsplit("\n\n", 1)[0] if "\n\n" in before else ""
        if c.ids and set(c.ids) <= _ids(paragraphs_before):
            return [REPEATS_FACT.format(ids=", ".join(c.ids))]
        line = repeats.verbatim(c.text, self.facts.sources[:1], self.earlier)
        return [REPEATS_LINE.format(line=line)] if line else []

    def recheck(self, m: cites.Cited, before: str) -> list[str]:
        """What is wrong with a rewrite: still a repeat, else its sentence checks (`check`)."""
        return self.judge(m, before) or check(m, self.facts)

    def evidence(self, c: cites.Cited, before: str) -> list[Item]:
        """The plan items neither the drafts so far nor the sentence cite."""
        used = _ids("\n\n".join([*self.earlier, before]))
        return [i for i in self.facts.items.values() if i.id not in used and i.id not in c.ids]


def unrepeat(draft: str, facts: Facts, essential: Essential, earlier: tuple[str, ...] = ()) -> Grounded:
    """`draft` with each sentence that says nearly what an earlier one says (`repeats.same`: before it in the
    draft, or in one of the `earlier` drafts of the pack), cites only items an earlier paragraph cites, or copies
    a CV line an earlier draft copies (`repeats.verbatim`) rewritten once from a plan item none of them cites,
    and checked (`check`) and compared again; one still repeating is taken out as `ground` takes a sentence
    out, else kept and listed."""
    asks = _Repeats(facts, earlier)
    return _settle(draft, facts, essential, _Asks(asks.judge, asks.recheck, asks.evidence, cut_failing=True))


def lengthen(done: Grounded, facts: Facts, words: int | None, essential: Essential) -> Grounded:
    """A letter under `flow.LENGTH_FLOOR` of the preferred length `words` with one paragraph added from the plan
    items it does not cite (prompt `paragraph_add`), checked as `ground` checks any paragraph and put before
    its last paragraph; the letter as it was when it is long enough, nothing is unused or nothing holds."""
    unused = [i for i in facts.items.values() if i.id not in _ids(done.text)]
    if not (done.marked and unused and flow.short(done.text, words)):
        return done
    request = (f"The letter:\n{done.text}\n\nUnused evidence:\n" + "\n".join(i.words() for i in unused)
               + f"\n\nThe letter has {flow.word_count(done.text)} words; the applicant wants about {words}.")
    said = _ask_paragraph(ADD_PROMPT, request, facts)
    added = ground(said, facts, essential) if said else None
    if added is None or not cites.strip(added.text).strip():
        return done
    paras = flow.paragraphs(done.text)
    at = len(paras) - 1 if len(paras) > 1 else len(paras)
    paras[at:at] = flow.paragraphs(added.text)
    return Grounded("\n\n".join(paras), done.failed + added.failed, done.rewrites + added.rewrites,
                    done.removed + added.removed, smoothed=done.smoothed + added.smoothed, added=True)

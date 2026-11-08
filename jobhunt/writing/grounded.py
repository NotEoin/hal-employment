"""Each sentence of a marked draft checked against only the evidence it cites, then rewritten, cut or listed.

`problems(c, facts)` finds, with no AI, what is wrong with one sentence (`writing.cites.Cited`) given the
evidence plan's items it cites; `verdict(c, facts)` asks the AI about that sentence and those items alone
(prompt `sentence_check`), and `verdicts(cs, facts)` asks about the sentences of one paragraph in one call
(prompt `paragraph_check`), each against its own items; `check(c, facts)` is the checks with no AI, then one
call. `ground(draft, facts, essential)` checks every sentence
of a draft, rewrites each that fails once (prompt `sentence_fix`), checks the rewrite, and takes out a
sentence still failing unless the draft needs it, when it is listed instead; a paragraph that changed is
smoothed once (`smooth`). `lengthen(done, facts, words, essential)` adds a paragraph to a short letter, and
`unrepeat(draft, facts, essential, earlier)` treats a sentence saying again what an earlier one said (in its
own draft or in an `earlier` one) as `ground` treats a failing one, its rewrite resting on a plan item none of
them cites.

Design decisions, each with its reason:

- Each sentence is judged against its own cited items only, never every fact: the single verify call over a
  whole pack, with every fact beside every sentence, gave about 20 false alarms for 10 real problems in five
  packs, mostly a fact of one project held against a sentence about another (2026-11-07). Only sentences
  that pass the checks with no AI and do not restate their evidence are sent (`needs_ai`): those whose
  items hold `drafts.CV_SHARE` of their words (`drafts.stated_in`), and those whose every content word,
  by its root (`_root`), is in their items (`restates`). The sentences of one paragraph that are left go in
  one call (`verdicts`, prompt `paragraph_check`), each numbered with its own items under it and the prompt
  saying never to judge one by another's; a paragraph with one such sentence uses `sentence_check`. A pack
  made about 55 calls, most of them one per sentence (2026-11-08).
- The checks with no AI come first, and a sentence that fails one is not sent to the AI: a number or a
  known tool the cited items do not hold, degree or own-time words the cited cards' contexts disagree with
  (as `drafts.wrong_context`, but through the markers, so a project described rather than named is
  caught), and "I built" of a team's card with no word of the team or of the person's part. The checks the
  whole draft had (a finished course spoken of as to come, a tool on the wrong project, a project in the
  wrong place by name, what a module covered) run on each sentence too, and a module's mark tied to a
  project or activity its own entry never names (`taught.joined`), and one card's tool or purpose given to a
  card of a similar name (`drafts.crossed`).
- A tool claimed for a project needs that project's own evidence (`_tool_problems`): a cited item tied to its
  card and not a skills list (`plan.Item.skills`), or the card's own names, summary and tools. "I used NumPy
  and pandas to build" a project passed on the CV's skills line though the project's card never names pandas
  (2026-11-07). A skills list may still support "I know pandas", which names no project.
- Wording that shows how a draft was made (an advert's requirement as a lead-in, "my CV lists", the person
  in the third person, a settings label pasted in: `writing.plain`) is a problem of the sentence like any
  other, so it gets the same one rewrite.
- A sentence resting on one project's items says which project (`unnamed`): "I wrote a 113-test suite that
  ..." left the reader guessing in every graded pack (2026-11-08). When neither it nor the sentences before it
  in its paragraph, back to one about another project, name the card (or an alias), a sentence opening "I
  <verb>" is given "For <card>, " with no AI (`name_projects`); any other is rewritten once, and kept as it
  was when the rewrite fails, as it says nothing untrue.
- A plan is not what was done: a sentence citing only items that quote a plan (`plan.Item.planned`) fails
  unless it says the thing was planned (`PLANNED_ONLY`), and a tool a sentence says was used must be in
  items that are not plans (2026-11-08).
- A number counts in figures or words ("seven" is "7"), so "a team of seven" meets "team of 7".
- The AI's "unsupported" words are dropped when the cited items or the employer's words hold them: a reply
  quoting what the evidence says is a false alarm. "supported": false with every quoted word dropped is
  read as supported; a reply that is not JSON says nothing, so it fails no sentence.
- A reason for the employer ([M]) that states something about the person rests on the letter's motivation
  pairing's item (`writing.motive`), and is checked against it as if it cited it.
- A failing sentence is rewritten once with its own items (or the whole plan when it cited none), then
  checked again. One still failing is taken out unless the draft needs it (`essential`): a paragraph's only
  sentence, or an answer's first, which answers the question, or the paragraph does not read without it (the
  next sentence points back at it, `flow.reads_without`). A needed one is kept and listed.
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
from jobhunt.writing import check as writing_check
from jobhunt.writing import cites, drafts, flow, plain, planned, repeats, spelling, stage, taught
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
PLANNED_ONLY = "its evidence is a plan, not something done; say it was planned, or leave it out"
REASON = re.compile(r"\(([^()]*)\)$")
REPEATS = 'already said as "{sentence}"'   # a sentence repeating an earlier one (`unrepeat`)
# A sentence telling again of a project an earlier paragraph told with the same evidence (`unrepeat`).
REPEATS_CARD = "tells again what an earlier paragraph says of {card}; use other evidence or leave it out"
UNNAMED = "does not say which project it is about; name {card}"   # (`unnamed`)
# "I wrote", "I also built": a sentence that can open "For <project>, " as it stands. Words of wanting or feeling
# ("I hope", "I would") are left to the rewrite.
ON_A_PROJECT = re.compile(r"^I\s+(?:(?:also|then|later|first|\w+ly)\s+)?(?!(?:am|would|will|can|could|should|"
                          r"hope|believe|feel|want|look|enjoy|love|like)\b)[a-z]+\b")
BATCH_PROMPT = "paragraph_check"
ROOT_MIN = 3               # letters a word keeps when its ending is taken off ("uses" is "use", "is" stays)
ENDINGS = ("ing", "ed", "es", "s")
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


def _projects(text: str, items: list[Item], facts: Facts) -> list[str]:
    """The cards a sentence is about: those its items are tied to and those it names (`drafts.names_card`)."""
    named = [c["name"] for c in drafts.named_cards(text, facts.cards)]
    return list(dict.fromkeys([*(i.card for i in items if i.card), *named]))


def _tool_problems(text: str, items: list[Item], facts: Facts) -> list[str]:
    """The tools of `facts.known` a sentence names that its evidence does not hold. For a sentence about a
    project, only that project's own evidence counts: its items that are not a skills list, and its card's
    names, summary and tools; a skills list alone never supports it."""
    if not planned.said(text):
        items = [i for i in items if not i.planned]   # a plan never backs a tool said to be used
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


def cited_items(c: cites.Cited, facts: Facts) -> list[Item]:
    """The items a sentence rests on: those it cites, or for a reason sentence ([M]) that states something
    about the person, the motivation pairing's item."""
    items = [facts.items[i] for i in c.ids if i in facts.items]
    if not items and c.motive and facts.motive is not None and cites.about_person(c.text, facts.allowed):
        return [facts.motive]
    return items


def _project_cards(facts: Facts) -> list[dict]:
    return [c for c in facts.cards if c["kind"].startswith("project") and c["name"].strip()]


def _project_of(c: cites.Cited, facts: Facts) -> dict | None:
    """The one project card every item a sentence cites with a card is about; None for none or several."""
    named = {facts.items[i].card for i in c.ids if i in facts.items and facts.items[i].card}
    return next((p for p in _project_cards(facts) if {p["name"]} == named), None)


def unnamed(c: cites.Cited, earlier: list[str], facts: Facts) -> dict | None:
    """The project card a sentence's items are all about when the reader cannot tell it: neither the sentence
    nor the `earlier` sentences of its paragraph name it, back to one about another project."""
    card = _project_of(c, facts)
    if card is None:
        return None
    projects = _project_cards(facts)
    for raw in [c.raw, *reversed(earlier)]:
        said = cites.cited(raw)[:1]
        if not said:
            continue
        named = drafts.named_cards(said[0].text, projects)
        if card in named:
            return None
        if named or _project_of(said[0], facts) not in (None, card):
            return card
    return card


def name_projects(draft: str, facts: Facts) -> str:
    """`draft` with "For <project>, " put before each sentence that leaves its project unnamed (`unnamed`) and
    opens "I <verb>" ("I wrote a 113-test suite" of one card's items); other such sentences are left to
    their one rewrite."""
    paras = flow.paragraphs(cites.normalise(draft))
    for n, para in enumerate(paras):
        now: list[str] = []
        for raw in cites.sentences(para):
            card = unnamed(cites.cited(raw)[0], now, facts)
            if card is not None and ON_A_PROJECT.match(raw):
                named = f"For {card['name']}, {raw}"
                para = _replace_nth(para, raw, sum(_pattern(raw).search(x) is not None for x in now), named)
                raw = named
            now.append(raw)
        paras[n] = para
    return "\n\n".join(paras)


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
        out += [p for p in (_context_problem(text, items), _alone_problem(text, items)) if p]
        if all(i.planned for i in items) and not planned.said(text):
            out.append(PLANNED_ONLY)
    if facts.now is not None and stage.ended_course(text, facts.now):
        out.append(stage.ended_said(facts.now))
    out += _reason(drafts.misplaced_tools(text, facts.cards, facts.known))
    out += _reason(drafts.wrong_context(text, facts.cards))
    out += _reason(drafts.crossed(text, facts.cards))
    out += _reason(taught.unsupported(text, facts.modules, facts.sources))
    out += _reason(taught.joined(text, facts.modules, facts.sources, facts.cards))
    out += plain.problems(text, list(facts.names), list(facts.labels))
    return list(dict.fromkeys(out))


def _held(span: str, where: str) -> bool:
    words = re.findall(r"\S+", span.strip().strip("\"'").rstrip(".,;:"))
    return bool(words) and re.search(r"\s+".join(map(re.escape, words)), where, re.I) is not None


def _request(c: cites.Cited, items: list[Item]) -> str:
    return f"Sentence: {c.text}\n\nEvidence:\n" + "\n".join(i.words() for i in items)


def _root(word: str) -> str:
    """A word without its ending ("records", "recorded" and "recording" are "record"; "guide" is "guid")."""
    for end in ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= ROOT_MIN:
            word = word[:-len(end)]
            break
    return word[:-1] if word.endswith("e") and len(word) > ROOT_MIN else word


def _roots(text: str) -> set[str]:
    return {_root(w) for w in re.findall(r"[a-z0-9+#]{2,}", (text or "").lower()) if w not in drafts.LITTLE_WORDS}


def restates(text: str, items: list[Item]) -> bool:
    """True when every content word of a sentence is in the items it cites (their quotes, cards, roles and
    tools), each compared by its root (`_root`): it says nothing they do not."""
    own = _roots(text)
    return bool(own) and own <= _roots(_evidence_text(items))


def needs_ai(c: cites.Cited, facts: Facts) -> bool:
    """True when a sentence passes the checks with no AI and rests on evidence it does not restate (`restates`,
    `drafts.stated_in`), so only the AI can say more."""
    items = cited_items(c, facts)
    if not items or problems(c, facts):
        return False
    return not (drafts.stated_in(c.text, "\n".join(i.text for i in items)) or restates(c.text, items))


def _found(reply: dict, items: list[Item], facts: Facts) -> list[str]:
    """What one reply of the sentence check finds wrong; quoted words the items or the employer hold are no
    problem."""
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


def verdict(c: cites.Cited, facts: Facts) -> list[str]:
    """What the AI finds wrong with one sentence, given only the items it rests on; [] when nothing is."""
    items = cited_items(c, facts)
    reply = llm.chat_json(prompts.text(CHECK_PROMPT), _request(c, items), role="main")
    return _found(reply, items, facts) if isinstance(reply, dict) else []


def verdicts(cs: list[cites.Cited], facts: Facts) -> list[list[str]]:
    """What the AI finds wrong with each of the sentences of one paragraph, in one call, each against only the
    items it rests on; a sentence the reply leaves out or answers wrongly is [] (see the module notes)."""
    if len(cs) == 1:
        return [verdict(cs[0], facts)]
    items = [cited_items(c, facts) for c in cs]
    request = "\n\n".join(f"Sentence {n}: {c.text}\nEvidence for sentence {n}:\n" + "\n".join(i.words() for i in its)
                           for n, (c, its) in enumerate(zip(cs, items, strict=True), 1))
    reply = llm.chat_json(prompts.text(BATCH_PROMPT), request, role="main")
    said = reply.get("sentences") if isinstance(reply, dict) else None
    by_n = {}
    for one in said if isinstance(said, list) else []:
        n = one.get("n") if isinstance(one, dict) else None
        if isinstance(n, int) and 1 <= n <= len(cs):
            by_n[n] = one
    return [_found(by_n[n], its, facts) if n in by_n else [] for n, its in enumerate(items, 1)]


def check(c: cites.Cited, facts: Facts) -> list[str]:
    """What is wrong with one sentence: the checks with no AI, then, when they find nothing and the sentence
    rests on evidence it does not simply restate (`needs_ai`), one AI call."""
    return verdict(c, facts) if needs_ai(c, facts) else problems(c, facts)


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


def fix(c: cites.Cited, found: list[str], text: str, facts: Facts, items: list[Item] | None = None) -> str:
    """One sentence rewritten once by the AI with `items` (by default its own evidence, or the whole plan when it
    cited none); "" when the reply says its evidence supports nothing it was there to say."""
    items = items if items is not None else cited_items(c, facts) or list(facts.items.values())
    request = (f"Sentence: {c.raw}\nWhat is wrong: {'; '.join(found)}\n\nEvidence:\n"
               + "\n".join(i.words() for i in items) + f"\n\nThe paragraph:\n{cites.strip(paragraph_of(text, c.raw))}")
    reply = llm.chat_json(prompts.text(FIX_PROMPT, spelling=facts.values.get("spelling", "")), request, role="writer")
    said = str(reply.get("sentence") or "").strip() if isinstance(reply, dict) else ""
    said = cites.normalise(spelling.respell(llm.undash(said), facts.values.get("spelling_code")))
    if said and not cites.has_markers(said) and c.ids:
        said = re.sub(r"([.!?]?)$", lambda m: f" [{','.join(c.ids)}]{m.group(1)}", said, count=1)
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


def _only_unnamed(found: list[str]) -> bool:
    """True when all that is wrong with a sentence is the project it leaves unnamed, which never cuts it."""
    return bool(found) and all(f.startswith(UNNAMED.split("{", 1)[0]) for f in found)


Judge = Callable[[cites.Cited, str], list[str]]     # what is wrong with a sentence, given the text before it
Evidence = Callable[[cites.Cited, str], list[Item]]  # the items its rewrite may rest on, given the text before it


def _replace_nth(text: str, sentence: str, nth: int, new: str) -> str:
    """`replace` of the `nth` (from 0) time `sentence` stands in `text`: a sentence written twice is two."""
    found = list(_pattern(sentence).finditer(text))
    if nth >= len(found):
        return text
    at = found[nth]
    head, tail = text[:at.start()], text[at.end():]
    marker = "\x00"
    return replace(head + marker + tail, marker, new)


@dataclass
class _Batched:
    """The sentence checks of `ground`, with the AI's verdicts on one paragraph's sentences asked in one call
    (`verdicts`) before they are judged in turn."""

    facts: Facts
    said: dict[str, list[str]] = field(default_factory=dict)

    def prepare(self, paragraph: str) -> None:
        """Asks the AI, in one call, about every sentence of `paragraph` that needs it (`needs_ai`)."""
        ask = [c for raw in cites.sentences(paragraph) for c in cites.cited(raw)[:1] if needs_ai(c, self.facts)]
        self.said = dict(zip((c.raw for c in ask), verdicts(ask, self.facts), strict=True)) if ask else {}

    def judge(self, c: cites.Cited, before: str) -> list[str]:
        """What is wrong with a sentence: the verdict asked for it in its paragraph's call, else `check`, and
        a project it leaves unnamed (`unnamed`)."""
        found = self.said.pop(c.raw) if c.raw in self.said else check(c, self.facts)
        card = unnamed(c, cites.sentences(before.rsplit("\n\n", 1)[-1]), self.facts)
        return found + ([UNNAMED.format(card=card["name"])] if card else [])


def _settle_paragraph(paras: list[str], n: int, facts: Facts, essential: Essential, out: Grounded,
                      judge: Judge, recheck: Judge, evidence: Evidence) -> str:
    """Paragraph `n` of `paras` with each sentence `judge` finds wrong rewritten once (`fix` with `evidence`) and
    `recheck`ed, else taken out or kept and listed (see `ground`), then smoothed once when a sentence was
    rewritten or taken out and none is kept failing."""
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
        elif _only_unnamed(found):
            pass                       # a true sentence that names no project stays as it was
        elif not essential(whole, raw) and flow.reads_without(text, raw):
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


def _settle(draft: str, facts: Facts, essential: Essential, judge: Judge, recheck: Judge,
            evidence: Evidence, prepare: Callable[[str], None] | None = None) -> Grounded:
    paras = flow.paragraphs(cites.normalise(draft))
    out = Grounded("")
    for n in range(len(paras)):
        if prepare is not None:
            prepare(paras[n])
        paras[n] = _settle_paragraph(paras, n, facts, essential, out, judge, recheck, evidence)
    out.text = "\n\n".join(p for p in paras if p)
    return out


def ground(draft: str, facts: Facts, essential: Essential) -> Grounded:
    """`draft` with each sentence checked against its own evidence (`check`): a failing one is rewritten once
    (`fix`) and checked again, then taken out when `essential(text, sentence)` says the draft does not need it
    and its paragraph reads without it (`flow.reads_without`), else kept and listed. A paragraph that lost or
    changed a sentence is smoothed once (`smooth`). The AI's checks of one paragraph's sentences are one call
    (`verdicts`); a rewrite is checked on its own. A lead-in from the advert that can simply be taken out is
    taken out first (`plain.unlead`), and a project left unnamed is named (`name_projects`), with no AI; a
    sentence that leaves its project unnamed otherwise is rewritten once, and kept as it was if that fails."""
    batch = _Batched(facts)
    draft = name_projects(plain.unlead(draft, facts.cards), facts)
    return _settle(draft, facts, essential, batch.judge, lambda c, before: check(c, facts),
                   lambda c, before: cited_items(c, facts) or list(facts.items.values()), batch.prepare)


@dataclass(frozen=True)
class _Repeats:
    """What `unrepeat` asks of each sentence: the sentences of the `earlier` drafts, and the plan."""

    facts: Facts
    earlier: tuple[str, ...]

    def judge(self, c: cites.Cited, before: str) -> list[str]:
        """The earlier sentence `c` says again, before it in its draft or in an earlier draft, or the project it
        tells again with evidence a paragraph two or more before it cites (`repeats.card_again`), as a
        problem."""
        seen = [cites.strip(x) for e in (*self.earlier, before) for x in cites.sentences(e)]
        hit = repeats.first_said(c.text, seen)
        if hit:
            return [REPEATS.format(sentence=hit)]
        card_of = {i.id: i.card for i in self.facts.items.values()}
        card = repeats.card_again(c.ids, before.split("\n\n")[:-2], card_of)
        return [REPEATS_CARD.format(card=card)] if card else []

    def recheck(self, m: cites.Cited, before: str) -> list[str]:
        """What is wrong with a rewrite: still a repeat, else its sentence checks (`check`)."""
        return self.judge(m, before) or check(m, self.facts)

    def evidence(self, c: cites.Cited, before: str) -> list[Item]:
        """The plan items neither the drafts so far nor the sentence cite."""
        used = _ids("\n\n".join([*self.earlier, before]))
        return [i for i in self.facts.items.values() if i.id not in used and i.id not in c.ids]


def unrepeat(draft: str, facts: Facts, essential: Essential, earlier: tuple[str, ...] = ()) -> Grounded:
    """`draft` with each sentence that says nearly what an earlier one says (`repeats.same`: before it in the
    draft, or in one of the `earlier` drafts of the pack) rewritten once from a plan item none of them cites,
    and checked (`check`) and compared again; one still repeating is taken out as `ground` takes a sentence
    out, else kept and listed."""
    asks = _Repeats(facts, earlier)
    return _settle(draft, facts, essential, asks.judge, asks.recheck, asks.evidence)


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

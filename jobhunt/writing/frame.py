"""A letter's opening and closing: drafted by the model from the fixed facts of who is writing, the role and the
reason for this employer, and checked like a paragraph.

`Facts` holds what the two paragraphs say (`pack.letter_frame` fills it), `settle(facts, scope)` asks the AI once
for both (prompt `letter_frame`), checks each with no AI (`problems`), asks once more when either fails, and
returns them (`Framed`).

Design decisions, each with its reason:

- The opening says who is writing (course, grade, institution and year, or final-year student, from
  `writing.opening.described`), the role and the one reason for this employer (`writing.motive`): letters
  lacked an introduction or buried it in their fourth paragraph, and one said "finishing my degree in 2026"
  after graduation (2026-11-08). The model writes it from those facts, never a template, as the person
  rejected pre-approved statements; the checks require the role and the institution.
- The checks are a paragraph's (`writing.paragraphs`) against the frame's own facts: a number or a known tool
  they lack, a course that has ended spoken of as running, the wording of `writing.plain` (the stock links
  among it), a sentence restating the advert or said twice, and the writing rules' high hits.
- A module the job's requirements name and no paragraph shows may be one clause of the opening, with its mark
  (`Facts.modules`, `plan.relevant_modules`): letters to an employer stressing security missed the person's
  high marks in security modules (2026-11-08). It is never a paragraph of its own while projects exist.
- The opening's reason for this employer is one concrete thing about the employer (`motive_problems`,
  2026-11-08): its product, its mission or a fact from the company brief, in the writer's own words, with one
  specific word of the employer's fact. It names no project or job of the person's (`NAMES_WORK`): openings
  told a project that a paragraph then told again (5 cases in 15 graded packs), and the two that escaped the
  readability cap gave the name, the role and the reason alone and left the projects to the paragraphs. The
  stock links ("matches my", "fits my") and the advert's lead-ins ("Your requirement to ...", "Your work on
  ...") are `plain.STOCK` and `plain.LEAD_IN`.
- The frame never retells the middle (`retold`, `Facts.middle`, 2026-11-08): a sentence of the opening or
  closing that says nearly what a paragraph's sentence says, shares `RETOLD_RUN` own words in a row with it
  or gives one of its numbers is a problem, drafted again with the frame's one redraft.
- One call drafts both paragraphs, and one more when either fails, so a letter's frame costs one or two calls.
  An opening still failing is the message's built sentence (`opening.Opening`, from the settings and the
  checked course), which needs no check; a closing still failing is left out, as a letter reads without it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from jobhunt.writing import check as writing_check
from jobhunt.writing import drafts, motive, opening, plain, repeats, stage
from jobhunt.writing.motive import Pairing
from jobhunt.writing.paragraphs import (
    ECHO_SAID,
    NUMBER_SAID,
    REPEAT_SAID,
    TOOL_SAID,
    Problem,
    Scope,
    ask,
    clean,
    numbers,
)

log = logging.getLogger(__name__)

PROMPT = "letter_frame"
WHAT = "the letter's opening or closing"   # how a problem names what it found it in
ROLE_MISSING = "does not name the role"
WHO_MISSING = "does not say who is writing ({who})"
NAMES_WORK = "names {work}; the opening gives a reason about the employer and leaves your work to the paragraphs"
MOTIVE_EMPLOYER = 'does not say what of the employer draws you ("{fact}")'
RETOLD_SAID = "retells what the paragraph about {work} says; leave it to that paragraph"
STEM = 5                   # letters two words share to be one word ("triage" and "triaging")
RETOLD_RUN = 4             # own words in a row a frame sentence shares with a paragraph to retell it: a name aside,
                           # three ("offline notes assistant") can name the work, four tell a fact of it


@dataclass(frozen=True)
class Facts:
    """The fixed facts of a letter's opening and closing: who is writing, the role and the reason for this
    employer."""

    name: str                  # the person's name, "" when unknown
    who: str                   # `opening.described`: "a first-class History graduate from Made-up University (2026)"
    title: str
    employer: str
    pairing: Pairing | None = None
    told: tuple[str, ...] = ()  # the names of the work the letter's middle tells, never named in the frame
    institution: str = ""
    modules: tuple[str, ...] = ()  # "Name (mark)" of modules the job names that no paragraph shows
    middle: tuple[tuple[str, str], ...] = ()  # (name, text) of each paragraph of the middle, never to retell

    def request(self, scope: Scope) -> str:
        """The request: who is writing, the role, the reason, what the middle tells and today."""
        reason = ("The reason for this employer: none was found in the applicant's facts; give none beyond the role."
                  if self.pairing is None else
                  f"The reason for this employer: the employer's \"{self.pairing.fact}\". Say one concrete thing about "
                  "the employer from it (its product, its mission or this fact) in your own words.")
        who = ", ".join(p for p in (self.name, self.who) if p) or "the applicant"
        lines = [f"Who is writing: {who}", f"The role: {self.title} at {self.employer}", reason]
        if self.told:
            lines.append(f"The letter's middle tells (never name or retell them): {'; '.join(self.told)}")
        if self.modules:
            lines.append("Modules of the applicant's that this job asks about and no paragraph shows (the opening "
                         f"may name them and their marks in one clause): {'; '.join(self.modules)}")
        lines.append(stage.today_lines(scope.now) if scope.now else "")
        if scope.request:
            lines.append(f"The applicant asked for this change: {scope.request}")
        return "\n".join(line for line in lines if line)

    def words(self) -> str:
        """Every word the frame's facts hold."""
        pair = self.pairing.fact if self.pairing else ""
        return "\n".join([self.name, self.who, self.title, self.employer, pair, *self.told, *self.modules])


@dataclass
class Framed:
    """A letter's opening and closing after their checks, and the calls they took."""

    opening: str = ""
    closing: str = ""
    calls: int = 0
    redrafted: bool = False


def problems(text: str, facts: Facts, scope: Scope, first: bool) -> list[Problem]:
    """What is wrong with the opening (`first`) or the closing, with no AI (see the module notes)."""
    said = writing_check.sentences(text)
    where = f"{facts.words()}\n{scope.allowed}"
    out = []
    for k, s in enumerate(said):
        found = [NUMBER_SAID.format(n=n, card=WHAT) for n in sorted(numbers(s) - numbers(where))]
        found += [TOOL_SAID.format(tool=t, card=WHAT) for t in dict.fromkeys(scope.known)
                  if t.strip() and drafts.names_tool(s, t) and not drafts.names_tool(where, t)]
        if scope.now is not None and stage.ended_course(s, scope.now):
            found.append(stage.ended_said(scope.now))
        found += plain.problems(s, list(scope.person), list(scope.labels))
        if scope.advert and drafts.echoes(s, scope.advert, scope.brief, scope.names):
            found.append(ECHO_SAID)
        if any(repeats.same(s, b) for b in said[:k]):
            found.append(REPEAT_SAID)
        found += [RETOLD_SAID.format(work=work) for work in retold(s, facts)]
        out += [Problem(s, r) for r in dict.fromkeys(found)]
    if first and not re.search(re.escape(facts.title.strip()), text, re.I):
        out.append(Problem("", ROLE_MISSING))
    if first and facts.institution and facts.institution.lower() not in text.lower():
        out.append(Problem("", WHO_MISSING.format(who=facts.who)))
    if first:
        out += [Problem("", p) for p in motive_problems(text, facts, list(scope.cards))]
    hits = writing_check.check(text, "letter", spelling=scope.values.get("spelling_code", ""),
                               allowed=scope.values.get("own_words") or ())
    return out + [Problem("", f"{h.message} ({h.quote})") for h in writing_check.high(hits)]


def _own_words(text: str, names: list[str]) -> list[str]:
    """The words of a text in order, names (`names`, longest first) and little words taken out."""
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", " ", text, flags=re.I)
    return [w for w in re.findall(r"[a-z0-9+#]+", text.lower()) if w not in drafts.LITTLE_WORDS]


def retold(sentence: str, facts: Facts) -> list[str]:
    """The names of the middle's paragraphs (`Facts.middle`) a frame sentence retells: it says nearly what one
    of the paragraph's sentences says (`repeats.same`), shares `RETOLD_RUN` of its own words in a row with one
    (names, the role and the employer aside), or gives a number the paragraph gives that who is writing and
    the modules do not."""
    names = [*(n for n, _ in facts.middle), *facts.told, facts.title, facts.employer]
    mine = _own_words(sentence, names)
    runs_of = {tuple(mine[i:i + RETOLD_RUN]) for i in range(len(mine) - RETOLD_RUN + 1)}
    own_numbers = numbers(sentence) - numbers("\n".join([facts.who, facts.title, facts.employer, *facts.modules]))
    out = []
    for work, text in facts.middle:
        theirs = _own_words(text, names)
        shared = any(tuple(theirs[i:i + RETOLD_RUN]) in runs_of for i in range(len(theirs) - RETOLD_RUN + 1))
        if shared or own_numbers & numbers(text) or any(repeats.same(sentence, b)
                                                         for b in writing_check.sentences(text)):
            out.append(work)
    return out


def _stems(text: str, leave: set[str] = frozenset()) -> set[str]:
    return {w[:STEM] for w in writing_check.words_of(text) if w not in drafts.LITTLE_WORDS and w not in leave}


def motive_problems(text: str, facts: Facts, cards: list[dict] = ()) -> list[str]:
    """What is wrong with the opening's reason for this employer: it names a project or job of the person's (a
    card of `cards`, or work the middle tells), or, with a pairing, it lacks one specific word of the employer's
    fact (`motive.specific`'s words)."""
    named = [c["name"] for c in drafts.named_cards(text, [c for c in cards if c.get("name", "").strip()])]
    named += [n for n in facts.told if n not in named and drafts.names_card(text, {"name": n, "aliases": []})]
    out = [NAMES_WORK.format(work=n) for n in named]
    if facts.pairing is None:
        return out
    names = {w for n in (facts.title, facts.employer) for w in writing_check.words_of(n)}
    own = _stems(facts.pairing.fact, names | motive.GENERIC)
    if own and not own & _stems(text):
        out.append(MOTIVE_EMPLOYER.format(fact=facts.pairing.fact))
    return out


def settle(facts: Facts, scope: Scope) -> Framed:
    """A letter's opening and closing (prompt `letter_frame`), each checked with no AI and the pair drafted once
    more when either fails; an opening still failing is the built sentence of `writing.opening`, a closing still
    failing is left out."""
    out = Framed()
    request = facts.request(scope)
    reply = ask(PROMPT, request, scope, key="")
    out.calls += 1
    first, last = clean(reply.get("opening"), scope), clean(reply.get("closing"), scope)
    found = problems(first, facts, scope, True) + problems(last, facts, scope, False)
    if found or not first:
        out.redrafted = True
        log.info("letter frame: %s", "; ".join(p.line() for p in found) or "no opening")
        note = (f"\n\nYour last opening:\n{first}\n\nYour last closing:\n{last}\n\nWhat is wrong:\n"
                + "\n".join(f"- {p.line()}" for p in found) + "\nWrite both again without these problems.")
        reply = ask(PROMPT, request + note, scope, key="")
        out.calls += 1
        first = clean(reply.get("opening"), scope) or first
        last = clean(reply.get("closing"), scope) or last
    if not first or problems(first, facts, scope, True):
        first = opening.Opening.of(facts.name, scope.now, facts.title).sentence if scope.now else ""
    out.opening = first
    out.closing = "" if problems(last, facts, scope, False) else last
    return out

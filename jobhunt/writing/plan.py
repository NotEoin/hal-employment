"""The evidence plan: the facts about the person that one letter, message or set of answers may rest on,
chosen before anything is written.

`sources(facts, kept, cards)` numbers what a draft may draw on (S1, S2, ...): each line of the CV, each line
of the Profile block and each passage of the person's own documents (`kept`, as `pack._kept` gathers them),
with the card each one is about. `make(requirements, questions, facts, kept, cards)` asks the AI once (prompt
`evidence_plan`) to pick up to `MAX_ITEMS` quotes from them, and `checked(reply, found)` keeps the picks that
hold, as `Item`s with ids E1, E2, ... `block(items)` is the plan as a writing request shows it.

Design decisions, each with its reason:

- The evidence is chosen before the draft, so each sentence can say what it rests on and be checked against
  that alone: a local model writing freely framed projects in the wrong place, wrote team work as solo and
  added details to fit the advert in every graded run (2026-11-07).
- `MAX_ITEMS` is 8: a letter's middle gives two or three pieces of evidence, each of which may need a second
  item for what came of it, its reason for the employer needs one more, and answers to two or three questions
  each need their own. More would be a list to copy out rather than a choice, and each item is read again by
  every sentence check that cites it.
- A pick is kept only when its source exists and its quote is in that source word for word, spaces and case
  aside; the text kept is the source's own, so a model's paraphrase never becomes evidence.
- The card, its context and the person's role come from Hal's reading of the card (`profile.contexts`, the
  card's Role field and team words), never from the reply: the context is what drafts got wrong.
- A list-shaped line (a CV's modules and marks, a skills list, a list of achievements) is one source per
  entry (`writing.atoms`), its heading kept on each, and an item quoted from one is the whole entry: a CV
  line of every module was one source, so "I achieved 86 in <module>, where I contributed to a team
  project" cited a quote that held another module's "Team Project" (2026-11-08). Whether an entry is a
  skills list is read from its whole line.
- Only a "Project" or "Job" line of the Profile block, a CV line or a passage of a card's file is tied to a
  card: a skill line quoting a project says nothing of where the project was done.
- A skills list (`skills_line`: a Profile "Skill:" line, or a CV line of a label and a list, "AI and ML:
  PyTorch, NumPy, pandas") is never tied to a card and is marked as one (`Item.skills`): it says what the
  person knows, never which project used it. A letter said "I used NumPy and pandas to build" a project whose
  card never names pandas, citing the CV's skills line (2026-11-07); `writing.grounded` holds a tool claimed
  for a project to that project's own evidence.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import atoms, drafts, stage

log = logging.getLogger(__name__)

PROMPT = "evidence_plan"
MAX_ITEMS = 8              # see the module notes
MIN_QUOTE_WORDS = 2        # a one-word quote ("Python") is a skill name, not evidence
LABEL = re.compile(r"^\[(.+?)\]$", re.M)                # a passage's "[file > heading]" line (`record.context`)
KIND_LINE = re.compile(r"^\(Kind: ([^)\n]*)\)\s*$")     # the "(Kind: personal project)" line under it
CARD_LINE = re.compile(r"^(Project|Job)\b")              # the Profile block lines that are a card
BY_WORDS = {words: context for context, words in stage.CONTEXT_WORDS.items()}
CV, PROFILE = "CV", "Profile"
# A skills list: "Languages: Python, SQL", or any short label and at least `LIST_ITEMS` comma-separated items.
SKILLS_LABEL = re.compile(r"^\s*(skills?|languages?|tools?|technologies|frameworks?|libraries|software)\b[^:]{0,30}:",
                          re.I)
LABELLED_LIST = re.compile(r"^\s*[A-Za-z][\w &/+#.-]{0,40}:\s*[^,.;]{1,40}(?:,[^,.;]{1,40}){2,}\.?\s*$")
PROFILE_SKILL = re.compile(r"^Skill\b")
SKILLS_WORDS = "a skills list: what the applicant knows, never which project used it"


@dataclass(frozen=True)
class Source:
    """One numbered fact a plan may quote: a CV line, a Profile line or a passage, with the card it is about."""

    id: str
    label: str                 # "CV", "Profile" or the passage's "file > heading"
    text: str
    card: dict | None = None
    context: str = ""          # the passage's own "(Kind: ...)" context when no card says one
    skills: bool = False       # an entry of a skills list (`skills_line`, read from its whole line)
    entry: bool = False        # one entry of a list-shaped line (`writing.atoms.split`), heading kept


@dataclass(frozen=True)
class Item:
    """One piece of evidence of a plan: a quote of one source, with its card's context and the person's role."""

    id: str                    # "E1", "E2", ...
    text: str                  # word for word from its source
    source: str                # the source's label
    card: str = ""             # the card's name, "" when the quote is about no one card
    context: str = ""          # `writing.stage.CONTEXTS`, or "" when unknown
    role: str = ""             # the person's part ("built the statistics features; team of 7"), "" when unsaid
    tools: tuple[str, ...] = ()
    skills: bool = False       # quoted from a skills list (`skills_line`), so tied to no project

    def words(self) -> str:
        """The item as a request lists it: its quote, then where it comes from, its context and role."""
        about = [f"from {'the ' + self.source if self.source in (CV, PROFILE) else self.source}"]
        if self.card:
            about.append(f"about {self.card}" + (f", {stage.CONTEXT_WORDS[self.context]}" if self.context else ""))
        elif self.context:
            about.append(stage.CONTEXT_WORDS[self.context])
        if self.role:
            about.append(f"the applicant's part: {self.role}")
        if self.tools:
            about.append(f"tools: {', '.join(self.tools)}")
        if self.skills:
            about.append(SKILLS_WORDS)
        return f"{self.id}: \"{self.text}\" ({'; '.join(about)})"


def skills_line(label: str, text: str) -> bool:
    """True when a source is a skills list: a Profile "Skill:" line, a CV line labelled "Skills:",
    "Languages:", "Tools:" and the like, or a short label followed by a list of at least three items."""
    if label == PROFILE:
        return bool(PROFILE_SKILL.match(text))
    return label == CV and bool(SKILLS_LABEL.match(text) or LABELLED_LIST.match(text))


def _card_of(text: str, cards: list[dict]) -> dict | None:
    """The one card a text names, or None when it names none or several."""
    named = [c for c in cards if c["name"].strip() and drafts.names_card(text, c, cards)]
    return named[0] if len(named) == 1 else None


def _line_sources(label: str, line: str, cards: list[dict], tied: bool = True) -> list[tuple]:
    """A CV or Profile line as sources: one per entry when it is list-shaped (`atoms.split`), each with the card
    it names when `tied`, and whether its whole line is a skills list."""
    entries = atoms.split(line)
    skills = skills_line(label, line)
    return [(label, e, _card_of(e, cards) if tied else None, "", skills, len(entries) > 1) for e in entries]


def sources(facts: str, kept: str, cards: list[dict]) -> list[Source]:
    """Every fact a plan may quote, numbered S1, S2, ...: the CV's lines, the Profile block's lines and the
    passages of the person's own documents in `kept`, a list-shaped line one source per entry."""
    found: list[tuple[str, str, dict | None, str, bool, bool]] = []
    for line in (facts or "").splitlines():
        if line.strip():
            found += _line_sources(CV, line.strip(), cards)
    parts = LABEL.split(kept or "")
    for line in parts[0].splitlines():
        said = line.strip()
        if said.startswith("- "):
            said = said[2:].strip()
            if CARD_LINE.match(said):
                found.append((PROFILE, said, _card_of(said, cards), "", False, False))
            else:
                found += _line_sources(PROFILE, said, cards, tied=False)
    for label, body in zip(parts[1::2], parts[2::2], strict=True):
        lines = body.strip().splitlines()
        kind = KIND_LINE.match(lines[0]) if lines else None
        text = "\n".join(lines[1:] if kind else lines).strip()
        file = label.split(" > ", 1)[0]
        card = next((c for c in cards if file in c.get("files", [])), None)
        if text:
            found.append((label, text, card, BY_WORDS.get(kind.group(1), "") if kind else "", False, False))
    return [Source(f"S{n}", *source) for n, source in enumerate(found, 1)]


def quoted(quote: str, source: str) -> str:
    """The words of `source` that `quote` copies, spaces and case aside; "" when it does not copy them."""
    words = re.findall(r"\S+", quote.strip().strip("\"'"))
    if words:
        words[-1] = words[-1].rstrip(".,;:")
    words = [w for w in words if w]
    if len(words) < MIN_QUOTE_WORDS:
        return ""
    found = re.search(r"\s+".join(map(re.escape, words)), source, re.I)
    return found.group(0) if found else ""


def _role(card: dict | None, text: str) -> str:
    """The person's part on the card, with the team words the quote's own source holds."""
    role = (card or {}).get("role", "")
    team = stage.team_words(text)
    if team and team.lower() not in role.lower():
        role = "; ".join(p for p in (role, team) if p)
    return role


def checked(reply: dict, found: list[Source]) -> list[Item]:
    """The picks of a plan reply that hold (see the module notes), as items E1, E2, ...; at most `MAX_ITEMS`."""
    picks = reply.get("evidence") if isinstance(reply, dict) else None
    by_id = {s.id: s for s in found}
    items: list[Item] = []
    seen: set[str] = set()
    for pick in picks if isinstance(picks, list) else []:
        source = by_id.get(str(pick.get("source") or "").strip().upper()) if isinstance(pick, dict) else None
        text = quoted(str(pick.get("quote") or ""), source.text) if source else ""
        text = source.text if text and source.entry else text     # an entry is one fact, its heading kept
        if not text or text.lower() in seen:
            log.info("evidence plan: dropped a pick that quotes no source word for word: %r", pick)
            continue
        seen.add(text.lower())
        skills = source.skills or skills_line(source.label, source.text)
        card = {} if skills else source.card or {}
        items.append(Item(f"E{len(items) + 1}", text, source.label, card.get("name", ""),
                          card.get("context", "") or source.context, _role(card or None, source.text),
                          tuple(card.get("tools", ())), skills))
        if len(items) == MAX_ITEMS:
            break
    return items


def request(requirements: list[str], questions: list[str], found: list[Source]) -> str:
    """The plan request: the job's requirements and questions, then every source."""
    listed = "\n".join(f"{s.id} ({s.label}): {s.text}" for s in found)
    asked = "".join(f"- {q}\n" for q in questions)
    return ("Requirements:\n" + "".join(f"- {r}\n" for r in requirements)
            + (f"\nQuestions:\n{asked}" if asked else "")
            + f"\nSources (the applicant's facts):\n{listed}\n\nPick at most {MAX_ITEMS}.")


def make(requirements: list[str], questions: list[str], facts: str, kept: str, cards: list[dict]) -> list[Item]:
    """The evidence plan for one job's drafts: one AI call, then the picks that hold; [] when nothing does."""
    found = sources(facts, kept, cards)
    if not found:
        return []
    reply = llm.chat_json(prompts.text(PROMPT), request(requirements, questions, found), role="main")
    return checked(reply, found)


HEADING = "Evidence plan (the only facts about the applicant a draft may state):"


def block(items: list[Item]) -> str:
    """The plan as a writing request shows it; "" for an empty plan."""
    return "\n".join([HEADING, *(i.words() for i in items)]) if items else ""

"""The evidence plan: the facts about the person that one letter, message or set of answers may rest on,
chosen before anything is written.

`sources(facts, kept, cards)` numbers what a draft may draw on (S1, S2, ...): each line of the CV, each line
of the Profile block and each passage of the person's own documents (`kept`, as `pack._kept` gathers them),
with the card each one is about. `make(requirements, questions, facts, kept, cards)` asks the AI once (prompt
`evidence_plan`) to pick up to `MAX_ITEMS` quotes from them, and `checked(reply, found)` keeps the picks that
hold, as `Item`s with ids E1, E2, ... (`Item.words` is one as the motivation pairing's request lists it).
`stories(items, cards)` groups the items into the pieces of work a draft tells, one paragraph each (`Story`),
and `choose(found, n, used)` picks which of them a letter, an answer or a message tells.

Design decisions, each with its reason:

- The evidence is chosen before the draft, so each sentence can say what it rests on and be checked against
  that alone: a local model writing freely framed projects in the wrong place, wrote team work as solo and
  added details to fit the advert in every graded run (2026-11-07).
- `MAX_ITEMS` is 8: a letter's middle gives two or three pieces of evidence, each of which may need a second
  item for what came of it, its reason for the employer needs one more, and answers to two or three questions
  each need their own. More would be a list to copy out rather than a choice.
- A pick is kept only when its source exists and its quote is in that source word for word, spaces and case
  aside; the text kept is the source's own, so a model's paraphrase never becomes evidence.
- The card, its context and the person's role come from Hal's reading of the card (`profile.contexts`, the
  card's Role field and team words), never from the reply: the context is what drafts got wrong.
- A list-shaped line (a CV's modules and marks, a skills list, a list of achievements) is one source per
  entry (`writing.atoms`), its heading kept on each, and an item quoted from one is the whole entry: a CV
  line of every module was one source, so "I achieved 86 in <module>, where I contributed to a team
  project" cited a quote that held another module's "Team Project" (2026-11-08). Whether an entry is a
  skills list is read from its whole line.
- An item about a card whose name is like another's says the card's tag ("about Quill (offline assistant over
  personal notes)"), and a text naming "Quill Employment" names that card, never "Quill" as well
  (`drafts.named_cards`).
- Only a "Project" or "Job" line of the Profile block, a CV line or a passage of a card's file is tied to a
  card: a skill line quoting a project says nothing of where the project was done. A file that lists projects
  (`profile.owners`) is no card's file: its passage is a source only when it names one card, and is that
  card's, as a dossier of every project put a network sniffer's passages in a navigation project's
  paragraph (2026-11-08).
- A pick whose quote is a plan (`writing.planned`: plan words, or a passage of a design document,
  specification or proposal saying nothing was done) is marked (`Item.planned`) and says so: a design
  document's planned tools became "I used Docker" in every pack (2026-11-08).
- A skills list (`skills_line`: a Profile "Skill:" line, or a CV line of a label and a list, "AI and ML:
  PyTorch, NumPy, pandas") is never tied to a card and is marked as one (`Item.skills`): it says what the
  person knows, never which project used it. A letter said "I used NumPy and pandas to build" a project whose
  card never names pandas, citing the CV's skills line (2026-11-07); a skills list is never a story, so a
  paragraph about a project holds a tool to that project's own facts (`writing.paragraphs`).
- One piece of work is one paragraph, told once (`Story`, 2026-11-08): a grader capped every pack at 2 of 5
  for readability, as drafts retold one project in several paragraphs (10 of 15 packs), gave one project's
  test suite or tools to another, and read as lists of facts. Each story holds one card's facts alone (its
  plan items first, then its own quotes, `STORY_FACTS` in all, its tools, part and where it was done), so a
  paragraph drafted from it has no other project's facts to borrow. An item tied to no card is a story of
  its own (a passage's items one story per file), and a skills list is never one, as it tells no work.
- A story's tools are the card's tools its facts, name or summary name (`own_tools`), or all of them when they
  name none, as a card's tool list may be the only place its language is given: a LIDAR project was drafted
  "using Lua and Python" when no fact of it named Python, which came from the card's tool list, such as a
  repository's count of file types that includes one helper script (2026-11-08).
- The plan says what each piece of evidence can help with in this job (`Item.use`), in the model's own
  words, so a paragraph's one link to the job names a task or a system rather than the advert's
  requirement: graders marked down "satisfies the requirement for" and the like in every pack (2026-11-08).
- How many stories each draft tells (`LETTER_STORIES`, `ANSWER_STORIES`, `MESSAGE_STORIES`) and which:
  `choose` prefers stories no earlier draft of the pack told, the plan's best first, so a letter's
  paragraphs and the answers each tell a different piece of work while the record has enough.
- Projects first (`Story.kind`, `letter_stories`, 2026-11-08): a software letter told two university modules
  ("I scored 86 in ...") instead of strong projects and came to 118 words against a 250-word setting, and
  another spent a paragraph restating the degree the opening gives. A letter's stories are project and job
  cards, the plan's first, then other cards the job's words fit (`relevance`, at least `RELEVANT_MIN` words)
  until it has `LETTER_STORIES`; a module (`module_of`) or a line tied to no card is a paragraph only when
  fewer than `MIN_PROJECTS` project or job stories exist. The degree itself (`DEGREE`, a line or a card named
  like "First-Class Honours") is never a story, as the opening says it. The answers may tell those other
  cards too (`fitting`), so they prefer work the letter did not tell: one project was told in a letter's
  opening, a letter paragraph and an answer (2026-11-08). An AI ranking (`writing.rank`) now chooses the work
  first; this word count chooses when the ranking gives nothing usable, with the person's core work
  (`profile.tiers.CORE`) first at an equal count.
- Every story names its work (`Story.name`, 2026-11-08): passages tied to no card were drafted as "I designed a
  pure simulation engine ..." and "I implemented config-hashed result caching ..." with nothing saying which
  project. A passage of a project's own file (one with a "(Kind: ...)" line) tied to no card is named after
  its file (`profile.owners.file_name`: the repository's or folder's name, or the note's title, which for a
  dissertation is its title), and a module story after its module; `writing.paragraphs` checks every named
  story names its work.
- What the person is looking for (`plain.GOAL`: "seeking a role in ...") is never a source: a CV's summary
  gave a letter "I am seeking a role in backend, systems or applied AI" (2026-11-08). The sentence is taken
  out of its line, and a line with nothing else left is no source.
- Stories come from the sectioned record (sections plan, 2026-11-09): starred projects of any source kind,
  starred coursework (a showcase piece is a starred project card of the degree) and jobs, a starred job
  first (`starred_first`). A project card only a CV names (`cv_only`) is told only when no card is starred,
  and its story says so; a field only a CV fills for a project with its own sources is marked in the story
  (`Story.cv_filled`); a dissertation's story says it is the dissertation (`Story.dissertation`), under its
  own title.
- A job only older CVs give (`Story.older_cv`, `profile.writing.older_cv_job`) is told after every project and
  job with current evidence (`older_last`), so only when the draft has room after them, and from its plain
  facts alone (title, employer, dates, duties), its story saying to add nothing: such a job was told as a full
  story beside the person's current work (2026-11-09). A starred one leads with the other starred work, as
  the person showcased it on purpose, and is still told from its plain facts alone (2026-11-09).
- Every starred card is a story (`with_showcase`), built from the card's own facts when the plan picked
  nothing of it: a pack whose plan picked nothing was drafted whole and told none of the person's starred
  projects, coursework or jobs (2026-11-09).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.profile import owners, tiers
from jobhunt.writing import atoms, drafts, plain, planned, stage
from jobhunt.writing import check as writing_check

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
PLANNED_WORDS = "a plan, not something done: it never says the applicant used it"


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
    tag: str = ""              # the card's tag when its name is like another card's (`profile.writing.tag`)
    planned: bool = False      # it says what was planned, not done (`writing.planned`)
    use: str = ""              # what in this job it can help with, in the model's words (see the module notes)

    def words(self) -> str:
        """The item as a request lists it: its quote, then where it comes from, its card (with its tag), context
        and role."""
        about = [f"from {'the ' + self.source if self.source in (CV, PROFILE) else self.source}"]
        if self.card:
            about.append(f"about {self.card}" + (f" ({self.tag})" if self.tag else "")
                         + (f", {stage.CONTEXT_WORDS[self.context]}" if self.context else ""))
        elif self.context:
            about.append(stage.CONTEXT_WORDS[self.context])
        if self.role:
            about.append(f"the applicant's part: {self.role}")
        if self.tools:
            about.append(f"tools: {', '.join(self.tools)}")
        if self.skills:
            about.append(SKILLS_WORDS)
        if self.planned:
            about.append(PLANNED_WORDS)
        return f"{self.id}: \"{self.text}\" ({'; '.join(about)})"


def skills_line(label: str, text: str) -> bool:
    """True when a source is a skills list: a Profile "Skill:" line, a CV line labelled "Skills:",
    "Languages:", "Tools:" and the like, or a short label followed by a list of at least three items."""
    if label == PROFILE:
        return bool(PROFILE_SKILL.match(text))
    return label == CV and bool(SKILLS_LABEL.match(text) or LABELLED_LIST.match(text))


def _card_of(text: str, cards: list[dict]) -> dict | None:
    """The one card a text names, or None when it names none or several."""
    named = drafts.named_cards(text, cards)
    return named[0] if len(named) == 1 else None


def without_goal(text: str) -> str:
    """A line without its sentences saying what the person is looking for (`plain.GOAL`); "" when nothing else
    is left."""
    kept = [s for s in writing_check.sentences(text) if not plain.GOAL.search(s)] if plain.GOAL.search(text) else [text]
    return " ".join(kept).strip()


def _line_sources(label: str, line: str, cards: list[dict], tied: bool = True) -> list[tuple]:
    """A CV or Profile line as sources: one per entry when it is list-shaped (`atoms.split`), each with the card
    it names when `tied`, and whether its whole line is a skills list; [] for a line that only says what the
    person is looking for (`without_goal`)."""
    line = without_goal(line)
    if not line:
        return []
    entries = atoms.split(line)
    skills = skills_line(label, line)
    return [(label, e, _card_of(e, cards) if tied else None, "", skills, len(entries) > 1) for e in entries]


def sources(facts: str, kept: str, cards: list[dict], listings: frozenset[str] = frozenset()) -> list[Source]:
    """Every fact a plan may quote, numbered S1, S2, ...: the CV's lines, the Profile block's lines and the
    passages of the person's own documents in `kept`, a list-shaped line one source per entry. A passage of a
    file that lists projects (`listings`) is a source only when it names one card, and is about that card."""
    found: list[tuple[str, str, dict | None, str, bool, bool]] = []
    for line in (facts or "").splitlines():
        if line.strip():
            found += _line_sources(CV, line.strip(), cards)
    parts = LABEL.split(kept or "")
    for line in parts[0].splitlines():
        said = line.strip()
        if said.startswith("- "):
            said = said[2:].strip()
            if CARD_LINE.match(said) and without_goal(said):
                found.append((PROFILE, without_goal(said), _card_of(said, cards), "", False, False))
            else:
                found += _line_sources(PROFILE, said, cards, tied=False)
    for label, body in zip(parts[1::2], parts[2::2], strict=True):
        lines = body.strip().splitlines()
        kind = KIND_LINE.match(lines[0]) if lines else None
        text = "\n".join(lines[1:] if kind else lines).strip()
        file = label.split(" > ", 1)[0]
        if file in listings:
            card = _card_of(text, cards)
            if card is None:
                log.info("evidence plan: left out a passage of %s, which lists projects and names no one card", file)
                continue
        else:
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
                          tuple(card.get("tools", ())), skills, card.get("tag", ""),
                          planned.planned(text, _file_of(source.label)),
                          " ".join(str(pick.get("use") or "").split())))
        if len(items) == MAX_ITEMS:
            break
    return items


def _file_of(label: str) -> str:
    """The file a source comes from: a passage's "file > heading" label names it; a CV or Profile line none."""
    return "" if label in (CV, PROFILE) else label.split(" > ", 1)[0]


def request(requirements: list[str], questions: list[str], found: list[Source]) -> str:
    """The plan request: the job's requirements and questions, then every source."""
    listed = "\n".join(f"{s.id} ({s.label}): {s.text}" for s in found)
    asked = "".join(f"- {q}\n" for q in questions)
    return ("Requirements:\n" + "".join(f"- {r}\n" for r in requirements)
            + (f"\nQuestions:\n{asked}" if asked else "")
            + f"\nSources (the applicant's facts):\n{listed}\n\nPick at most {MAX_ITEMS}.")


def make(requirements: list[str], questions: list[str], facts: str, kept: str, cards: list[dict],
         listings: frozenset[str] = frozenset()) -> list[Item]:
    """The evidence plan for one job's drafts: one AI call, then the picks that hold; [] when nothing does.
    `listings` are the files that list projects (`profile.owners.Files.listings`)."""
    found = sources(facts, kept, cards, listings)
    if not found:
        return []
    reply = llm.chat_json(prompts.text(PROMPT), request(requirements, questions, found), role="main")
    return checked(reply, found)



STORY_FACTS = 12           # facts a paragraph is given: the prototype's (2026-11-08); more would be a list to copy
LETTER_STORIES = 3         # a letter's middle tells two or three pieces of work, one paragraph each
MIN_PROJECTS = 2           # project or job stories below which a module or another line may be a letter's paragraph
RELEVANT_MIN = 2           # words of the job's requirements a card's words must share to be told unpicked
PROJECT, JOB, MODULE, OTHER = "project", "job", "module", "other"     # `Story.kind`
# The degree itself: a qualification, a class or the course as a whole, which the opening already gives.
DEGREE = re.compile(r"^(?:Education|Degree|Qualifications?)\b|\b(?:BSc|BA|MSc|MA|MEng|BEng|MPhil|PhD|LLB|BEd|HND|"
                    r"A-levels?|degree|graduat\w*|honours|hons|first[- ]class|2:[12]|GPA|final[- ]year)\b", re.I)
# A module line: the Profile's "Module (BA History): Oral History (68)" or a CV's "Year 2 modules: Ferry Studies 86".
MODULE_LINE = re.compile(r"^(?P<head>[^:]{0,60}\bmodules?\b[^:]{0,60}):\s*(?P<name>.+)$", re.I)
ANSWER_STORIES = 1         # an answer tells one piece of work ...
ANSWER_STORIES_MANY = 2    # ... or two, when its question asks for more than one example
MESSAGE_STORIES = 1        # a message to the person hiring has room for one
# A question asking for more than one example: "Give two examples", "times when you ...".
MANY = re.compile(r"\b(?:examples|instances|occasions|situations|times when|(?:two|three|several) "
                  r"(?:times|ways|projects|pieces))\b", re.I)
WHERE_JOB = "a job"        # where a job card's work was done, as a story says it
CV_ONLY_WORDS = "your CV only; no project files back it, so keep to what the CV says"
CV_FILLED_WORDS = "From your CV, not the project's own sources (say no more than this)"
DISSERTATION_WORDS = "your dissertation for your degree; call it your dissertation"
OLDER_CV_JOB_WORDS = ("an older CV only; say only its title, employer, dates and duties as given, and add nothing "
                      "to them")


@dataclass(frozen=True)
class Story:
    """One piece of work a draft tells in one paragraph: a card's facts, or items tied to no card."""

    key: str                   # what tells it from another: its card's name, else its items' source
    name: str                  # the card's name; "" for items tied to no card
    where: str                 # where it was done, in a prompt's words ("personal project", "a job"); "" unknown
    context: str = ""          # `writing.stage.CONTEXTS`, or "" when unknown
    role: str = ""             # the person's part
    summary: str = ""
    tools: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()           # its plan items' quotes, then its card's own quotes
    planned: tuple[str, ...] = ()         # quotes that are plans, never done (`Item.planned`)
    planned_tools: tuple[str, ...] = ()   # tools only a plan of its card gives (`profile.writing.planned_only`)
    use: str = ""              # what in this job it can help with (`Item.use`)
    tag: str = ""
    aliases: tuple[str, ...] = ()
    card: dict | None = field(default=None, compare=False, hash=False)
    kind: str = OTHER          # `PROJECT`, `JOB`, `MODULE` or `OTHER`
    cv_filled: tuple[str, ...] = ()       # "field: value" lines only a CV gives a project with its own sources
    cv_only: bool = False      # a project card only a CV names (`profile.cvs.cv_only`)
    dissertation: bool = False
    older_cv: bool = False     # a job only older CVs give (`profile.writing.older_cv_job`): plain facts, told last

    def words(self) -> str:
        """Every word the story's facts hold, plans aside: what a paragraph about it may say."""
        return "\n".join([self.name, *self.aliases, self.where, self.role, self.summary, *self.tools, *self.facts,
                          *self.cv_filled])

    def block(self) -> str:
        """The story as a request gives it: plain sentences under its name, never an evidence id."""
        kind = "Job" if self.where == WHERE_JOB else "Module" if self.kind == MODULE else "Project"
        lines = [f"{kind}: {self.name}" + (f" ({self.tag})" if self.tag else "") if self.name
                 else "Evidence (no name of its own)"]
        lines += [f"Where it was done: {self.where}"] if self.where else []
        lines += [f"What kind of work: {DISSERTATION_WORDS}"] if self.dissertation else []
        lines += [f"Where its facts come from: {CV_ONLY_WORDS}"] if self.cv_only else []
        lines += [f"Where its facts come from: {OLDER_CV_JOB_WORDS}"] if self.older_cv else []
        lines += [f"Your part: {self.role}"] if self.role else []
        lines += [f"What it is: {self.summary}"] if self.summary else []
        lines += [f"Tools: {', '.join(self.tools)}"] if self.tools else []
        lines += ["Facts:", *(f"- {f}" for f in self.facts)] if self.facts else []
        lines += [f"{CV_FILLED_WORDS}:", *(f"- {f}" for f in self.cv_filled)] if self.cv_filled else []
        if self.planned:
            lines += ["Planned, never done (leave out, or say it was planned):", *(f"- {f}" for f in self.planned)]
        lines += [f"What it can help with in this job: {self.use}"] if self.use else []
        return "\n".join(lines)


def _where(card: dict | None, context: str) -> str:
    if card is not None and card.get("kind") == "job":
        return WHERE_JOB
    return stage.CONTEXT_WORDS.get(context, "")


def _story(key: str, items: list[Item], card: dict | None, module: str = "") -> Story:
    """The story of one card (with no items when the plan picked none of it), of a module, of a project's own
    file, or of items tied to none: its items' quotes first, its card's own after. A job only older CVs give
    (`older_cv_job`) is told from its card's plain facts alone, never its items' quotes."""
    plain = bool(card is not None and card.get("older_cv_job"))
    quotes = [*(i.text for i in items if not i.planned and not plain), *((card or {}).get("quotes") or [])]
    facts = tuple(list(dict.fromkeys(q for q in quotes if q.strip()))[:STORY_FACTS])
    plans = tuple(i.text for i in items if i.planned)
    use = next((i.use for i in items if i.use), "")
    first = items[0] if items else Item("", "", "")
    if card is None:
        project = _file_project(first)
        return Story(key, module or project, _where(None, first.context), first.context, first.role, facts=facts,
                     planned=plans, use=use, kind=MODULE if module else PROJECT if project else OTHER)
    context = card.get("context", "") or first.context
    filled = card.get("cv_filled") or {}
    cv_tools = set(filled.get("tools") or ())
    return Story(key, card["name"], _where(card, context), context, card.get("role", "") or first.role,
                 card.get("summary", ""), tuple(t for t in own_tools(card, facts) if t not in cv_tools), facts, plans,
                 tuple(card.get("planned") or ()), use, card.get("tag", ""), tuple(card.get("aliases") or ()), card,
                 JOB if card.get("kind") == JOB else PROJECT,
                 tuple(f"{name}: {', '.join(values)}" for name, values in filled.items()),
                 bool(card.get("cv_only")), bool(card.get("dissertation")), plain)


def own_tools(card: dict, facts: tuple[str, ...] = ()) -> tuple[str, ...]:
    """A card's tools a story about it may say: those its facts, name or summary name, or every one of them when
    they name none (see the module notes)."""
    tools = tuple(str(t) for t in card.get("tools") or () if str(t).strip())
    said = "\n".join([card.get("name", ""), card.get("summary", ""), *facts])
    named = tuple(t for t in tools if drafts.names_tool(said, t))
    return named or tools


def _file_project(item: Item) -> str:
    """The project a passage's file is named after, when it is a project's own file (its passage says where the
    work was done); "" for a CV or Profile line or any other note."""
    if item.source in (CV, PROFILE, "") or not item.context:
        return ""
    return owners.file_name(_file_of(item.source))


def module_of(text: str) -> str:
    """The module a line is about ("Oral History" of "Module (BA History): Oral History (68)"), "" for none."""
    found = MODULE_LINE.match((text or "").strip())
    return atoms.MARK.sub("", found.group("name")).strip() if found else ""


def stories(items: list[Item], cards: list[dict]) -> list[Story]:
    """The pieces of work the plan's items tell, best first (see the module notes): one per card, one per
    module, one per passage's file, one per other item; a skills list and the degree itself tell none."""
    by_name = {c["name"]: c for c in cards if c.get("name")}
    groups: dict[str, tuple[str, list[Item]]] = {}
    for i in items:
        module = "" if i.card else module_of(i.text)
        if i.skills or (not module and DEGREE.search(i.card or i.text)):
            continue                       # what the person knows, or the degree the opening says
        key = i.card or (f"Module {module}" if module else
                         i.source if i.source not in (CV, PROFILE) else f"{i.source} {i.id}")
        groups.setdefault(key, (module, []))[1].append(i)
    return [_story(key, its, by_name.get(its[0].card), module) for key, (module, its) in groups.items()]


def relevance(card: dict, wanted: str) -> int:
    """How many words of the job's requirements (`wanted`) a card's name, summary, tools and quotes share."""
    def words(text: str) -> set[str]:
        return {w for w in writing_check.words_of(text) if w not in drafts.LITTLE_WORDS}

    own = " ".join([card.get("name", ""), card.get("summary", ""), *card.get("tools", ()), *card.get("quotes", ())])
    return len(words(own) & words(wanted))


MODULE_CLAUSE = 2          # modules one clause of the opening may name: more is a transcript, not a reason


def relevant_modules(modules: list[tuple[str, str]], wanted: str, told: str, n: int = MODULE_CLAUSE) -> tuple[str, ...]:
    """The modules (name, mark) whose names share a word with the job's requirements (`wanted`) and none with
    what the letter's paragraphs tell (`told`), best mark first, at most `n`, each "Name (mark)". A security
    advert's letters missed the person's high marks in security modules that no project showed (2026-11-08)."""
    def words(text: str) -> set[str]:
        return {w for w in writing_check.words_of(text) if w not in drafts.LITTLE_WORDS and len(w) > 2}

    asked, shown = words(wanted), words(told)
    fits = [(name, mark) for name, mark in modules if words(name) & asked and not words(name) & shown]
    marks = [float(m) if re.fullmatch(r"\d+(?:\.\d+)?", str(m).strip()) else -1.0 for _, m in fits]
    best = sorted(range(len(fits)), key=lambda k: -marks[k])[:n]
    return tuple(f"{fits[k][0]} ({fits[k][1]})" if str(fits[k][1]).strip() else fits[k][0] for k in best)


def _is_project(card: dict | None) -> bool:
    return card is not None and str(card.get("kind", "")).startswith("project")


def showcase_on(cards: list[dict]) -> bool:
    """True when the person starred any project card: then only starred projects are told as stories."""
    return any(_is_project(c) and c.get("star") for c in cards)


def _kept(card: dict | None, cards: list[dict]) -> bool:
    """True when a card may be told (`story_cards`); a story with no card always may."""
    if card is None:
        return True
    if _is_project(card) and showcase_on(cards) and not card.get("star"):
        return False
    return not (card.get("cv_only") and not card.get("star") and any(c.get("star") for c in cards))


def story_cards(cards: list[dict]) -> list[dict]:
    """The cards a draft may tell as stories: every job, and the starred projects once any project is starred
    (all projects while none is, so a record from before stars is not left with nothing to tell); a project only
    a CV names is left out while any card is starred. The cards left out still give the fit map and skills their
    evidence."""
    return [c for c in cards if _kept(c, cards)]


def offered(found: list[Story], cards: list[dict]) -> list[Story]:
    """`found` without the stories of the cards `story_cards` leaves out."""
    return [s for s in found if _kept(s.card, cards)]


def starred_first(found: list[Story]) -> list[Story]:
    """`found` with the stories of starred jobs first, the rest in their order: a starred job leads."""
    def lead(s: Story) -> bool:
        return s.card is not None and s.card.get("kind") == JOB and bool(s.card.get("star"))

    return [s for s in found if lead(s)] + [s for s in found if not lead(s)]


def _older_unstarred(s: Story) -> bool:
    return s.older_cv and not (s.card is not None and s.card.get("star"))


def older_last(found: list[Story]) -> list[Story]:
    """`found` with the stories of unstarred jobs only older CVs give (`Story.older_cv`) after every other, each
    part in its order: work with current evidence is told first, and such a job only when there is room after it.
    A starred one keeps its place with the other starred work, as the person chose it."""
    return [s for s in found if not _older_unstarred(s)] + [s for s in found if _older_unstarred(s)]


def card_story(card: dict) -> Story:
    """The story of a card the plan picked nothing of: its own quotes, tools, part and where it was done."""
    return _story(card["name"], [], card)


def with_showcase(found: list[Story], cards: list[dict]) -> list[Story]:
    """`found` and, after it, the story of every starred card it does not tell, built from the card itself
    (`card_story`), so what the person showcased is told even when the evidence plan picked nothing of it. A
    starred card with nothing of its own to tell (no quote, no summary) gives no story."""
    named = {s.name for s in found}
    return [*found, *(card_story(c) for c in cards if c.get("star") and c.get("name") and c["name"] not in named
                      and (c.get("quotes") or c.get("summary")))]


def fitting(found: list[Story], cards: list[dict], wanted: str) -> list[Story]:
    """The stories of the cards no story of `found` tells whose words share at least `RELEVANT_MIN` words with
    the job's requirements (`wanted`), the best fit first and, at an equal fit, the person's core work first.
    Only the cards a draft may tell (`story_cards`) are offered."""
    names = {s.name for s in found}
    cards = story_cards(cards)
    fits = sorted(((relevance(c, wanted), k) for k, c in enumerate(cards)
                   if c.get("name") and c["name"] not in names and (c.get("quotes") or c.get("summary"))),
                  key=lambda r: (bool(cards[r[1]].get("cv_only") or (cards[r[1]].get("older_cv_job")
                                                                     and not cards[r[1]].get("star"))), -r[0],
                                 cards[r[1]].get("tier") != tiers.CORE, r[1]))
    return [card_story(cards[k]) for r, k in fits if r >= RELEVANT_MIN]


def letter_stories(found: list[Story], cards: list[dict], wanted: str, n: int = LETTER_STORIES) -> list[Story]:
    """The `n` stories a letter tells (see the module notes): the plan's project and job stories, then the cards
    the job's requirements (`wanted`) fit best that no story tells, then, only below `MIN_PROJECTS` of those,
    modules and other lines in the plan's order; an unstarred job only older CVs give comes after every other
    project and job (`older_last`)."""
    found = offered(found, cards)
    named = {s.name for s in found}
    found = starred_first([*found, *(card_story(c) for c in cards
                                     if c.get("kind") == JOB and c.get("star") and c.get("name") not in named)])
    told = older_last([*(s for s in found if s.kind in (PROJECT, JOB)), *fitting(found, cards, wanted)])[:n]
    if len(told) < MIN_PROJECTS:
        told += [s for s in found if s.kind not in (PROJECT, JOB)]
    return told[:n]


def choose(found: list[Story], n: int, used: dict[str, int]) -> list[Story]:
    """`n` of the stories (`found`, best first), those the pack has told least first (`used`, by key, which is
    counted up for each one chosen), in the plan's order."""
    picked = sorted(sorted(range(len(found)), key=lambda k: (used.get(found[k].key, 0), k))[:max(n, 0)])
    out = [found[k] for k in picked]
    for s in out:
        used[s.key] = used.get(s.key, 0) + 1
    return out


def examples_wanted(question: str) -> int:
    """How many pieces of work an answer to `question` tells: `ANSWER_STORIES_MANY` when it asks for more than
    one example, else `ANSWER_STORIES`."""
    return ANSWER_STORIES_MANY if MANY.search(question or "") else ANSWER_STORIES

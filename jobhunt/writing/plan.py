"""The person's work a job's drafts are written from, chosen by code: which cards, and what each one says.

`chosen(cards, wanted, fit_names)` picks up to `WORK_GIVEN` of the checked project and job cards for one job,
with no AI call: the work the fit map cites and whose words the job's requirements share, best first.
`card_story(card)` is one card as a request gives it (`Story.block`): its name, where it was done, the
person's part, what it is, its own tools and its own quotes. `relevant_modules` names the modules the job's
requirements name, and `without_listings` takes a record passage out of the request when it comes from a file
that lists several projects and names no one card.

Design decisions, each with its reason:

- Code chooses the facts, and one writing call writes each document from them (owner, 2026-11-19: letters
  "written one shot by the ai", about 5 minutes per application at most). An AI evidence plan, an AI ranking
  of the work and a call per paragraph took 24 and 27 calls and about 5 minutes for two real adverts, and
  their letters all read alike (2026-11-19). The fit map is already the AI's reading of the job against the
  record, so a card it cites counts for more than shared words alone (`FIT_WEIGHT`).
- `WORK_GIVEN` is 4: a letter weaves two or three pieces of work into its argument, and the message and the
  answers may reach for one more without being given a list to copy out.
- A card is told from its own facts alone, under its own name, with where it was done and the person's part
  (`Story.block`), as a local model framed projects in the wrong place, wrote team work as solo and gave one
  project's tools to another when it wrote from loose lines (2026-11-07, 2026-11-08). Its quotes are
  `profile.writing.quotes`, at most `STORY_FACTS`.
- A card's tools are those its facts, name or summary name, or all of them when they name none
  (`own_tools`): a LIDAR project was drafted "using Lua and Python" when only the card's tool list gave Python
  (2026-11-08). A tool only a plan of the card gives is listed as planned, never done (`Story.planned_tools`).
- Which cards may be told (`story_cards`, sections plan 2026-11-09): every job, and the starred projects once
  any project is starred (all projects while none is); a project only a CV names is left out while any card is
  starred, and a project whose summary says it was done as a dissertation is left out when the record has the
  dissertation's own card, as letters told that work twice (2026-11-12). A starred job leads; a job only older
  CVs give (`older_cv_job`) and a CV-only project come after every other, told from their plain facts.
- A file that lists several projects (`profile.owners.Files.listings`) is no card's file: its passage is
  given only when it names one card, as a dossier of every project put a network sniffer's facts beside a
  navigation project (2026-11-08).
- The modules the job's requirements name, best mark first, at most `MODULE_CLAUSE`, are given apart
  (`relevant_modules`): a security advert's letters missed high marks in security modules (2026-11-08).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobhunt.profile import tiers
from jobhunt.writing import check as writing_check
from jobhunt.writing import drafts, stage

STORY_FACTS = 12           # a card's own quotes a request gives: more would be a list to copy out
WORK_GIVEN = 4             # see the module notes
FIT_WEIGHT = 3             # words of shared requirement one fit row citing the card counts as
PROJECT, JOB = "project", "job"     # `Story.kind`
LABEL = re.compile(r"^\[(.+?)\]$", re.M)                # a passage's "[file > heading]" line (`record.context`)
# A project whose summary says it is the dissertation ("developed as an MSc dissertation"), not work done beside one
# ("a side-project during a dissertation"): the record's dissertation card tells that work.
AS_DISSERTATION = re.compile(r"\b(?:as|for) (?:an? |my |the |their )?(?:[\w-]+ )?dissertation\b", re.I)
WHERE_JOB = "a job"        # where a job card's work was done, as a story says it
CV_ONLY_WORDS = "your CV only; no project files back it, so keep to what the CV says"
CV_FILLED_WORDS = "From your CV, not the project's own sources (say no more than this)"
DISSERTATION_WORDS = "your dissertation for your degree; call it your dissertation"
OLDER_CV_JOB_WORDS = ("another of your CVs only, not the CV your record follows; say only its title, employer, "
                      "dates and duties as given, and add nothing to them")
PLANNED_WORDS = "Planned only, never used (leave out, or say it was planned)"


@dataclass(frozen=True)
class Story:
    """One piece of the person's work as a request gives it: one card's own facts."""

    key: str                   # what tells it from another: its card's name
    name: str
    where: str                 # where it was done, in a prompt's words ("personal project", "a job"); "" unknown
    context: str = ""          # `writing.stage.CONTEXTS`, or "" when unknown
    role: str = ""             # the person's part
    summary: str = ""
    tools: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()           # its card's own quotes
    planned_tools: tuple[str, ...] = ()   # tools only a plan of its card gives (`profile.writing.planned_only`)
    tag: str = ""
    aliases: tuple[str, ...] = ()
    card: dict | None = field(default=None, compare=False, hash=False)
    kind: str = PROJECT        # `PROJECT` or `JOB`
    cv_filled: tuple[str, ...] = ()       # "field: value" lines only a CV gives a project with its own sources
    cv_only: bool = False      # a project card only a CV names (`profile.cvs.cv_only`)
    dissertation: bool = False
    older_cv: bool = False     # a job only older CVs give (`profile.writing.older_cv_job`): plain facts, told last

    def words(self) -> str:
        """Every word the story's facts hold, plans aside: what a draft about it may say."""
        return "\n".join([self.name, *self.aliases, self.where, self.role, self.summary, *self.tools, *self.facts,
                          *self.cv_filled])

    def block(self) -> str:
        """The story as a request gives it: plain lines under its name."""
        kind = "Job" if self.where == WHERE_JOB else "Project"
        lines = [f"{kind}: {self.name}" + (f" ({self.tag})" if self.tag else "")]
        lines += [f"Where it was done: {self.where}"] if self.where else []
        lines += [f"What kind of work: {DISSERTATION_WORDS}"] if self.dissertation else []
        lines += [f"Where its facts come from: {CV_ONLY_WORDS}"] if self.cv_only else []
        lines += [f"Where its facts come from: {OLDER_CV_JOB_WORDS}"] if self.older_cv else []
        lines += [f"Your part: {self.role}"] if self.role else []
        lines += [f"What it is: {self.summary}"] if self.summary else []
        lines += [f"Tools: {', '.join(self.tools)}"] if self.tools else []
        lines += [f"{PLANNED_WORDS}: {', '.join(self.planned_tools)}"] if self.planned_tools else []
        lines += ["Facts:", *(f"- {f}" for f in self.facts)] if self.facts else []
        lines += [f"{CV_FILLED_WORDS}:", *(f"- {f}" for f in self.cv_filled)] if self.cv_filled else []
        return "\n".join(lines)


def _where(card: dict, context: str) -> str:
    if card.get("kind") == JOB:
        return WHERE_JOB
    return stage.CONTEXT_WORDS.get(context, "")


def own_tools(card: dict, facts: tuple[str, ...] = ()) -> tuple[str, ...]:
    """A card's tools a story about it may say: those its facts, name or summary name, or every one of them when
    they name none (see the module notes)."""
    tools = tuple(str(t) for t in card.get("tools") or () if str(t).strip())
    said = "\n".join([card.get("name", ""), card.get("summary", ""), *facts])
    named = tuple(t for t in tools if drafts.names_tool(said, t))
    return named or tools


def card_story(card: dict) -> Story:
    """The story of one card: its own quotes, tools, part and where it was done."""
    facts = tuple(list(dict.fromkeys(q for q in card.get("quotes") or [] if q.strip()))[:STORY_FACTS])
    context = card.get("context", "")
    filled = card.get("cv_filled") or {}
    cv_tools = set(filled.get("tools") or ())
    return Story(card["name"], card["name"], _where(card, context), context, card.get("role", ""),
                 card.get("summary", ""), tuple(t for t in own_tools(card, facts) if t not in cv_tools), facts,
                 tuple(card.get("planned") or ()), card.get("tag", ""), tuple(card.get("aliases") or ()), card,
                 JOB if card.get("kind") == JOB else PROJECT,
                 tuple(f"{name}: {', '.join(values)}" for name, values in filled.items()),
                 bool(card.get("cv_only")), bool(card.get("dissertation")), bool(card.get("older_cv_job")))


def relevance(card: dict, wanted: str) -> int:
    """How many words of the job's requirements (`wanted`) a card's name, summary, tools and quotes share."""
    def words(text: str) -> set[str]:
        return {w for w in writing_check.words_of(text) if w not in drafts.LITTLE_WORDS}

    own = " ".join([card.get("name", ""), card.get("summary", ""), *card.get("tools", ()), *card.get("quotes", ())])
    return len(words(own) & words(wanted))


MODULE_CLAUSE = 2          # modules one clause may name: more is a transcript, not a reason


def relevant_modules(modules: list[tuple[str, str]], wanted: str, told: str, n: int = MODULE_CLAUSE) -> tuple[str, ...]:
    """The modules (name, mark) whose names share a word with the job's requirements (`wanted`) and none with
    the work already given (`told`), best mark first, at most `n`, each "Name (mark)"."""
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
    """True when the person starred any project card: then only starred projects are told."""
    return any(_is_project(c) and c.get("star") for c in cards)


def _kept(card: dict, cards: list[dict]) -> bool:
    """True when a card may be told (`story_cards`)."""
    if _is_project(card) and showcase_on(cards) and not card.get("star"):
        return False
    if _is_project(card) and _is_dissertation_work(card, cards):
        return False
    return not (card.get("cv_only") and not card.get("star") and any(c.get("star") for c in cards))


def _is_dissertation_work(card: dict, cards: list[dict]) -> bool:
    """True when `card` says it was done as a dissertation and another card is the dissertation itself, so a letter
    tells that work once, from the dissertation's own sources; letters told both as two pieces of work and copied
    the repo's wrong degree (eval, 2026-11-12)."""
    return (not card.get("dissertation") and bool(AS_DISSERTATION.search(card.get("summary") or ""))
            and any(c.get("dissertation") and c is not card for c in cards))


def story_cards(cards: list[dict]) -> list[dict]:
    """The cards a draft may tell (see the module notes). The cards left out still give the fit map and skills
    their evidence."""
    return [c for c in cards if _kept(c, cards)]


def fit_names(fit: dict, cards: list[dict]) -> dict[str, int]:
    """How often the fit map's rows cite each card by name (`drafts.named_cards`), a strong row twice."""
    out: dict[str, int] = {}
    for m in fit.get("matches") or []:
        if not isinstance(m, dict):
            continue
        for card in drafts.named_cards(str(m.get("evidence") or ""), cards):
            out[card["name"]] = out.get(card["name"], 0) + (2 if m.get("strength") == "strong" else 1)
    return out


def chosen(cards: list[dict], wanted: str, cited: dict[str, int] | None = None, n: int = WORK_GIVEN) -> list[Story]:
    """The work one job's drafts are written from (see the module notes): of the cards a draft may tell with
    something of their own to say, a starred job first, then by the fit map's citations (`cited`, `fit_names`)
    and the words they share with the job's requirements (`wanted`), the person's core work first at a tie; a
    CV-only project and an unstarred job only older CVs give come last. At most `n`."""
    cited = cited or {}
    pool = [c for c in story_cards(cards) if c.get("name") and (c.get("quotes") or c.get("summary"))]

    def order(k: int) -> tuple:
        c = pool[k]
        late = bool(c.get("cv_only") or (c.get("older_cv_job") and not c.get("star")))
        lead = c.get("kind") == JOB and bool(c.get("star"))
        score = relevance(c, wanted) + FIT_WEIGHT * cited.get(c["name"], 0)
        return late, not lead, -score, c.get("tier") != tiers.CORE, k

    return [card_story(pool[k]) for k in sorted(range(len(pool)), key=order)[:max(n, 0)]]


def without_listings(kept: str, cards: list[dict], listings: frozenset[str]) -> str:
    """The record part of a request (`kept`: the Profile block, then "[file > heading]" passages) without each
    passage of a file that lists projects (`listings`) unless it names exactly one card (see the module notes)."""
    if not listings or not kept:
        return kept
    parts = LABEL.split(kept)
    out = [parts[0]]
    for label, body in zip(parts[1::2], parts[2::2], strict=True):
        if label.split(" > ", 1)[0] in listings and len(drafts.named_cards(body, cards)) != 1:
            continue
        out.append(f"[{label}]{body}")
    return "".join(out).strip()

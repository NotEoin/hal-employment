"""Which pieces of work a pack's drafts tell, and in what order: one AI ranking of the person's projects and jobs
against the job.

`make(job, cards, found)` asks the AI once (prompt `story_rank`) to order every project and job card that is not
archived, with the plan's named project stories that have no card (`candidates`), against the job's own words
(`Job`: its title, summary, requirements and the company brief), `checked` keeps the names that are among
them, each with its one-line reason (`Ranked`), and `with_core` adds any core card the reply left out.
`order(found, cards, ranked, wanted)` turns a ranking into the stories a letter tells and the order the message
and the answers take the rest in (`Told`), and `fits(name, ranked)` says whether a piece of work is in the
ranking's top half, so it fits the job.

Design decisions, each with its reason:

- One call ranks the work, as two words in common chose it badly (`plan.relevance`, at least two shared
  words; 15 graded packs, 2026-11-08): a trading firm's software letter led with a voice assistant and a
  job-search app while a packet sniffer and a real-time autopilot were left to the fit table, a fintech
  letter left out the one project on the advert's own stack, and an ML letter told a first-year coursework
  instead of the dissertation. Ranking needs the whole job and every project at once, which a word count
  cannot weigh.
- The reply's names are kept only when they are names the request gave (case, punctuation, a trailing tag
  and a card's other names aside), each once: the ranking chooses among the person's work, never adds to it.
- Every core card (`profile.tiers.CORE`, what the person marked as their best work) is always ranked
  (`with_core`): one core project was missing from 3 graded rankings (2026-11-08). Three things in the code
  could leave one out: the prompt let the model leave out work that "does not fit"; a card with no summary
  was offered as a bare name; and names matched only up to case and spaces, so "Quill-Employment" or an
  alias was dropped. The prompt now asks for every piece of work and says what core means, a card with no
  summary is offered with its first quote or its part, names match by their words and aliases, and a core
  card the reply still leaves out is ranked last. No cap limits the cards a request lists.
- The person's core work leads (`led`): the letter tells the core cards the ranking places in its top half,
  in its order, then the next ranked; a card that is not core goes before a core one only when it is in the
  ranking's top `TOP_PLACES` (3) and the core one is not in the top half. With tier as a tie-break alone, a
  fintech letter told two first-year coursework apps while the person's core AI assistant came 13th, and a
  trading letter left a core real-time pathfinding project 14th (2026-11-08). The message and the answers
  take untold work in the same order. Archived work is never offered. A starred job leads whatever the
  ranking says (`plan.starred_first`): the person chose it (sections plan, 2026-11-09). An unstarred job only
  older CVs give comes after all other work (`plan.older_last`), as an older CV only fills gaps; a starred one
  leads like any starred job.
- A failed call (no usable names, or the AI unavailable for it) falls back to the word count
  (`plan.letter_stories`, `plan.fitting`), so a pack is never lost to the ranking.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.profile import tiers
from jobhunt.writing import plan as planning
from jobhunt.writing import stage
from jobhunt.writing.plan import Story

log = logging.getLogger(__name__)

PROMPT = "story_rank"
BRIEF_CHARS = 2000         # of the company brief a ranking request carries: what the employer does, not its history
SUMMARY_CHARS = 300        # of each card's summary: what the work is, not its whole write-up
TOP_PLACES = 3             # places at the top of a ranking where work that is not core may lead core work
TRAILING_TAG = re.compile(r"\s*\([^)]*\)\s*$")   # "Gullwing (real-time autopilot)": a tag the model added


@dataclass(frozen=True)
class Job:
    """What a ranking reads of the job: its title and employer, its summary, its requirements and the brief."""

    title: str
    employer: str
    summary: str = ""
    requirements: tuple[str, ...] = ()
    brief: str = ""


@dataclass(frozen=True)
class Ranked:
    """One piece of work in a ranking: its card's (or story's) name and why it fits, in the model's words."""

    name: str
    reason: str = ""


@dataclass(frozen=True)
class Told:
    """What a ranking makes of the stories: the letter's, and every story the message and answers may tell."""

    letter: tuple[Story, ...] = ()
    others: tuple[Story, ...] = ()
    ranked: tuple[Ranked, ...] = field(default=())


def candidates(found: list[Story], cards: list[dict]) -> list[dict]:
    """The work a ranking orders: every project and job card that is not archived, then each named project story
    of the plan that has no card (a passage of a project's own file, such as a dissertation), as card fields.
    Once the person starred a project, only starred projects are offered (`plan.story_cards`)."""
    out = [c for c in planning.story_cards(cards) if c.get("name", "").strip() and c.get("tier") != tiers.ARCHIVE
           and (str(c.get("kind", "")).startswith("project") or c.get("kind") == "job")]
    names = {c["name"] for c in out}
    out += [{"kind": "project", "name": s.name, "tools": list(s.tools),
             "summary": s.summary or (s.facts[0] if s.facts else ""), "planned": [], "context": s.context,
             "tier": "", "aliases": list(s.aliases), "role": s.role, "files": [], "quotes": list(s.facts),
             "tag": s.tag, "similar": []}
            for s in found if s.card is None and s.kind == planning.PROJECT and s.name and s.name not in names]
    return out


def _line(card: dict) -> str:
    """One card as a request lists it: its name, where it was done, its tier when core, what it is (its summary,
    else its first quote, else the person's part, so no card is a bare name) and its tools."""
    where = "a job" if card.get("kind") == "job" else stage.CONTEXT_WORDS.get(card.get("context", ""), "project")
    about = [where] + (["core"] if card.get("tier") == tiers.CORE else [])
    said = card.get("summary") or next(iter(card.get("quotes") or ()), "") or card.get("role") or ""
    summary = " ".join(str(said).split())[:SUMMARY_CHARS]
    tools = ", ".join(card.get("tools") or ())
    return (f"- {card['name']} ({'; '.join(about)})" + (f": {summary}" if summary else "")
            + (f". Tools: {tools}" if tools else ""))


def request(job: Job, offered: list[dict]) -> str:
    """The ranking request: the job's own words, then every piece of work offered, one a line."""
    wanted = "".join(f"- {r}\n" for r in job.requirements)
    return (f"Job: {job.title} at {job.employer}\n"
            + (f"What the job is: {job.summary}\n" if job.summary else "")
            + (f"\nRequirements:\n{wanted}" if wanted else "")
            + (f"\nWhat the employer does:\n{job.brief[:BRIEF_CHARS]}\n" if job.brief.strip() else "")
            + "\nThe applicant's work:\n" + "\n".join(_line(c) for c in offered))


def _key(name: str) -> str:
    """A name as names are matched: its words in lower case, punctuation aside."""
    return " ".join(re.findall(r"[a-z0-9+#*]+", (name or "").lower()))


def _names(offered: list[dict]) -> dict[str, str]:
    """Each matching key (`_key`) of an offered card's name and aliases, to the card's name; a key two cards
    share is left out, as it names neither for sure."""
    out: dict[str, str] = {}
    shared: set[str] = set()
    for c in offered:
        for said in (c["name"], *(c.get("aliases") or ())):
            key = _key(said)
            if key and out.get(key, c["name"]) != c["name"]:
                shared.add(key)
            out.setdefault(key, c["name"])
    return {k: v for k, v in out.items() if k and k not in shared}


def checked(reply: dict, offered: list[dict]) -> list[Ranked]:
    """The ranking of a reply: the names that are among `offered` (case, punctuation, a trailing tag and a card's
    aliases aside), each once, in the reply's order, with their reasons; [] for a reply that holds none."""
    by_key = _names(offered)
    picks = reply.get("ranking") if isinstance(reply, dict) else None
    out: list[Ranked] = []
    for pick in picks if isinstance(picks, list) else []:
        said = str((pick.get("name") if isinstance(pick, dict) else pick) or "")
        name = by_key.get(_key(said)) or by_key.get(_key(TRAILING_TAG.sub("", said)))
        if name is None:
            log.info("story ranking: dropped a name that is no card's: %r", said)
            continue
        if any(r.name == name for r in out):
            continue
        reason = " ".join(str(pick.get("reason") or "").split()) if isinstance(pick, dict) else ""
        out.append(Ranked(name, reason))
    return out


def with_core(ranked: list[Ranked], offered: list[dict]) -> list[Ranked]:
    """`ranked` with every offered core card it leaves out added last, so core work is always ranked."""
    left = [c["name"] for c in offered if c.get("tier") == tiers.CORE and all(r.name != c["name"] for r in ranked)]
    if left:
        log.info("story ranking: core work the reply left out, ranked last: %s", "; ".join(left))
    return [*ranked, *(Ranked(n) for n in left)]


def make(job: Job, cards: list[dict], found: list[Story]) -> list[Ranked]:
    """The ranking of the person's work for one job: one AI call, then `checked` and `with_core`; [] when there is
    nothing to rank or the call gives nothing usable."""
    offered = candidates(found, cards)
    if not offered:
        return []
    try:
        reply = llm.chat_json(prompts.text(PROMPT), request(job, offered), role="main", purpose=PROMPT)
    except llm.LLMUnavailable as e:
        log.warning("story ranking: the AI could not rank the work (%s); the job's words choose it", e)
        return []
    ranked = checked(reply, offered)
    return with_core(ranked, offered) if ranked else []


def _core(story: Story) -> bool:
    return story.card is not None and story.card.get("tier") == tiers.CORE


def led(first: list[Story]) -> list[Story]:
    """Ranked stories with the person's core work leading (see the module notes): the core ones in the top half,
    then the others in the top `TOP_PLACES`, then the other core ones, then the rest, each in ranking order."""
    half = (len(first) + 1) // 2
    groups: list[list[Story]] = [[], [], [], []]
    for k, s in enumerate(first):
        core = _core(s)
        groups[0 if core and k < half else 1 if not core and k < TOP_PLACES else 2 if core else 3].append(s)
    return [s for g in groups for s in g]


def order(found: list[Story], cards: list[dict], ranked: list[Ranked], wanted: str,
          n: int = planning.LETTER_STORIES) -> Told:
    """The stories a letter tells and the order the message and the answers take every story in (see the module
    notes); with no ranking, the word count's (`plan.letter_stories`, `plan.fitting`). `wanted` is the job's
    requirements. Once the person starred a project, the stories of unstarred projects are told nowhere
    (`plan.offered`)."""
    found = planning.offered(found, cards)
    if not ranked:
        fits = planning.fitting(found, cards, wanted)
        return Told(tuple(planning.letter_stories(found, cards, wanted, n)),
                    tuple(planning.older_last([*found, *fits])))
    by_story = {s.name: s for s in found if s.name}
    by_card = {c["name"]: c for c in planning.story_cards(cards) if c.get("name")}
    first: list[Story] = []
    for r in ranked:
        story = by_story.get(r.name) or (planning.card_story(by_card[r.name]) if r.name in by_card else None)
        if story is not None and story not in first:
            first.append(story)
    every = planning.older_last(planning.starred_first([*led(first), *(s for s in found if s not in first)]))
    told = [s for s in every if s.kind in (planning.PROJECT, planning.JOB) and s.facts][:n]
    if len(told) < planning.MIN_PROJECTS:
        told += [s for s in every if s.kind not in (planning.PROJECT, planning.JOB)]
    return Told(tuple(told[:n]), tuple(every), tuple(ranked))


def fits(name: str, ranked: list[Ranked]) -> bool:
    """True when the ranking places the work called `name` in its top half: the work fits this job (see
    `writing.paragraphs` and `writing.motive`); False with no ranking or for work it does not hold."""
    places = [r.name for r in ranked]
    return name in places and places.index(name) < (len(places) + 1) // 2


def said(ranked: list[Ranked]) -> str:
    """A ranking as a log line says it: "1. Name (reason); 2. ..."."""
    return "; ".join(f"{k}. {r.name}" + (f" ({r.reason})" if r.reason else "") for k, r in enumerate(ranked, 1))

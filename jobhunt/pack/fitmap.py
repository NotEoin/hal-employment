"""The fit map: each requirement of the advert matched to the person's evidence, made complete and consistent
with no AI after the one ask, and the eligibility flag it gives."""
import re

from .. import ai
from ..company import reputation
from ..cvstudio import cv
from ..profile import writing as profile_writing
from ..writing import check as writing_check
from ..writing import drafts, eligibility, repeats, stage
from .reading import _analyse, _job_text, _kept, _record_part, _requirements, _words, prompt


def _fit(who: dict, analysis: dict, facts: str, kept: str, job=None) -> dict:
    """The fit map: each requirement matched to evidence from the CV, every checked card and the record, then
    made complete and consistent with no AI (`complete_fit`, `consistent_fit`); a must-have the reply left out
    is asked for once more."""
    conditions = "\n".join(f"- {c}" for c in analysis.get("eligibility", []))
    cards = profile_writing.cards()
    shown = profile_writing.cards_block(cards)

    def ask(requirements: list[str]) -> dict:
        return with_card_tools(without_reasoning(ai.chat_json(
            prompt("fit", who), "Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
            + f"\n\nConditions:\n{conditions}\n\n{stage.today_lines()}\n\nCV:\n{facts}"
            + (f"\n\n{shown}" if shown else "") + _record_part("Employment record", kept), role="checker",
            timeout=ai.LONG_TIMEOUT_S, purpose="fit")), cards)

    fit = complete_fit(ask(_requirements(analysis)), analysis.get("must_have") or [], ask)
    fit = consistent_fit(fit, eligibility.person(), reputation.towns(reputation.advert_of(job)), facts)
    fit = checked_fit_tools(fit, cards, known_tools(cards), facts)
    fit = covered_gaps(fit, cards, profile_writing.skill_names(), facts)
    return with_sources({**fit, "gaps": repeats.unique_gaps(fit.get("gaps") or [])}, cards, facts)


SKILLS_PLACE = "your skills"     # where `covered_gaps` says a checked skill shows a gap's skill
CV_PLACE = "your CV"             # where it says the CV does
SETTINGS_WORDS = "from your settings"
SKILLS_WORDS = "from your skills"


def row_source(evidence: str, cards: list[dict], facts: str) -> str:
    """Where one fit row's evidence comes from: the card it names (its `source`), the current CV when a CV line
    states it, the person's checked skills, else the record as a whole."""
    named = drafts.named_cards(evidence, [c for c in cards if c.get("name")])
    if len(named) == 1 and named[0].get("source"):
        return named[0]["source"]
    if evidence.startswith(CV_PLACE) or drafts.stated_in(evidence, facts):
        return profile_writing.CURRENT_CV_WORDS
    if evidence.startswith(SKILLS_PLACE):
        return SKILLS_WORDS
    return profile_writing.RECORD_WORDS


def with_sources(fit: dict, cards: list[dict], facts: str) -> dict:
    """The fit map with each row citing where its evidence comes from (`row_source`), unless it already says."""
    return {**fit, "matches": [{**m, "source": m.get("source") or row_source(str(m.get("evidence") or ""), cards,
                                                                             facts)}
                               for m in fit.get("matches") or [] if isinstance(m, dict)]}


def known_tools(cards: list[dict]) -> list[str]:
    """Every tool a checked card or skill names, every tool a card's plan names (`planned`) and every tool only an
    older CV gives a card (`older_cv`): what a draft or fit row may wrongly give to a project."""
    return [t for c in cards for t in (*c["tools"], *c.get("planned", ()), *c.get("older_cv", ()))] \
        + profile_writing.skill_names()


NO_EVIDENCE = re.compile(r"^\s*(no|none|not)\b|\bno (evidence|mention)\b", re.I)   # a fit row that found nothing
ROW_SHARE = 0.6           # share of a requirement's words a fit row must hold to be that requirement's row
MISSING_GAP = "{requirement} (no evidence found)"   # a must-have still missing after the second ask


def _covers(said: str, requirement: str) -> bool:
    wanted = set(_words(requirement))
    return bool(wanted) and len(wanted & set(_words(said))) >= ROW_SHARE * len(wanted)


def missing_rows(fit: dict, must_haves: list[str]) -> list[str]:
    """The must-haves no row or gap of the fit map speaks for."""
    said = [str(m.get("requirement") or "") for m in fit.get("matches") or [] if isinstance(m, dict)]
    said += [str(g) for g in fit.get("gaps") or []]
    return [r for r in (str(r).strip() for r in must_haves) if r and not any(_covers(s, r) for s in said)]


def complete_fit(fit: dict, must_haves: list[str], ask) -> dict:
    """The fit map with a row or gap for every must-have: one more `ask(missing)` for those the reply left out,
    and a gap for any still missing (a requirement went missing from a real fit table, 2026-11-07)."""
    missing = missing_rows(fit, must_haves)
    if not missing:
        return fit
    more = ask(missing)
    fit = {**fit, "matches": [*(fit.get("matches") or []), *(more.get("matches") or [])],
           "gaps": [*(fit.get("gaps") or []), *(more.get("gaps") or [])]}
    left = missing_rows(fit, missing)
    return {**fit, "gaps": [*fit["gaps"], *(MISSING_GAP.format(requirement=r) for r in left)]}


def consistent_fit(fit: dict, you, towns: list[str], facts: str) -> dict:
    """The fit map with rows that agree with their evidence and the person's settings: a row or gap about
    where the job is that where the person lives settles is met, with the reason
    (`eligibility.where_you_live_reason`); a row whose evidence says there is none is a gap, never partial
    or strong. A real table rated "London office three days a week" partial, "no evidence of refusal", for a
    person living in London (2026-11-07)."""
    matches, gaps = [], []
    for m in (dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)):
        requirement = str(m.get("requirement") or "")
        reason = eligibility.where_you_live_reason(requirement, you, towns, facts)
        if reason:
            matches.append({**m, "evidence": reason, "strength": "strong", "source": SETTINGS_WORDS})
        elif NO_EVIDENCE.search(str(m.get("evidence") or "")):
            gaps.append(requirement)
        else:
            matches.append(m)
    for gap in fit.get("gaps") or []:
        reason = eligibility.where_you_live_reason(str(gap), you, towns, facts)
        if reason:
            matches.append({"requirement": str(gap), "evidence": reason, "strength": "strong",
                            "source": SETTINGS_WORDS})
        else:
            gaps.append(gap)
    return {**fit, "matches": matches, "gaps": gaps}


def checked_fit_tools(fit: dict, cards: list[dict], known: list[str], facts: str) -> dict:
    """The fit map with every tool a row gives to a project card that never used it taken out of the row, and
    each card of a similar name named with its tag (`drafts.evidence_checked`).

    A part of a row's evidence left with no tool is dropped; a row that lost evidence is partial at best, and
    one left with none is a gap. A fit row said two projects used Pandas, Scikit-learn and Matplotlib when no
    source had either using them (2026-11-07), and rows gave a local assistant its namesake's stack
    (2026-11-08).
    """
    matches, gaps = [], list(fit.get("gaps") or [])
    for m in (dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)):
        evidence, dropped = drafts.evidence_checked(str(m.get("evidence") or ""), cards, known, facts)
        if not dropped:
            matches.append({**m, "evidence": evidence} if m.get("evidence") else m)
        elif evidence:
            matches.append({**m, "evidence": evidence,
                            "strength": "partial" if m.get("strength") == "strong" else m.get("strength")})
        else:
            gaps.append(str(m.get("requirement") or ""))
    return {**fit, "matches": matches, "gaps": gaps}


def with_card_tools(fit: dict, cards: list[dict]) -> dict:
    """The fit map with every requirement that names a checked card's tool given that card as evidence.

    A gap or a "no evidence" row naming a tool a card lists becomes a partial match citing the card: the
    fit table said "No evidence of AWS" although a project card lists AWS (2026-11-06).
    """
    def card_for(requirement: str) -> str:
        for c in cards:
            tool = next((t for t in c["tools"] if drafts.names_tool(requirement, t)), None)
            if tool:
                return f"{c['kind']}: {c['name']} (tools: {tool})"
        return ""

    matches = [dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)]
    for m in matches:
        found = card_for(str(m.get("requirement") or "")) if NO_EVIDENCE.search(str(m.get("evidence") or "")) else ""
        if found:
            m.update(evidence=found, strength="partial")
    gaps = []
    for gap in fit.get("gaps") or []:
        found = card_for(str(gap))
        if found:
            matches.append({"requirement": str(gap), "evidence": found, "strength": "partial"})
        else:
            gaps.append(gap)
    return {**fit, "matches": matches, "gaps": gaps}


# The model's own reasoning or a sentence about the table, never a fact of the person's: "I will mark this as a
# gap if strict ..." reached a fit table (2026-11-08).
REASONING = re.compile(r"\b(?:I(?:'ll| will| would| shall| am going to| need to| think)|let me|let's|if (?:being )?"
                       r"strict(?:ly)?|to be strict|this (?:table|row|column)|the fit (?:map|table))\b", re.I)
# Words that mark a whole cell as the model thinking aloud: a reply of about 400 words, "Wait, re-reading the
# advert ... However, the instruction says ...", survived the sentence filter in a fit row (2026-11-08), so a cell
# holding any of these is reasoning throughout and is taken out whole. "Wait" counts only before a comma or a stop,
# as "wait times" can be a fact.
THINKING = re.compile(r"\b(?:wait\s*[,.!:;]|(?:let me|re-?reading|however, the|I need to|the instruction says)\b)",
                      re.I)
CELL_WORDS = 40            # words of a fit row's evidence or a gap; a fact of the record is a sentence, not a page


def _capped(text: str) -> str:
    """`text` when within `CELL_WORDS`, else its first sentence, cut at `CELL_WORDS` words when still longer."""
    if len(text.split()) <= CELL_WORDS:
        return text
    first = next(iter(writing_check.sentences(text)), "")
    words = first.split()
    return first if len(words) <= CELL_WORDS else " ".join(words[:CELL_WORDS]) + " ..."


def _unreasoned(text: str) -> str:
    """`text` without the model's own reasoning: "" for a cell that thinks aloud (`THINKING`), else without its
    sentences matching `REASONING`, at most `CELL_WORDS` words (`_capped`)."""
    if THINKING.search(text):
        return ""
    return _capped(" ".join(s for s in writing_check.sentences(text) if not REASONING.search(s)))


def without_reasoning(fit: dict) -> dict:
    """The fit map with the model's reasoning taken out of each row's evidence and each gap, each at most
    `CELL_WORDS` words; a row left with no evidence is a gap, and a gap left with no words is gone."""
    matches, gaps = [], []
    for m in (dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)):
        said = str(m.get("evidence") or "")
        kept = _unreasoned(said)
        if kept or not said.strip():
            matches.append({**m, "evidence": kept} if said.strip() else m)
        else:
            gaps.append(str(m.get("requirement") or ""))
    gaps += [g for g in (_unreasoned(str(gap)) for gap in fit.get("gaps") or []) if g]
    return {**fit, "matches": matches, "gaps": gaps}


# What a fit gap may call a skill, with the tools and methods that show it (2026-11-08): the fit table listed
# CI/CD and graph algorithms as gaps although the CV lists GitHub Actions and a project uses A*.
EQUIVALENTS = (
    (("CI/CD", "CI / CD", "continuous integration", "continuous delivery", "continuous deployment"),
     ("GitHub Actions", "GitLab CI", "Jenkins")),
    (("graph algorithms", "graph algorithm", "graph search"), ("A*", "Dijkstra", "BFS", "breadth-first search")),
)


def _gap_terms(gap: str, named: list[str]) -> list[str]:
    """The skills a gap names: each of `named` (a card's tool or a checked skill) it names, and for a skill
    `EQUIVALENTS` knows, its names and the tools and methods that show it."""
    out = [t for t in named if t.strip() and drafts.names_tool(gap, t)]
    for names, shown_by in EQUIVALENTS:
        if any(drafts.names_tool(gap, n) for n in names):
            out += [*names, *shown_by]
    return list(dict.fromkeys(out))


def covered_gaps(fit: dict, cards: list[dict], skills: list[str], facts: str) -> dict:
    """The fit map with each gap naming a skill the person's record shows made a partial match citing where:
    a card's tools or quotes, a checked skill, or the CV (`facts`), for the skill or one that shows it
    (`EQUIVALENTS`)."""
    places = [(f"{c['kind']}: {c['name']}", "\n".join([*c.get("tools", ()), *c.get("quotes", ())])) for c in cards]
    places += [(SKILLS_PLACE, "\n".join(skills)), (CV_PLACE, facts)]
    named = [*(t for c in cards for t in c.get("tools", ())), *skills]
    matches, gaps = list(fit.get("matches") or []), []
    for gap in fit.get("gaps") or []:
        found = next(((where, t) for t in _gap_terms(str(gap), named) for where, text in places
                      if drafts.names_tool(text, t)), None)
        if found:
            matches.append({"requirement": str(gap), "evidence": f"{found[0]} ({found[1]})", "strength": "partial"})
        else:
            gaps.append(gap)
    return {**fit, "matches": matches, "gaps": gaps}


def read_job(job, who: dict, facts: str) -> dict:
    """What one job's documents are written from, as `write_letter` takes it: {analysis, kept, fit}."""
    analysis = _analyse(job, who)
    kept = _kept(analysis)
    return {"analysis": analysis, "kept": kept, "fit": _fit(who, analysis, facts, kept, job)}


def _eligibility_flag(job, who: dict, fit: dict, conditions: list) -> str:
    # with no condition to judge, Claude still answered "eligible": false ("nothing to match", 2026-10-23)
    note = str(fit.get("eligibility_notes") or "")
    if fit.get("eligible") is False and conditions and not eligibility.where_you_live_settles(
            note, eligibility.person(), reputation.towns(reputation.advert_of(job)), cv.facts_text()) \
            and not eligibility.unfounded_degree(note, _job_text(job)):
        return f"ELIGIBILITY: {fit.get('eligibility_notes', 'may not be eligible')}"
    line = str(who.get("eligibility_line") or "")
    if who.get("not_eligible") and not eligibility.where_you_live_settles(
            line, eligibility.person(), reputation.towns(reputation.advert_of(job)), cv.facts_text()):
        return f"ELIGIBILITY: {line or 'the advert may not be open to you'}"
    return ""

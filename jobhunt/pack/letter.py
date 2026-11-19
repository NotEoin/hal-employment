"""The cover letter: written whole in one call from the job's one request, checked once, and the Tone, Length,
Evidence and Checked notes beside it."""
from __future__ import annotations

import logging

from jobhunt.cvstudio import cv
from jobhunt.pack.checks import CUT_FLAG, CUT_WHAT, DANGLING_FLAG, ECHO_FLAG, Draft, check_drafts, settle
from jobhunt.pack.drafting import Given, _draft, given
from jobhunt.pack.fitmap import _fit, known_tools
from jobhunt.pack.reading import Brief, _analyse, _job_text, _kept, passages, person, previous_applications
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import drafts, eligibility, flow, plain, relocation, titles

log = logging.getLogger(__name__)


NOTES_HEADING = "Letter notes"
NOTE_KEYS = {"Tone": "tone", "Length": "length", "Evidence": "evidence", "Checked": "checked"}
TONE_LABELS = {"plain": "Plain", "formal": "Formal", "warm": "Warm"}   # the setting's choices (catalog/cvdocs.py)
CHECKED_CLEAN = "Every fact is in your CV, record or the advert. No numbers were invented."
REVISED = "Hal revised it once for its writing rules. "   # before the Checked line when the first draft broke one
SOFTENED = "Hal rewrote it once to take out claims your facts do not support. "   # ... when it made such claims
# A letter still short after its one redraft (`flow.short`).
SHORT_FLAG = ("the letter is {n} words, well under the {words} you prefer; add a paragraph of your own evidence "
              "before you send it")
SHORT_NOTE = ("The letter is {n} words; the applicant prefers about {words}. Write it to that length with more of "
              "their evidence from the facts above, never with filler.")
ALIKE_NOTE = ("These paragraphs open like an earlier one:\n{listed}\nOpen each paragraph differently, with at most "
              "one opening with \"I\".")
CLOSER_NOTE = ("These paragraphs end on a sentence that only says the work is relevant:\n{listed}\nTake each one "
               "out, or end on a fact instead; let the facts make the case.")
EVIDENCE_SHOWN = 3        # pieces of evidence named on the letter's Evidence line


def letter_notes(who: dict, letter: str, evidence: list[str], flags: list[str], *, revised: bool = False,
                 softened: bool = False) -> dict:
    """The Tone, Length, Evidence and Checked lines shown beside a letter; `revised` when the writer had to
    redraft it once, `softened` when the redraft took out claims the facts do not support."""
    from jobhunt.core import settings

    tone = TONE_LABELS.get(settings.get("cvdocs.letter_tone"), who["tone"].capitalize())
    letter_flags = [f for f in flags if not f.startswith(("REAPPLICATION", "ELIGIBILITY"))]
    # the fit map's evidence is sentences ("... spreadsheet.", 2026-10-23), so the line joins them with ";"
    shown = [e.strip().rstrip(".") for e in evidence[:EVIDENCE_SHOWN] if e.strip()]
    return {"tone": f"{tone} (your setting)",
            "length": f"{len(letter.split())} words (you prefer about {who['letter_words']})",
            "evidence": "; ".join(shown) or "None named",
            "checked": (SOFTENED if softened else REVISED if revised else "") + (
                CHECKED_CLEAN if not letter_flags else
                f"{len(letter_flags)} {'thing' if len(letter_flags) == 1 else 'things'} to check before "
                "sending: see Check before sending.")}


def employer_words(brief: Brief) -> tuple[str, str]:
    """The advert and the company brief: the employer's own words, which the checks never flag for restating."""
    return _job_text(brief.job), brief.about


def check_all(brief: Brief, facts: Given, written: list[Draft]) -> None:
    """The one check of a job's drafts (`checks.settle`) against what their writer was given."""
    settle(brief, written, employer=employer_words(brief), given=facts.checked(), own=facts.words(),
           names=(titles.of_job(brief.job), brief.job["company"]))


def _letter_extra(brief: Brief):
    def extra(letter: str) -> list[str]:
        out = []
        words = brief.who.get("letter_words")
        if flow.short(letter, words):
            out.append(SHORT_NOTE.format(n=flow.word_count(letter), words=words))
        alike = flow.alike_openings(letter)
        if alike:
            out.append(ALIKE_NOTE.format(listed="\n".join(f"- {s}" for s in alike)))
        closers = flow.relevance_closers(letter)
        if closers:
            out.append(CLOSER_NOTE.format(listed="\n".join(f"- {s}" for s in closers)))
        return out
    return extra


def drafted(brief: Brief, facts: Given, *, request: str = "", trimmed: set[str] | None = None) -> Draft:
    """The letter written in one call from the job's request (`Given.request`), as a `Draft` for `settle`;
    `request` holds the person's own words when they asked for a change, and a reply cut off twice is added to
    `trimmed`."""
    who = brief.who
    trimmed = set() if trimmed is None else trimmed
    asked = facts.request("letter", asked=request)
    sources = employer_words(brief)
    names = (titles.of_job(brief.job), brief.job["company"])

    def again(note: str, first: str) -> str:
        return _draft(who, "letter", f"{asked}\n\nYour first draft:\n{first}\n\n{note}", trimmed)

    d = Draft("letter", _draft(who, "letter", asked, trimmed), lambda note: "",
              echoed=lambda text: drafts.echoed(drafts.first_paragraph(text), *sources, names),
              extra=_letter_extra(brief))
    d.rewrite = lambda note: again(note, d.value)
    return d


def finished(con, brief: Brief, facts: Given, d: Draft, fit: dict, *, trimmed: set[str] = frozenset()) -> dict:
    """The settled letter tidied, with its "Check before sending" lines and its notes: {text, flags, used, notes,
    revised, softened}."""
    who, cards = brief.who, profile_writing.cards()
    sources = employer_words(brief)
    letter = plain.tidy(relocation.without_own_move(d.value, eligibility.person(con)), cards)
    flags = check_drafts(brief, letter, sources=(*sources, facts.words()), claims=d.claims)
    names = (titles.of_job(brief.job), brief.job["company"])
    flags += [f"{ECHO_FLAG}: {s}" for s in drafts.echoed(drafts.first_paragraph(letter), *sources, names)]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(letter)]
    if flow.short(letter, who.get("letter_words")):
        flags.append(SHORT_FLAG.format(n=flow.word_count(letter), words=who["letter_words"]))
    if d.value in trimmed:
        flags.append(CUT_FLAG.format(what=CUT_WHAT["letter"]))
    used = passages(brief.kept)
    told = [s.name for s in facts.work if s.card is not None and s.card in drafts.named_cards(letter, cards)]
    shown = told or used or [m.get("evidence", "") for m in fit.get("matches", []) if m.get("strength") == "strong"]
    shown = [e for e in (drafts.evidence_checked(s, cards, known_tools(cards), brief.facts)[0] for s in shown) if e]
    return {"text": letter, "flags": flags, "used": used, "revised": d.revised, "softened": d.softened,
            "notes": letter_notes(who, letter, shown, flags, revised=d.revised, softened=d.softened)}


def write_letter(con, job, *, who: dict | None = None, analysis: dict | None = None, about: str = "",
                 kept: str | None = None, fit: dict | None = None, request: str = "") -> dict:
    """Writes and checks a cover letter for one job: {text, flags, used, notes, revised, softened}.

    The same path for a pack's letter (which `pack.build` checks with the message and answers in one call) and
    for a letter drafted at form time (`documents.draft_letter`) or rewritten as the person asked
    (`docs.rewrite_letter`): the advert read, the record searched and the fit map made, each unless given, one
    request assembled (`drafting.given`), one writing call, one check (`settle`) and at most one redraft.
    `request` holds the person's own words when they asked for a change.
    """
    who = who or person(job)
    facts = cv.facts_text()
    analysis = analysis if analysis is not None else _analyse(job, who)
    kept = kept if kept is not None else _kept(analysis)
    fit = fit if fit is not None else _fit(who, analysis, facts, kept, job)
    brief = Brief(job, who, analysis, about, facts, kept)
    given_facts = given(brief, fit, previous_applications(con, job))
    trimmed: set[str] = set()
    d = drafted(brief, given_facts, request=request, trimmed=trimmed)
    check_all(brief, given_facts, [d])
    return finished(con, brief, given_facts, d, fit, trimmed=trimmed)

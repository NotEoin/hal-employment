"""What the letter, the message and the answers are written from: one request assembled by code for one job
(`Given`), and a draft asked for again when it is cut off.

`given(brief, fit, con)` assembles, with no AI, everything one job's drafts may use: the job's part (the role as
a person would say it, the advert's needs from the reading, the employer note from the brief), the fit map as a
guide, and the applicant's part (today and their stage, who is writing, the work `writing.plan.chosen` picks
with where each piece was done and the person's part, the modules the job names, the CV and the passages of
their own documents). `Given.request(kind)` is the one request a letter, message or set of answers is written
from, and `Given.checked()` the facts the one check (`checks.settle`) reads the drafts against.

Design decisions, each with its reason:

- One request and one writing call a document (owner, 2026-11-19: "a single prompt assembled", about 5 minutes
  per application at most). Choosing the facts is code's job (`writing.plan.chosen`), so no call is spent on
  it; the request stays well inside the context: about 4,000 tokens for each document, 5,000 with the
  prompt, for three real adverts on a copy of real data (2026-11-19), and its size is logged.
- The fit map is given as a guide to what to argue and never as a fact, and the check never reads it as one:
  the AI wrote it from the same facts. Needs the facts do not show are named so the writer never claims them.
- The advert itself is not in the request, only its reading: the needs, the summary and the employer note.
  Letters given the advert gave its sentences back as the reason (26 sentences in five packs, 2026-11-07).
- Each draft is written at `DRAFT_TEMPERATURE`, cooler than other prose, as letters drifted from their facts
  when warmer (2026-11-08).
- A letter's request carries one or two short excerpts of the person's own letters while "Match my own
  writing" is on (`sections.letters.voice_excerpts`), to match their tone and sentence length, never as
  facts: the check never counts their words (sections plan, 2026-11-09).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from jobhunt import ai, record
from jobhunt.pack.reading import SOMEONE, Brief, _record_part, _requirements, field_of, prompt
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import drafts, fields, opening, spelling, stage, titles
from jobhunt.writing import plan as planning

log = logging.getLogger(__name__)


CUT_NOTE = "Your last reply stopped mid-sentence. Write the whole draft again, and end it with a full sentence."
ROOM_CTX = 16384          # tokens for the second ask after a cut-off reply, as for the CV score and critique
DRAFT_TEMPERATURE = 0.4   # see the module notes
NOTE_PARA = re.compile(r"\(.*\)\Z|(notes?|word count)\b", re.S | re.I)   # a closing paragraph about the draft
ABOUT_CHARS = 1500        # of the employer note a request carries: what the employer does, never a page of it
FIT_ROWS = 12             # fit map rows a request carries; an advert lists fewer needs than this
CHARS_PER_TOKEN = 4       # for the logged size of a request, as `ai_usage` estimates it
VOICE_HEADING = ("How this person writes (from their own letters; match the tone and sentence length, never copy "
                 "a fact or a phrase from it):")
NOT_A_FACT = "a guide to what to argue, never a fact"
AVAILABILITY = ("Mention availability only if the advert's start date is flexible; for a fixed intake, refer to "
                "that intake instead.")


@dataclass(frozen=True)
class Given:
    """What one job's drafts are written from and checked against (see the module notes)."""

    job: str                                   # the job's part of every request
    fit: str                                   # the fit map, a guide never a fact
    applicant: str                             # who is writing, today, the earlier applications
    work: tuple[planning.Story, ...]           # the work chosen for this job (`plan.chosen`)
    modules: tuple[str, ...]                   # the modules the job's requirements name
    cv: str
    kept: str                                  # the Profile block and the passages of their own documents
    conventions: dict[str, str]                # the field's conventions, by kind ("letter", "answer")
    voice: tuple[str, ...] = ()                # excerpts of the person's own letters, for a letter's request

    def facts(self, *, checking: bool = False) -> str:
        """The applicant's facts as a request gives them; `checking` for the check's request, whose record heading
        says only where the passages come from."""
        work = "\n\n".join(s.block() for s in self.work)
        record_words = (f"Employment record ({record.OWN_DOCUMENTS})" if checking else
                        f"Employment record ({record.OWN_DOCUMENTS}; use it where the CV is silent)")
        return "\n\n".join(p for p in (
            self.applicant,
            ("The applicant's work that fits this job best (keep where each was done and their part as given):\n"
             + work) if work else "",
            ("Modules of theirs the job's needs name: " + ", ".join(self.modules)) if self.modules else "",
            f"CV:\n{self.cv}",
        ) if p) + _record_part(record_words, self.kept)

    def words(self) -> str:
        """Every word the applicant's facts hold: what a number in a draft may come from."""
        return "\n".join([self.cv, self.kept, *(s.words() for s in self.work), *self.modules])

    def checked(self) -> str:
        """The facts the one check reads every draft against: what the writer was given, the fit map aside."""
        return self.facts(checking=True)

    def request(self, kind: str, *, extra: str = "", asked: str = "") -> str:
        """The one request a `kind` of draft ("letter", "message" or "answers") is written from: the job, the
        fit map, the applicant's facts, the field's conventions, the person's voice for a letter, `extra` (the
        questions, the message's opening) and the change the person `asked` for."""
        voice = "\n".join([VOICE_HEADING, *(f"<<<\n{v}\n>>>" for v in self.voice)]) \
            if kind == "letter" and self.voice else ""
        parts = [extra if kind == "answers" else "", self.job, self.fit, self.facts(),
                 self.conventions.get("answer" if kind == "answers" else kind, ""), voice,
                 extra if kind != "answers" else "",
                 f"The applicant asked for this change: {asked}" if asked else ""]
        text = "\n\n".join(p.strip() for p in parts if p and p.strip())
        log.info("%s request: about %d tokens", kind, len(text) // CHARS_PER_TOKEN)
        return text


def _job_part(brief: Brief) -> str:
    job, analysis = brief.job, brief.analysis
    needs = [str(r) for r in analysis.get("must_have") or [] if str(r).strip()]
    liked = [str(r) for r in analysis.get("nice_to_have") or [] if str(r).strip()]
    lines = [f"The job: {titles.of_job(job)} at {job['company']}",
             f"What the role is: {str(analysis.get('summary') or '').strip()}"]
    if needs:
        lines += ["What they need:", *(f"- {r}" for r in needs)]
    elif not liked:
        lines += ["What they need:", *(f"- {r}" for r in _requirements(analysis))]
    if liked:
        lines += ["What they would also like:", *(f"- {r}" for r in liked)]
    about = (brief.about or "").strip()
    if about:
        lines += ["What the employer does (Hal's note from the advert and the web; never copy its words):",
                  about[:ABOUT_CHARS]]
    return "\n".join(lines)


def _fit_part(fit: dict) -> str:
    rows = [m for m in fit.get("matches") or [] if isinstance(m, dict) and str(m.get("evidence") or "").strip()]
    lines = [f"- {m.get('requirement', '')}: {m.get('evidence', '')} ({m.get('strength', '')})"
             for m in rows[:FIT_ROWS]]
    gaps = [str(g) for g in fit.get("gaps") or [] if str(g).strip()]
    out = (f"Hal's fit map (how the applicant's evidence meets each need; {NOT_A_FACT}):\n" + "\n".join(lines)
           if lines else "")
    if gaps:
        out += ("\n" if out else "") + "Needs their facts do not show (never claim them): " + "; ".join(gaps)
    return out


def _applicant_part(brief: Brief, history: str) -> str:
    now = stage.current()
    name = str(brief.who.get("name") or "")
    who = ", ".join(p for p in ("" if name == SOMEONE else name, opening.described(now)) if p)
    return "\n".join(p for p in (
        stage.today_lines(now), f"Who is writing: {who}" if who else "",
        f"Earlier applications to this employer: {history}. Do not mention them unless asked." if history else "",
        AVAILABILITY) if p)


def given(brief: Brief, fit: dict, history: str = "") -> Given:
    """Everything one job's drafts are written from, assembled with no AI (see the module notes); `history`
    is the earlier applications to the same employer (`reading.previous_applications`)."""
    cards = profile_writing.cards()
    wanted = "\n".join(_requirements(brief.analysis))
    work = tuple(planning.chosen(cards, wanted, planning.fit_names(fit, cards)))
    course = stage.current().institution
    modules = planning.relevant_modules(profile_writing.marked_modules(institution=course), wanted,
                                        "\n".join(s.words() for s in work))
    kept = planning.without_listings(brief.kept if brief.kept != "none" else "", cards, profile_writing.listings())
    resolved = field_of(brief.job, brief.who, brief.analysis)
    return Given(_job_part(brief), _fit_part(fit), _applicant_part(brief, history), work, modules, brief.facts, kept,
                 {k: fields.block(resolved, k) for k in ("letter", "answer")}, _voice())


def _voice() -> tuple[str, ...]:
    """Excerpts of the person's own letters for a letter's request (`sections.letters.voice_excerpts`); none
    while "Match my own writing" (`cvdocs.voice`) is off."""
    from jobhunt.core import settings
    from jobhunt.sections import letters

    return tuple(letters.voice_excerpts()) if settings.get("cvdocs.voice") else ()


def spelt(text: str, who: dict) -> str:
    """An AI draft in the person's spelling (`who["spelling_code"]`; UK English when it is unset)."""
    return spelling.respell(text, who.get("spelling_code"))


def _draft(who: dict, prompt_id: str, user: str, trimmed: set[str]) -> str:
    """A text draft (letter or message) at `DRAFT_TEMPERATURE`, asked for again once with more room when the
    reply stops mid-sentence (`drafts.cut_off`); when that one stops too it is kept to its last full sentence
    and added to `trimmed`, so "Check before sending" can say so if it is the draft that is kept."""
    def ask(text: str, ctx: int | None = None) -> str:
        said = ai.chat(prompt(prompt_id, who), text, role="writer", ctx=ctx, temperature=DRAFT_TEMPERATURE,
                       timeout=ai.LONG_TIMEOUT_S, purpose=prompt_id).strip()
        return spelt(drop_notes(ai.undash(said)), who)

    draft = ask(user)
    if not drafts.cut_off(draft):
        return draft
    again = ask(f"{user}\n\n{CUT_NOTE}", ROOM_CTX)
    if not drafts.cut_off(again):
        return again
    kept = drafts.last_full_sentence(again)
    trimmed.add(kept)
    return kept


def drop_notes(text: str) -> str:
    """A draft without the model's closing notes about it, and without Markdown bold."""
    # Claude ended a message with "(About 75 words. The advert names no individual, ...)" and began one with
    # "**Subject:** ..." (2026-10-23); neither belongs in what the person sends
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    while len(paras) > 1 and NOTE_PARA.match(paras[-1].strip()):
        paras.pop()
    return re.sub(r"\*\*(.+?)\*\*", r"\1", "\n\n".join(paras))

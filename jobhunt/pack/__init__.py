"""Application packs: everything needed to apply for one job, drafted locally.

A pack holds a company brief, a requirements-to-evidence map with honest gaps,
a tailored CV, a cover letter, a short message for the person hiring, and
answers to common form questions. It is written to the job's folder (`documents.pack_dir`), and a
`docs.prepared` event says one is ready. With the Hal add-on on, a copy also goes into its Applications
folder (`addon.applications_mirror`). Nothing is sent or submitted.

This module builds the pack (`build`) and exports every name of its parts, so callers import `jobhunt.pack`:
- `reading`: the prompt values, the advert reading, the record passages, the evidence plan and `Brief`.
- `fitmap`: the fit map and the eligibility flag.
- `checks`: the writing and claim checks every draft goes through, and the refusals taken out.
- `drafting`: the shared request, whole drafts, the paragraph scope and the story ranking.
- `hiring`: who the message and the letter go to.
- `letter`: the cover letter and its notes.
- `others`: the message to the person hiring and the form answers.
- `markdown`: the pack's Markdown file, its company check and its add-on copy.

Design notes:
- The prompts live in `ai/prompts/*.txt` and name no one: `cvstudio.context.build` fills them from the
  `you.*`, `goal.*` and `cvdocs.*` settings and the CV type. `prompt(id)` renders
  the text when it is called, so callers see what the person's edits and settings give.
- One letter path: `write_letter` reads the advert, the record and the fit map, writes the letter and checks
  it, for a pack and for a letter a form asks for (`documents.draft_letter`), so every letter is checked
  the same way.
- Every draft the AI writes is respelt to the person's spelling (`spelt`, `writing.spelling`) before it is
  checked, as dashes are taken out (`ai.undash`): a spelling has one right form, so it needs no revision.
- Every draft goes through the writing checker (`writing.check`). A high-severity break (an invented number,
  a machine-made word, a staged contrast) makes the writer revise once with the problems quoted; what is
  left is listed under "Check before sending", never fixed in a loop.
- The claim check (`unsupported_claims`) works the same way (`fix_claims`) for a draft with no story to tell
  (see below): the letter, the message and the answers are each rewritten once without the claims about the
  person their facts do not support, checked again, and only what is left is listed. The advert and the
  brief are the employer's own words, never flagged for being restated.
- The letter's first paragraph and the "why do you want to work here" answers are checked, with no AI, for
  sentences that restate the advert or the brief (`drafts.echoes`) and rewritten once to tie something of
  the applicant's own to the employer (`fix_echoes`), before the claim check reads them: 26 generic
  sentences in five real packs were the advert's words given back as a reason (2026-11-07).
- An offer to relocate to the town the person lives in is taken out of the letter, the message and the
  answers with no AI (`relocation.without_own_move`): a letter offered to relocate to the London office of a
  person living in London (2026-11-07).
- A form answer that repeats an earlier one nearly word for word is rewritten once, with the others, to
  use other evidence (`fix_repeats`, `repeats.repeated`), and the fit map lists each gap once
  (`repeats.unique_gaps`): two answers of a real pack gave the same details and a gap was listed twice
  (2026-11-07).
- Each draft rests on an evidence plan picked before it is written (`evidence_plan`, `writing.plan`): quotes
  of the CV, the Profile block and the record, each with its card's context and the person's part, as a
  local model framed projects in the wrong place and wrote team work as solo when it wrote freely
  (2026-11-07). The letter's reason for the employer comes from a pairing of one employer fact with one plan
  item (`writing.motive`), or it gives none, as letters gave generic motivation that echoed the advert.
- The plan's items are grouped into stories, one per piece of work (`plan.stories`), and the letter, the
  message and the answers tell each story in a paragraph of its own, drafted from that story's facts alone
  and checked against them alone (`writing.paragraphs`, `_story_letter`, `_story_others`), with the letter's
  opening and closing drafted from who is writing, the role and the reason (`writing.frame`). A grader capped
  every pack at 2 of 5 for readability across four designs that drafted and checked sentence by sentence
  with evidence markers, rewrote, cut and smoothed them, named projects with "For <card>, " and cut the
  advert's lead-ins (15 packs, 2026-11-08); all of that is gone. Every starred card is a story too, from the
  card's own facts (`plan.with_showcase`), so a pack is drafted whole and checked with `fix_claims`, as before
  the plan, only when the plan tells no story and the record has no starred card (2026-11-09).
- Which work the drafts tell comes from one AI ranking per pack (`story_ranking`, `writing.rank`), made by
  `write_letter` and passed to `write_others`: word overlap with the advert led letters with weak projects
  (15 graded packs, 2026-11-08). The letter tells the ranking's best, the message and answers the next.
- No answer and no message is left blank (`_story_others`, 2026-11-08): 8 of 15 graded packs had an empty
  answer or a message with nothing after its introduction, as a dropped paragraph left nothing. The next
  story is tried, up to `STORY_TRIES`, and what is still empty is written by the whole-draft path.
- The pack's checklist names no command: every action is a button on the Documents page.
- Each pack logs how many model calls it made (`build`, `ai.counted`): a pack made about 55 with sentence
  checks, and the stories aim for about 30 at most (2026-11-08), so the count is logged to be measured.
"""
import logging
from pathlib import Path

from .. import ai, db, reputation
from ..core import events
from ..cvstudio import cv
from ..profile import writing as profile_writing
from ..writing import drafts, eligibility, motive, rank
from ..writing import plan as planning
from .checks import (
    CUT_FLAG,
    DANGLING_FLAG,
    ECHO_FLAG,
    check_drafts,
    fix_claims,
    fix_echoes,
    revise_once,
    unsupported_claims,
    unsupported_numbers,
    without_refusals,
)
from .drafting import (
    CUT_NOTE,
    DRAFT_TEMPERATURE,
    _context,
    fitting_names,
    paragraph_scope,
    shared_context,
    spelt,
    story_ranking,
)
from .fitmap import (
    CELL_WORDS,
    EQUIVALENTS,
    MISSING_GAP,
    REASONING,
    THINKING,
    _eligibility_flag,
    _fit,
    checked_fit_tools,
    complete_fit,
    consistent_fit,
    covered_gaps,
    known_tools,
    read_job,
    row_source,
    with_card_tools,
    with_sources,
    without_reasoning,
)
from .hiring import FIND_PREFIX, hiring_contacts, message_to, salutation
from .letter import (
    CHECKED_CLEAN,
    LETTER_TRIES,
    REVISED,
    SHORT_FLAG,
    SHORT_MIDDLE,
    SOFTENED,
    _item_note,
    _letter_bodies,
    _short_middle,
    _story_letter,
    letter_frame,
    write_letter,
)
from .markdown import NOTHING_TO_CHECK, PREPARED, _markdown, add_company_check, mirror_note, read_letter_notes
from .others import (
    NO_ANSWER,
    REPEAT_FLAG,
    STORY_TRIES,
    _answers,
    _fitting,
    _story_answer,
    _story_message,
    _story_of,
    _story_others,
    _why_story,
    answers_request,
    fix_repeats,
    message_opening,
    write_others,
)
from .reading import (
    COMMON_QUESTIONS,
    Brief,
    _analyse,
    _job_text,
    _kept,
    _research,
    evidence_plan,
    field_of,
    passages,
    person,
    previous_applications,
    prompt,
    slug,
    written_out,
)

__all__ = [
    "CELL_WORDS",
    "CHECKED_CLEAN",
    "CUT_FLAG",
    "CUT_NOTE",
    "DANGLING_FLAG",
    "DRAFT_TEMPERATURE",
    "ECHO_FLAG",
    "EQUIVALENTS",
    "FIND_PREFIX",
    "LETTER_TRIES",
    "MISSING_GAP",
    "NOTHING_CHECKED",
    "NOTHING_TO_CHECK",
    "NO_ANSWER",
    "PREPARED",
    "REASONING",
    "REPEAT_FLAG",
    "REVISED",
    "SHORT_FLAG",
    "SHORT_MIDDLE",
    "SOFTENED",
    "STORY_TRIES",
    "THINKING",
    "Brief",
    "NothingChecked",
    "_answers",
    "_eligibility_flag",
    "_fit",
    "_fitting",
    "_item_note",
    "_job_text",
    "_kept",
    "_letter_bodies",
    "_short_middle",
    "_story_answer",
    "_story_letter",
    "_story_message",
    "_story_of",
    "_story_others",
    "_why_story",
    "add_company_check",
    "build",
    "check_drafts",
    "checked_fit_tools",
    "complete_fit",
    "consistent_fit",
    "covered_gaps",
    "drafts",
    "evidence_plan",
    "field_of",
    "fitting_names",
    "fix_claims",
    "fix_echoes",
    "fix_repeats",
    "has_facts",
    "hiring_contacts",
    "known_tools",
    "letter_frame",
    "message_opening",
    "message_to",
    "mirror_note",
    "motive",
    "paragraph_scope",
    "passages",
    "person",
    "planning",
    "prompt",
    "rank",
    "read_job",
    "read_letter_notes",
    "reputation",
    "revise_once",
    "row_source",
    "salutation",
    "shared_context",
    "slug",
    "spelt",
    "story_ranking",
    "unsupported_claims",
    "unsupported_numbers",
    "with_card_tools",
    "with_sources",
    "without_reasoning",
    "without_refusals",
    "write_letter",
    "write_others",
    "written_out",
]

log = logging.getLogger(__name__)


class NothingChecked(RuntimeError):
    """No card the person checked and no CV with entries: there is nothing true to write from."""


NOTHING_CHECKED = ("Hal has nothing of yours to write from yet, so it wrote nothing for this job. Open Your record "
                   "and press Check your record to confirm the facts Hal found, or add your CV on My CV, then try "
                   "again.")


def has_facts(con) -> bool:
    """True when a draft has something of the person's to tell: a card they checked, or a CV fact beyond the line
    under the name and the settings' status line. Hal's starter CV holds only "Graduate", which the writer took
    for a degree."""
    said = {cv.load().get("title", ""), cv.status_line()}
    own = [line for line in cv.facts_text().splitlines() if line.strip() and line not in said]
    return bool(own) or profile_writing.checked_any(con)


def build(con, job_id: str, use_web: bool = True, ctx=None) -> Path:
    """Drafts the pack for one job and returns the pack's Markdown path; the log says how many model calls it
    made (`ai.counted`), the measure of a pack's cost on a local model.

    With `ctx` (a running action) the record passages used are noted under its Activity "Used".
    """
    with ai.counted() as calls:
        pack_md = _build(con, job_id, use_web, ctx)
    log.info("pack for job %s: %d model calls", job_id, calls.n)
    return pack_md


def _build(con, job_id: str, use_web: bool, ctx) -> Path:
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    from .. import documents

    if not has_facts(con):
        raise NothingChecked(NOTHING_CHECKED)
    job_text = _job_text(job)
    facts = cv.facts_text()
    out_dir = documents.pack_dir(job)
    who = person(job)

    analysis = _analyse(job, who)
    web, urls = _research(job["company"]) if use_web else ("", [])
    about = ai.chat(prompt("brief", who),
                     f"Company: {job['company']}\n\nJob advert:\n{job_text[:4000]}\n\nWeb sources:\n{web[:8000]}",
                     role="checker", timeout=ai.LONG_TIMEOUT_S, purpose="brief")
    kept = _kept(analysis)
    fit = _fit(who, analysis, facts, kept, job)
    plan = evidence_plan(analysis, facts, kept, tuple(COMMON_QUESTIONS))
    brief = Brief(job, who, analysis, about, facts, kept)
    written = write_letter(con, job, who=who, analysis=analysis, about=about, kept=kept, fit=fit, plan=plan)
    letter = written["text"]
    context = _context(brief, fit, previous_applications(con, job))
    questions = [*(analysis.get("questions") or []), *COMMON_QUESTIONS]
    others = write_others(who, job, analysis, about, message_request=context,
                          answers_request=answers_request(brief, questions, context),
                          facts=facts, kept=kept, plan=plan, questions=questions,
                          pairing=written["pairing"], paired=True, told=tuple(written.get("told", ())),
                          ranking=written.get("ranking"))
    message, answers = others["message"], others["answers"]
    letter, message, answers, refused = without_refusals(letter, message, answers)

    variant, track = cv.tailor(job_text)
    pdf = cv.render(variant, out_dir)
    flagged = refused + written["flags"] + others["flags"]
    # The type's special cases: a KTP advert's employer is the host company, not the scheme.
    ktp = "ktp" in who["special_cases"] and job["company"] == "KTP"
    employer = (analysis.get("employer") or job["company"]) if ktp else job["company"]
    company_flags, company_md = reputation.check(employer, reputation.advert_of(job))
    flagged += company_flags
    history = previous_applications(con, job)
    if history:
        flagged.insert(0, f"REAPPLICATION: you applied here before: {history}")
    plain = eligibility.blockers([*(analysis.get("must_have") or []), *(analysis.get("eligibility") or [])],
                                 eligibility.person(con), job_text)
    found = _eligibility_flag(job, who, fit, analysis.get("eligibility") or [])
    flagged[:0] = plain + ([found] if found else [])   # first, and never softened by a rewrite
    if ctx is not None:
        for label in written["used"]:
            ctx.used("record", label)

    md = _markdown(brief, urls, fit, letter, message, answers, pdf, track, flagged, written["notes"])
    md = md.replace("## Role\n", company_md + "\n## Role\n", 1)
    pack_md = out_dir / "pack.md"
    pack_md.write_text(md)
    events.publish(PREPARED, job_id=job_id, company=job["company"], title=job["title"], pack=str(pack_md))
    mirror_note(job, pack_md)
    if job["status"] in ("new", "shortlisted"):
        db.set_status(con, job_id, "ready", f"pack drafted in {out_dir.name}")
    con.commit()
    return pack_md

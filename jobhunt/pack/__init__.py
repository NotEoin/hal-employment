"""Application packs: everything needed to apply for one job, drafted locally.

A pack holds a company brief, a requirements-to-evidence map with honest gaps, a CV adjusted for the job
(`cvstudio.tailor.prepare`, kept with its change list), a cover letter, a short message for the person
hiring, and answers to common form questions. It is written to the job's folder (`documents.pack_dir`), and a
`docs.prepared` event says one is ready. With the Hal add-on on, a copy also goes into its Applications
folder (`addon.applications_mirror`). Nothing is sent or submitted.

This module builds the pack (`build`) and exports every name of its parts, so callers import `jobhunt.pack`:
- `reading`: the prompt values, the advert reading, the record passages and `Brief`.
- `fitmap`: the fit map and the eligibility flag.
- `drafting`: the one request each document is written from (`Given`) and a draft asked for again when cut off.
- `checks`: the one check of every draft and its one redraft (`settle`), and the refusals taken out.
- `hiring`: who the message and the letter go to.
- `letter`: the cover letter and its notes.
- `others`: the message to the person hiring and the form answers.
- `markdown`: the pack's Markdown file, its company check and its add-on copy.

Design notes:
- The prompts live in `ai/prompts/*.txt` and name no one: `cvstudio.context.build` fills them from the
  `you.*`, `goal.*` and `cvdocs.*` settings and the CV type. `prompt(id)` renders
  the text when it is called, so callers see what the person's edits and settings give.
- Each document is written in one call from one request code assembles (owner, 2026-11-19: "a single prompt
  assembled", about 5 minutes per application at most): the advert's reading, the employer note, the fit
  map, and the work `writing.plan.chosen` picks with no AI, with where each piece was done and the person's
  part (`drafting.given`). An evidence plan, a ranking, a motivation pairing and a call per paragraph took 24
  and 27 calls and about 5 minutes for two real adverts, and their letters all read alike (2026-11-19).
- One check, not one per paragraph: the letter, the message and the answers are written, then checked in one
  `verify` call together, each redrafted at most once with everything wrong with it stated (`checks.settle`),
  and what is left is listed under "Check before sending", never fixed in a loop. The letter alone (form
  time, `documents.draft_letter`, and `docs.rewrite_letter`) and the message and answers alone
  (`docs.change`) take the same path through `write_letter` and `write_others`.
- Every draft the AI writes is respelt to the person's spelling (`spelt`, `writing.spelling`) before it is
  checked, as dashes are taken out (`ai.undash`): a spelling has one right form, so it needs no revision.
- An offer to relocate to the town the person lives in is taken out of the letter, the message and the
  answers with no AI (`relocation.without_own_move`): a letter offered to relocate to the London office of a
  person living in London (2026-11-07).
- The pack's checklist names no command: every action is a button on the Documents page.
- A pack is written from a settled record (`freshness`): it waits while a record update is queued or running,
  and starts again when the record's revision moves before the job's CV or the pack is saved, as packs
  written behind a long record update read a record that changed under them (2026-11-18). The revision it
  was written from is kept (`documents.mark_written_from`), so a later change shows it out of date.
- A pack is written on one of the person's CVs, the one `cvstudio/choose.py` picks for the advert (or the one
  the person picked for this application, which a rewrite keeps): the facts, the fit map, the letter and the
  job's CV all read it (`cv.based_on`), and the choice is kept with the job's CV (`tailor.prepare`). When the
  person changes the CV while the pack is written, the job's CV is built on theirs, and the letter stays, as
  Change CV keeps it.
- Each pack logs how many model calls it made (`build`, `ai.counted`): analyse, brief, fit, the letter, the
  message, the answers and one check, seven with no redraft (2026-11-19).
"""
import logging
from pathlib import Path

from jobhunt import ai, db
from jobhunt.company import reputation
from jobhunt.core import events
from jobhunt.cvstudio import choose, cv, tailor
from jobhunt.pack import freshness
from jobhunt.pack.checks import (
    CUT_FLAG,
    DANGLING_FLAG,
    ECHO_FLAG,
    Draft,
    check_drafts,
    settle,
    unsupported_claims,
    unsupported_numbers,
    without_refusals,
)
from jobhunt.pack.drafting import CUT_NOTE, DRAFT_TEMPERATURE, Given, given, spelt
from jobhunt.pack.fitmap import (
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
from jobhunt.pack.hiring import FIND_PREFIX, hiring_contacts, message_to, salutation
from jobhunt.pack.letter import CHECKED_CLEAN, REVISED, SHORT_FLAG, SOFTENED, check_all, write_letter
from jobhunt.pack.letter import drafted as drafted_letter
from jobhunt.pack.letter import finished as finished_letter
from jobhunt.pack.markdown import (
    NOTHING_TO_CHECK,
    PREPARED,
    _markdown,
    add_company_check,
    mirror_note,
    read_letter_notes,
)
from jobhunt.pack.others import (
    NO_ANSWER,
    REPEAT_FLAG,
    drafted_answers,
    drafted_message,
    message_opening,
    write_others,
)
from jobhunt.pack.others import finished as finished_others
from jobhunt.pack.reading import (
    COMMON_QUESTIONS,
    Brief,
    _analyse,
    _job_text,
    _kept,
    _research,
    field_of,
    passages,
    person,
    previous_applications,
    prompt,
    slug,
    written_out,
)
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import drafts, eligibility

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
    "MISSING_GAP",
    "NOTHING_CHECKED",
    "NOTHING_TO_CHECK",
    "NO_ANSWER",
    "PREPARED",
    "REASONING",
    "REPEAT_FLAG",
    "REVISED",
    "SHORT_FLAG",
    "SOFTENED",
    "THINKING",
    "Brief",
    "Draft",
    "Given",
    "NothingChecked",
    "_eligibility_flag",
    "_fit",
    "_job_text",
    "_kept",
    "add_company_check",
    "build",
    "check_drafts",
    "checked_fit_tools",
    "complete_fit",
    "consistent_fit",
    "covered_gaps",
    "drafts",
    "field_of",
    "given",
    "has_facts",
    "hiring_contacts",
    "known_tools",
    "message_opening",
    "message_to",
    "mirror_note",
    "passages",
    "person",
    "prompt",
    "read_job",
    "read_letter_notes",
    "reputation",
    "row_source",
    "salutation",
    "settle",
    "slug",
    "spelt",
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


AFTER_WAIT_WORDS = "Writing your documents from your updated record"
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

    It is written from a settled record (`freshness.written`): it waits for a record update in hand, and
    starts again when the record changes while it writes. With `ctx` (a running action) the wait is a step
    that Stop ends, and the record passages used are noted under its Activity "Used".
    """
    with ai.counted() as calls:
        pack_md, _ = freshness.written(lambda fresh: _build(con, job_id, use_web, ctx, fresh), ctx,
                                       then=AFTER_WAIT_WORDS)
    log.info("pack for job %s: %d model calls", job_id, calls.n)
    return pack_md


def _build(con, job_id: str, use_web: bool, ctx, fresh: freshness.Fresh) -> Path:
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    # The choice carries what was stored when it was made (`Choice.seen`), so a Change CV the person presses
    # while this pack is written stands: `tailor.prepare` builds the job's CV on it (2026-11-19). No lock is
    # held for the pack's AI calls, so pages that read the application never wait for it.
    base = choose.for_writing(con, dict(job), again=True)
    with cv.based_on(base.doc_id):
        return _build_on(con, job, base, use_web, ctx, fresh)


def _written(con, brief: Brief, fit: dict, questions: list[str]) -> tuple[dict, dict]:
    """The letter, the message and the answers, each written in one call from one request (`drafting.given`)
    and checked together in one call (`checks.settle`): (the letter as `write_letter` returns it, the message
    and answers as `write_others` does)."""
    facts = given(brief, fit, previous_applications(con, brief.job))
    letter_cut: set[str] = set()
    message_cut: set[str] = set()
    letter = drafted_letter(brief, facts, trimmed=letter_cut)
    message = drafted_message(brief, facts, trimmed=message_cut)
    answers = drafted_answers(brief, facts, questions)
    check_all(brief, facts, [letter, message, answers])
    return (finished_letter(con, brief, facts, letter, fit, trimmed=letter_cut),
            finished_others(brief, facts, message, answers, trimmed=message_cut))


def _build_on(con, job, base: choose.Choice, use_web: bool, ctx, fresh: freshness.Fresh) -> Path:
    """`_build` on the CV chosen for the job (`base`), which every fact below is read from (`cv.based_on`)."""
    from jobhunt import documents

    job_id = job["id"]
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
    brief = Brief(job, who, analysis, about, facts, kept)
    questions = [*(analysis.get("questions") or []), *COMMON_QUESTIONS]
    written, others = _written(con, brief, fit, questions)
    fresh.check()
    message, answers = others["message"], others["answers"]
    letter, message, answers, refused = without_refusals(written["text"], message, answers)

    fresh.check()    # the job's CV is saved next, so a record that moved is caught before anything is kept
    made = tailor.prepare(dict(job), priorities=tailor.picked(job_id, con), base=base, con=con)
    track = next((c["after"] for c in made["changes"] if c["target"] == "headline"), None)
    pdf = cv.render(tailor.v1(job_id, con), out_dir)
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
    fresh.check()
    pack_md = out_dir / "pack.md"
    pack_md.write_text(md)
    documents.mark_written_from(con, job_id, fresh.revision)
    events.publish(PREPARED, job_id=job_id, company=job["company"], title=job["title"], pack=str(pack_md))
    mirror_note(job, pack_md)
    if job["status"] in ("new", "shortlisted"):
        db.set_status(con, job_id, "ready", f"pack drafted in {out_dir.name}")
    con.commit()
    return pack_md

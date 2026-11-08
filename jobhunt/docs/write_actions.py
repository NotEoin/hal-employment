"""The documents area's writing work: changes asked for in the person's words, interview notes, contacts.

Kinds: `docs.change` rewrites the letter, message and answers as the person asked;
`docs.rewrite_letter` rewrites the letter alone; `docs.prep` drafts notes for the next stage; `docs.contacts`
finds who to message; `docs.critique` reads the CV and letter as a recruiter would (`writing.critique`) and
changes nothing. Each is slow AI or search work, so it runs as an action with steps, Stop and an
Activity row, and the route only starts it (`docs/write_routes.py`).

Design decisions, each with its reason:

- A rewritten letter takes the pack's letter path (`pack.write_letter`), so it is checked like the first
  draft, and its record passages are noted under the Activity row's "Used".
- A changed message and changed answers go through the pack's own path (`pack.write_others`), with the
  same advert reading, record passages and fit map as the letter, so a change is checked for claims,
  project contexts, a cut-off reply and restating the advert, and the message keeps its opening: the
  change used to save the AI's words with none of these checks (2026-11-07).
- Results are saved through `apply.actions.save_documents`, the rule every edit follows: a form already
  filled with the old documents is marked out of date.
- G9 runs every kind from its example without an AI or a search engine: the examples stand a recording
  stand-in in for the slow call for the life of the made-up world; the kind's own steps and row are real.
"""
from __future__ import annotations

from typing import Any

from .. import contacts, cv, documents, pack, prep
from ..apply import actions as apply_actions
from ..apply.queries import _job
from ..core import runner
from ..writing import critique as writing_critique
from ..writing import fields

WORDS_SHOWN = 200   # characters of the person's request kept in the action's parameters and its why


def _letter_example(world: Any) -> dict:
    row = world.con.execute("SELECT id FROM jobs WHERE status = 'ready' ORDER BY id LIMIT 1").fetchone()
    return {"job_id": row["id"], "_subject": row["id"], "words": "Make the second paragraph shorter"}


def _stand_in_example(owner: Any, name: str, reply: Any):
    def example(world: Any) -> dict:
        from unittest import mock

        world._stack.enter_context(mock.patch.object(owner, name, lambda *a, **k: reply))
        row = world.con.execute("SELECT id FROM jobs WHERE status = 'ready' ORDER BY id LIMIT 1").fetchone()
        return {"job_id": row["id"], "_subject": row["id"]}
    return example


def _used(ctx, written: dict) -> None:
    for label in written["used"]:
        ctx.used("record", label)


def rewrite_letter_now(ctx, job, words: str, **read) -> dict:
    """Rewrites one job's letter as asked, checks it and saves it; returns what was written. `read` holds the
    advert reading, record passages, fit map and brief (`pack.write_letter`'s) when the caller has them."""
    ctx.step("Reading the advert and your evidence")
    written = pack.write_letter(ctx.con, job, request=words, **read)
    _used(ctx, written)
    ctx.step("Checking every fact and number")
    docs = documents.load(ctx.con, job)
    documents.save(ctx.con, job, {**docs, "letter_notes": written["notes"], "letter_flags": written["flags"]})
    apply_actions.save_documents(ctx.con, job["id"], {"letter": written["text"]})
    return written


def _changed_others(ctx, job, who: dict, docs: dict, words: str, read: dict, about: str, facts: str,
                    letter: str = "") -> dict:
    """The message and answers rewritten as the person asked and checked as the pack checks them
    (`pack.write_others`, against the new `letter` too): {message, answers, flags}. Each answer keeps its
    question, and one the AI left out keeps its old words."""
    context = pack.shared_context(ctx.con, job, who, read, about, facts)
    asked = f"The applicant asked for this change: {words}"
    given = docs.get("answers") or []
    listing = "\n".join(f"- {a.get('question', '')}\n  As it stands: {a.get('answer', '')}" for a in given)
    conventions = fields.block(pack.field_of(job, who, read["analysis"]), "answer")
    ctx.step("Changing the message and the answers, and checking them")
    out = pack.write_others(
        who, job, read["analysis"], about, facts=facts, kept=read["kept"],
        message_request=f"{context}\n\nThe message as it stands:\n{docs.get('message') or ''}\n\n{asked}",
        answers_request=f"Questions:\n{listing}\n\n{context}\n\n{conventions}\n\n{asked}" if given else "",
        letter=letter)
    by_question = {a.get("question", ""): a.get("answer", "") for a in out["answers"]}
    answers = [{"question": a.get("question", ""),
                "answer": by_question.get(a.get("question", "")) or a.get("answer", "")} for a in given]
    return {**out, "answers": answers}


@runner.action("docs.change", area="documents", label="Changing your documents for {title} at {company}",
               why="You asked: {words}", priority=1, example=_letter_example)
def change(ctx, job_id: str, words: str) -> dict:
    """Rewrites the letter, the message and the answers in the way the person asked, each checked as the pack's
    drafts are; nothing is sent."""
    words = (words or "").strip()[:WORDS_SHOWN]
    if not words:
        raise ValueError("Say what you want changed first.")
    job = _job(ctx.con, job_id)
    who, facts = pack.person(job), cv.facts_text()
    ctx.step("Reading the advert and your evidence")
    read, about = pack.read_job(job, who, facts), documents.summary(job).get("company", "")
    written = rewrite_letter_now(ctx, job, words, who=who, about=about, **read)
    docs = documents.load(ctx.con, job)
    others = _changed_others(ctx, job, who, docs, words, read, about, facts, written["text"])
    flags = list(dict.fromkeys(written["flags"] + others["flags"]))
    out = apply_actions.save_documents(ctx.con, job_id, {"message": others["message"], "answers": others["answers"]})
    ctx.changed("Hal rewrote the letter, the message and the answers as you asked. Read them before you approve.")
    return {"letter": written["text"], "flags": flags, **out}


@runner.action("docs.rewrite_letter", area="documents", label="Rewriting the letter for {title} at {company}",
               why="You asked: {words}", priority=1, example=_letter_example)
def rewrite_letter(ctx, job_id: str, words: str = "") -> dict:
    """Rewrites the cover letter alone, in the way the person asked, and checks it like any other."""
    job = _job(ctx.con, job_id)
    written = rewrite_letter_now(ctx, job, (words or "").strip()[:WORDS_SHOWN])
    ctx.changed("Hal rewrote the cover letter. Read it before you approve.")
    return {"letter": written["text"], "flags": written["flags"], "notes": written["notes"]}


def critique_subject(job_id: str) -> str:
    """The subject of a critique: its own, so asking for one never stops Hal writing or filling for the job."""
    return f"{job_id}:critique"


def _critique_example(world: Any) -> dict:
    row = world.con.execute("SELECT id FROM jobs WHERE status = 'ready' ORDER BY id LIMIT 1").fetchone()
    return {"job_id": row["id"], "_subject": critique_subject(row["id"])}


@runner.action("docs.critique", area="documents", label="Reading your documents as a recruiter",
               why="You asked for a recruiter's read of your documents", priority=0,
               example=_critique_example)
def critique(ctx, job_id: str) -> dict:
    """Grades the CV and letter against the advert, with line comments and the top three fixes; changes nothing."""
    job = _job(ctx.con, job_id)
    ctx.step("Reading your CV and letter against the advert, as a recruiter would")
    ctx.used("application", "Your CV and cover letter for this job", job_id)
    out = writing_critique.critique(ctx.con, job)
    ctx.step(out["note"] or "Hal's read is ready. It is Hal's opinion; nothing was changed.")
    return out


@runner.action("docs.prep", area="documents", label="Getting you ready for {title} at {company}",
               why="An interview is booked", priority=1,
               example=_stand_in_example(prep, "build", "## What they will test\n\nA made-up note."))
def prepare_for_interview(ctx, job_id: str) -> dict:
    """Drafts notes for the next stage of one application: what they will test, questions, stories."""
    job = _job(ctx.con, job_id)
    ctx.step("Reading the advert, your fit map and your record")
    notes = prep.build(ctx.con, job)
    ctx.changed("Hal wrote notes to get you ready for the next stage.")
    return {"prep_md": notes}


@runner.action("docs.contacts", area="documents", label="Finding who to message about {title} at {company}",
               why="You asked who to message", priority=1,
               example=_stand_in_example(contacts, "find", {"people": [], "by": "rules", "note": ""}))
def find_contacts(ctx, job_id: str) -> dict:
    """Finds people worth messaging about one application; Hal sends nothing."""
    job = _job(ctx.con, job_id)
    ctx.step("Looking for the people who hire for this role")
    found = contacts.find(ctx.con, job)
    count = len(found["people"])
    ctx.changed(f"Hal suggested {count} {'person' if count == 1 else 'people'} to message." if count
                else "Hal found nobody to suggest.")
    return found

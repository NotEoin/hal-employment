"""The documents area's writing work: changes asked for in the person's words, interview notes, contacts.

Kinds: `docs.change` rewrites the letter, message and answers as the person asked;
`docs.rewrite_letter` rewrites the letter alone; `docs.prep` drafts notes for the next stage; `docs.contacts`
finds who to message. Each is slow AI or search work, so it runs as an action with steps, Stop and an
Activity row, and the route only starts it (`docs/write_routes.py`).

Design decisions, each with its reason:

- A rewritten letter takes the pack's letter path (`pack.write_letter`), so it is checked like the first
  draft, and its record passages are noted under the Activity row's "Used".
- Results are saved through `apply.actions.save_documents`, the rule every edit follows: a form already
  filled with the old documents is marked out of date.
- G9 runs every kind from its example without an AI or a search engine: the examples stand a recording
  stand-in in for the slow call for the life of the made-up world; the kind's own steps and row are real.
"""
from __future__ import annotations

from typing import Any

from .. import contacts, documents, llm, pack, prep
from ..apply import actions as apply_actions
from ..apply.queries import _job
from ..core import runner
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


def rewrite_letter_now(ctx, job, words: str) -> dict:
    """Rewrites one job's letter as asked, checks it and saves it; returns what was written."""
    ctx.step("Reading the advert and your evidence")
    written = pack.write_letter(ctx.con, job, request=words)
    _used(ctx, written)
    ctx.step("Checking every fact and number")
    docs = documents.load(ctx.con, job)
    documents.save(ctx.con, job, {**docs, "letter_notes": written["notes"], "letter_flags": written["flags"]})
    apply_actions.save_documents(ctx.con, job["id"], {"letter": written["text"]})
    return written


def _message(job, who: dict, docs: dict, words: str) -> str:
    request = (f"Job: {job['title']} at {job['company']}\n\nThe message as it stands:\n{docs.get('message') or ''}"
               f"\n\nThe applicant asked for this change: {words}")
    return pack.drop_notes(llm.undash(llm.chat(pack.prompt("message", who), request, role="writer").strip()))


def _answers(job, who: dict, docs: dict, words: str) -> list[dict]:
    given = docs.get("answers") or []
    if not given:
        return []
    listing = "\n".join(f"- {a.get('question', '')}\n  As it stands: {a.get('answer', '')}" for a in given)
    request = (f"Job: {job['title']} at {job['company']}\n\nQuestions:\n{listing}"
               f"\n\n{fields.block(fields.for_job(job, who), 'answer')}"
               f"\n\nThe applicant asked for this change: {words}")
    reply = llm.chat_json(pack.prompt("answers", who), request, role="writer").get("answers") or []
    by_question = {a.get("question", ""): llm.undash(a.get("answer", "")) for a in reply if isinstance(a, dict)}
    return [{"question": a.get("question", ""), "answer": by_question.get(a.get("question", "")) or a.get("answer", "")}
            for a in given]


@runner.action("docs.change", area="documents", label="Changing your documents for {title} at {company}",
               why="You asked: {words}", priority=1, example=_letter_example)
def change(ctx, job_id: str, words: str) -> dict:
    """Rewrites the letter, the message and the answers in the way the person asked; nothing is sent."""
    words = (words or "").strip()[:WORDS_SHOWN]
    if not words:
        raise ValueError("Say what you want changed first.")
    job = _job(ctx.con, job_id)
    written = rewrite_letter_now(ctx, job, words)
    who = pack.person(job)
    docs = documents.load(ctx.con, job)
    ctx.step("Changing the message")
    message = _message(job, who, docs, words)
    ctx.step("Changing the answers")
    answers = _answers(job, who, docs, words)
    out = apply_actions.save_documents(ctx.con, job_id, {"message": message, "answers": answers})
    ctx.changed("Hal rewrote the letter, the message and the answers as you asked. Read them before you approve.")
    return {"letter": written["text"], "flags": written["flags"], **out}


@runner.action("docs.rewrite_letter", area="documents", label="Rewriting the letter for {title} at {company}",
               why="You asked: {words}", priority=1, example=_letter_example)
def rewrite_letter(ctx, job_id: str, words: str = "") -> dict:
    """Rewrites the cover letter alone, in the way the person asked, and checks it like any other."""
    job = _job(ctx.con, job_id)
    written = rewrite_letter_now(ctx, job, (words or "").strip()[:WORDS_SHOWN])
    ctx.changed("Hal rewrote the cover letter. Read it before you approve.")
    return {"letter": written["text"], "flags": written["flags"], "notes": written["notes"]}


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

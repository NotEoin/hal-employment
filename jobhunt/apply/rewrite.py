"""Write the documents again: which applications may be rewritten, and what of the person's own work a
rewrite keeps.

The rewrite itself is `docs.prepare` with `again` (`apply/actions.py`): it builds the pack from the record and
the CV as they are now (`pack.build`, which stamps the record revision, `documents.mark_written_from`, and makes
the job's CV again from the CV in use, `tailor.prepare`, which keeps `job_cvs.base_version_id`), then saves the
new drafts with the person's own edits put back (`kept_edits`, `merged`) and makes the PDFs. An application
written before CVs were kept per job gets its `job_cvs` row there, so it moves to the line-by-line CV editor.

Design decisions, each with its reason:

- The person's own work is what differs from the pack it was drafted from: a letter, message or answer they
  edited, or one Hal rewrote because they asked (`docs.change`). Nothing marks an edit as the person's when
  it is saved, so the pack is the one honest reference. A letter Hal drafted when a form asked for one
  (`documents.draft_letter`, with `letter_notes`) while the pack had none is Hal's, not the person's.
- Kept by default, and the request says when to write everything fresh. CV edits the person made (`by` "you")
  are kept by `tailor.prepare`; Hal's changes are made again, so a change of Hal's the person undid does not
  carry over to the new text, and an older application's `undone_changes` are dropped.
- Approved and sent applications are never rewritten (`refusal`): the employer may already have them, and an
  approval fixed what the form is filled with. A form fill in progress for the job is refused too, as
  `actions.start` replaces the action holding the job and would stop the fill without a word.
"""
from __future__ import annotations

import sqlite3

from jobhunt import documents
from jobhunt.core import runner

# Why a rewrite is refused, in the words the page shows.
APPROVED = ("You approved these documents, so Hal leaves them as they are. Press \"Change the documents\" to "
            "reopen them first, then write them again.")
SENT = "This application is sent, so Hal keeps its documents as they went to the employer."
CLOSED = "This application is closed. Put it back in Matches before Hal writes for it again."
FILLING = ("Hal is filling in the form for this job. Stop it first, then write the documents again, or Hal "
           "would stop the form part way through.")
WRITABLE = ("new", "shortlisted", "ready", "applying")   # statuses Hal still writes documents for
SENT_STATUSES = ("applied", "assessment", "interview", "offer")


class NotNow(ValueError):
    """The application's documents cannot be written again now; the message says why in plain words."""


def refusal(con: sqlite3.Connection, job_id: str, *, in_action: bool = False) -> str | None:
    """Why the job's documents may not be written again now, or None when they may.

    `in_action` from the rewrite itself, which holds the job, so the check for a fill in progress is left out.
    """
    job = con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise LookupError(f"no job {job_id}")
    if job["status"] in SENT_STATUSES:
        return SENT
    if job["status"] not in WRITABLE:
        return CLOSED
    app = con.execute("SELECT approved_at, sent_at FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if app is not None and app["sent_at"]:
        return SENT
    if app is not None and app["approved_at"]:
        return APPROVED
    if not in_action:
        held = runner.running_for(job_id)
        if held and str(held.get("kind", "")).startswith("apply."):
            return FILLING
    return None


def check(con: sqlite3.Connection, job_id: str) -> None:
    """Raises `NotNow` with the reason when the job's documents may not be written again now."""
    why = refusal(con, job_id)
    if why:
        raise NotNow(why)


# ------------------------------------------------------------------------------ the person's own edits

def kept_edits(docs: dict, drafted: dict) -> dict:
    """The person's own work in `docs`, measured against the pack's `drafted` documents (`documents.drafts`):
    {letter, letter_notes, letter_flags, message, answers: {question: answer}}, each only when there is one."""
    out: dict = {}
    if _changed(docs, drafted, "letter") and not (not _text(drafted, "letter") and docs.get("letter_notes")):
        out.update({key: docs[key] for key in ("letter", "letter_notes", "letter_flags") if docs.get(key)})
    if _changed(docs, drafted, "message"):
        out["message"] = docs["message"]
    first = {a.get("question", ""): _text(a, "answer") for a in drafted.get("answers") or []}
    answers = {a.get("question", ""): a["answer"] for a in docs.get("answers") or []
               if _text(a, "answer") and _text(a, "answer") != first.get(a.get("question", ""))}
    if answers:
        out["answers"] = answers
    return out


def _text(d: dict, key: str) -> str:
    return (d.get(key) or "").strip()


def _changed(docs: dict, drafted: dict, key: str) -> bool:
    """True when `docs` has words for `key` that are not the pack's."""
    return bool(_text(docs, key)) and _text(docs, key) != _text(drafted, key)


def edits_now(con: sqlite3.Connection, job) -> dict:
    """The person's own work on the job's letter, message and answers as they stand (`kept_edits`)."""
    return kept_edits(documents.load(con, job), documents.drafts(job))


def has_own_edits(con: sqlite3.Connection, job) -> bool:
    """True when the job's documents hold work of the person's own that a fresh rewrite would replace."""
    from jobhunt.cvstudio import tailor

    if edits_now(con, job):
        return True
    return tailor.kept(job["id"], con) and tailor.shown(job["id"], con)["edits"] > 0


def merged(drafted: dict, kept: dict) -> dict:
    """The new drafts with the person's kept work put back: their letter and message, and their answer to each
    question; an answer to a question the new drafts no longer ask is kept after the others."""
    out = {k: v for k, v in drafted.items() if k != "approved"}
    for key in ("letter", "letter_notes", "letter_flags", "message"):
        if key in kept:
            out[key] = kept[key]
    own = dict(kept.get("answers") or {})
    answers = [{**a, "answer": own.pop(a.get("question", ""), a.get("answer", ""))} for a in out.get("answers") or []]
    out["answers"] = answers + [{"question": q, "answer": a} for q, a in own.items()]
    return out


def save_again(con: sqlite3.Connection, job, before: dict, *, keep_edits: bool) -> dict:
    """Saves the documents the pack just drafted, with the person's work from `before` (`edits_now`, read
    before the pack was written) put back when `keep_edits`, and makes their PDFs; returns the documents.

    Writing everything fresh also takes the person's edits out of the job's CV (`tailor.drop_your_edits`). A
    form already filled with the old documents is marked out of date, as an edit marks it
    (`actions.save_documents`), and the Home note that an older job's folder needs the documents made again
    is cleared.
    """
    from jobhunt.apply import queries
    from jobhunt.cvstudio import tailor

    docs = merged(documents.drafts(job), before if keep_edits else {})
    if not keep_edits and tailor.kept(job["id"], con):
        tailor.drop_your_edits(job["id"], con)
    documents.save(con, job, docs)
    form = queries.load_app(con, job["id"])["form"]
    if form and form.get("filled"):
        queries.set_app(con, job["id"], form={**form, "stale": True})
    con.execute("UPDATE applications SET docs_need_redraft = 0 WHERE job_id = ?", (job["id"],))
    con.commit()
    return docs


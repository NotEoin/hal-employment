"""One approval flow: the applying action kinds, and the workflow rules the screens rely on.

The kinds (`docs.prepare`, `apply.fill`, `apply.apply_edits`, `apply.next_page`, `apply.draft_rest`,
`apply.adopt`, `apply.match_tab`) are persisted actions over the form driver, so each has its steps, its
Activity row and Stop. The workflow rules are plain functions here, called by the routes: approving the
documents starts the form, editing them marks a filled form out of date, and a discard keeps its reason
apart from the others.

Hal never presses Submit: none of this does more than the driver, which hands the last page over.

Design decisions, each with its reason:

- An application's `mode`, `session_id` and `helper_id` are fixed when its documents are approved,
  so an application started from the laptop keeps using the laptop's browser.
- G9 runs every kind from its example without a browser, so the examples stand a recording stand-in in
  for the driver call for the life of the made-up world; the kind's own steps and Activity row are real.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import browser, db, documents
from ..core import runner, settings
from . import driver
from .queries import _app, _job, _set_app, _site_of, column_of, mark_applied

log = logging.getLogger(__name__)

# Discard reasons (migration 0009 `closed_reason`), with the dialog's words.
DISCARD_REASONS = {
    "advert_closed": "The advert has closed or the page is missing",
    "too_much_effort": "Too much effort to apply",
    "not_interested": "Not interested any more",
    "problem": "I found a problem with the job",
    "skipped_for_now": "Discarded",
}
STATUS_REASONS = {"rejected": "rejected", "withdrawn": "withdrawn", "closed": "advert_closed"}
CHANGEABLE = ("applying", "applied", "assessment", "interview", "offer", "rejected", "withdrawn")
ADOPT_WHY = "You handed Hal a tab you opened yourself"


# ------------------------------------------------------------------------------------------- action kinds

def _stand_in(world: Any, owner: Any, name: str) -> None:
    """For gate G9: replaces one slow call with a stand-in that takes a step, for the world's life."""
    from unittest import mock

    def stand_in(con, job_id: str, *args, **kw) -> str:
        driver._say(job_id, "the made-up form was filled")
        return "done"

    world._stack.enter_context(mock.patch.object(owner, name, stand_in))


def _example(name: str | None, owner: Any = driver, **extra: Any):
    def example(world: Any) -> dict:
        row = world.con.execute("SELECT id FROM jobs WHERE status = 'ready' ORDER BY id LIMIT 1").fetchone()
        if name:
            _stand_in(world, owner, name)
        return {"job_id": row["id"], "_subject": row["id"], **extra}
    return example


def _prepare_example(world: Any) -> dict:
    from .. import pack

    return _example("build", pack, time="10:00", open_check=False)(world)


@runner.action("docs.prepare", area="documents", label="Writing your documents for {title} at {company}",
               why="You pressed Apply with Hal at {time}. Documents come first.", priority=1,
               resumable=True, example=_prepare_example)
def prepare(ctx, job_id: str, time: str = "", open_check: bool = True) -> dict:
    """Moves a job to Getting ready and makes its documents; nothing is opened or sent.

    Resumable: a restart part way through starts it again by itself, since a queue of jobs left every one
    stalled under Getting ready after a restart (testers, 2026-11-04). Starting again only rewrites the drafts.
    """
    from .. import pack

    job = _job(ctx.con, job_id)
    if job["status"] in ("new", "shortlisted", "ready"):
        db.set_status(ctx.con, job_id, "applying", "chosen from the queue")
        ctx.con.commit()
    ctx.step("Reading the advert")
    if open_check and settings.get("cvdocs.check_open") and _advert_closed(ctx.con, job):
        ctx.step("The advert has closed, so Hal wrote nothing for it")
        return {"job_id": job_id, "closed": True}
    if not documents.summary(job)["has_pack"]:
        ctx.step("Writing your CV and cover letter for this job")
        pack.build(ctx.con, job_id, ctx=ctx)   # the record passages the letter used go under Activity "Used"
    # The pack drafts the letter's words only; without this the folder held no cover letter until the person
    # saved an edit or approved (testers found none, 2026-11-03), and the page had no letter to show.
    ctx.step("Making the PDFs of your CV and cover letter")
    made = documents.render(job, documents.load(ctx.con, job))
    ctx.step("Your documents are ready for you to check" if "letter" in made else
             "Hal drafted no cover letter for this job; write one on the Cover letter tab, or check that Hal's AI "
             "is working in Settings")
    return {"job_id": job_id}


def _advert_closed(con, job) -> bool:
    """Checks the advert is still open before Hal writes for it; a closed one closes the job.

    Queued adverts had often closed since they were found, and writing for them took minutes of the graphics
    card each (testers, 2026-11-04). One fetch, as the check before a job first shows in Matches; a page that
    cannot be read (some boards refuse plain fetches) counts as open, and the form step finds out later.
    """
    from ..core import clock
    from ..find import closed_check

    if closed_check.check(dict(job)) != "closed":
        return False
    db.set_status(con, job["id"], "closed", "the advert had closed before Hal wrote the documents")
    con.execute("UPDATE jobs SET open_state = 'closed', open_checked_at = ? WHERE id = ?", (clock.iso(), job["id"]))
    db.alert(con, "info", f"No longer open: {job['company']}", job["title"], job["id"])
    con.commit()
    return True


@runner.action("apply.fill", area="applying", label="Filling in the form for {title} at {company}",
               why="You approved the documents for {title}", priority=1, example=_example("act_auto"))
def fill(ctx, job_id: str, fresh: bool = False, here: bool = False) -> str:
    """Opens the employer's form and fills it, stopping where a person must check; never presses Submit.

    With `here`, Hal fills the page the job's tab shows from its first step, pressing nothing to get there:
    the person found the form when Hal could not.
    """
    ctx.step("Reading the page you are on" if here else "Opening the employer's form")
    return driver.act_auto(ctx.con, job_id, bool(here), bool(fresh))


@runner.action("apply.apply_edits", area="applying", label="Putting your changes in the form for {title}",
               why="You changed answers for {title}", priority=0, example=_example("act_refill", edits=[]))
def apply_edits(ctx, job_id: str, edits: list | None = None) -> str:
    """Types the person's changes into the form; nothing is pressed."""
    ctx.step("Putting your changes in the form")
    return driver.act_refill(ctx.con, job_id, list(edits or []))


@runner.action("apply.next_page", area="applying", label="Carrying on to the next page for {title}",
               why="You checked this page of the form for {title}", priority=0, example=_example("act_approve"))
def next_page(ctx, job_id: str) -> str:
    """Presses Next on a checked middle page; on the last page hands the form over instead."""
    ctx.step("Carrying on to the next page")
    return driver.act_approve(ctx.con, job_id, [])


@runner.action("apply.draft_rest", area="applying", label="Answering the rest of the form for {title}",
               why="You asked Hal to answer the rest", priority=0, example=_example("act_draft_rest"))
def draft_rest(ctx, job_id: str) -> str:
    """Drafts answers for the questions still empty."""
    ctx.step("Drafting answers for the empty questions")
    return driver.act_draft_rest(ctx.con, job_id)


@runner.action("apply.adopt", area="applying", label="Filling the tab you opened for {title}", why=ADOPT_WHY,
               priority=0, example=_example("act_adopt", url="https://jobs.example.org/apply/1"))
def adopt_tab(ctx, job_id: str, url: str = "") -> str:
    """Fills an application the person opened in a tab of their own."""
    ctx.step("Reading your tab")
    return driver.act_adopt(ctx.con, job_id, url)


def matched_tab(con, page: dict) -> dict:
    """The job a tab the person opened belongs to, made when no known job matches."""
    from .. import adopt

    job, made = adopt.adopt(con, page)
    return {"job": job["id"], "made": made, "company": job["company"], "title": job["title"]}


def _match_example(world: Any) -> dict:
    return {"page": {"url": "https://jobs.example.org/fernhill/apply", "title": "Apply: Graduate Software Engineer",
                     "text": "Fernhill Analytics. Graduate Software Engineer. Apply now."}}


@runner.action("apply.match_tab", area="applying", label="Finding the job your tab shows", why=ADOPT_WHY,
               priority=0, exclusive="none", example=_match_example)
def match_tab(ctx, page: dict) -> dict:
    """Matches a tab the person opened to a job, or adds the job."""
    ctx.step("Reading your tab")
    found = matched_tab(ctx.con, page)
    ctx.changed(f"{'Added' if found['made'] else 'Found'} {found['title']} at {found['company']}")
    return found


def start(kind: str, job_id: str, *, params: dict | None = None, why: str | None = None,
          session_id: str | None = None, helper_id: str | None = None, remote: bool = False,
          priority: int | None = None) -> str:
    """Starts one of the kinds above for a job, in place of anything Hal is doing for it; returns its id.

    `priority` 0 is for a button the person just pressed and is watching; it never waits behind queued jobs.
    """
    return runner.start(kind, job_id=job_id, params={"job_id": job_id, **(params or {})}, why=why, replace=True,
                        session_id=session_id, helper_id=helper_id, remote=remote, priority=priority)


# ----------------------------------------------------------------------------------------- workflow rules

def apply_with_hal(con, job_id: str, *, session_id: str | None = None) -> str:
    """Apply with Hal: the documents come first; the form opens only once they are approved."""
    from ..core import clock

    return start("docs.prepare", job_id, params={"time": clock.now_utc().strftime("%H:%M")},
                 session_id=session_id)


def _merge_edits(docs: dict, body: dict) -> dict:
    """The documents with the person's edits from an approve or save request."""
    out = dict(docs)
    if isinstance(body.get("letter"), str):
        out["letter"] = body["letter"]
    if isinstance(body.get("message"), str):
        out["message"] = body["message"]
    if isinstance(body.get("answers"), list):
        out["answers"] = [{"question": a.get("q", a.get("question", "")), "answer": a.get("a", a.get("answer", ""))}
                          for a in body["answers"] if isinstance(a, dict)]
    if isinstance(body.get("undone_changes"), list):
        out["undone_changes"] = [str(c) for c in body["undone_changes"]]
    return out


def approve_documents(con, job_id: str, body: dict | None = None, *, session_id: str | None = None,
                      helper_id: str | None = None, remote: bool = False, priority: int | None = None) -> str:
    """Approve and fill in the form: keeps the edits, fixes where the form is filled, moves the application to
    the Form step and starts `apply.fill`."""
    job = _job(con, job_id)
    if job["status"] in ("new", "shortlisted", "ready"):
        db.set_status(con, job_id, "applying", "chosen from the queue")
    now = db.now()
    docs = {**_merge_edits(documents.load(con, job), body or {}), "approved": now}
    documents.save(con, job, docs)
    # The Form step starts now, not when the page has loaded: the application page follows it at once.
    stage = {} if _app(con, job_id)["stage"] in ("form", "filled") else {"stage": "form"}
    _set_app(con, job_id, approved_at=now, mode=browser.mode(), session_id=session_id, helper_id=helper_id, **stage)
    db.log(con, job_id, "note", "documents approved")
    con.commit()
    if not settings.get("setup.first_app_seen"):
        settings.set("setup.first_app_seen", True, by="you", session_id=session_id)
    return start("apply.fill", job_id, why=f"You approved the documents for {job['title']}",
                 session_id=session_id, helper_id=helper_id, remote=remote, priority=priority)


def reopen_documents(con, job_id: str) -> None:
    """Takes an approved application back to its Documents step, to change the documents and approve them again.

    Hal stops filling. The employer's tab stays open, so approving again fills the form on show there with
    the changed documents.
    """
    job = _job(con, job_id)
    if job["status"] != "applying":
        raise ValueError("Only an application in progress can go back to its documents.")
    runner.stop_action(job_id)
    docs = documents.load(con, job)
    docs.pop("approved", None)
    documents.save(con, job, docs)
    _set_app(con, job_id, stage="documents", approved_at=None)
    db.log(con, job_id, "note", "documents reopened to change them")
    con.commit()


def save_documents(con, job_id: str, body: dict) -> dict:
    """Keeps edits to the documents; a form already filled holds the old ones, so it is marked out of date."""
    job = _job(con, job_id)
    documents.save(con, job, _merge_edits(documents.load(con, job), body))
    form = _app(con, job_id)["form"]
    stale = bool(form and form.get("filled"))
    if stale:
        form["stale"] = True
        _set_app(con, job_id, form=form)
    return {"ok": True, "form_out_of_date": stale}


def set_change(con, job_id: str, change_id: str, on: bool) -> list[str]:
    """Undo (`on` False) or Redo one per-job CV change; returns the changes now undone."""
    job = _job(con, job_id)
    docs = documents.load(con, job)
    undone = [c for c in docs.get("undone_changes") or [] if c != change_id]
    if not on:
        undone.append(change_id)
    documents.save(con, job, {**docs, "undone_changes": undone})
    return undone


def discard(con, job_id: str, reason: str = "skipped_for_now", words: str = "") -> None:
    """Discards an application or a job, keeping why apart from the other reasons.

    Hal stops at once and its tab closes when the browser is free; the documents stay. "Too much effort"
    is remembered against the site, and the same advert listed twice does not come back into the queue.
    "The same advert" is the repost rule's (`rules.repost_key`): employer, title and place, so discarding a
    role in one city leaves the same role in another (testers, 2026-11-04).
    """
    if reason not in DISCARD_REASONS:
        raise ValueError(f"Choose why: one of {', '.join(DISCARD_REASONS)}.")
    job = _job(con, job_id)
    stopped = runner.stop_action(job_id)
    started = job["status"] == "applying"
    if started or stopped:
        runner.POOL.submit(_close_quietly, job_id)
    where = "mid-application" if started else "from the queue"
    words = (words or "").strip()
    said = words or ("" if reason == "skipped_for_now" else DISCARD_REASONS[reason])
    db.set_status(con, job_id, "closed" if reason == "advert_closed" else "skipped",
                  f"discarded {where}{': ' + said if said else ''}")
    if reason == "too_much_effort":
        db.log(con, job_id, "effort", _site_of(con, job))
    if started and not stopped:
        # An application with no row yet (Getting ready) gets one, so Closed can list it with its reason.
        # After a stopped run nothing new is written: that run's work is dropped whole.
        con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                    (job_id, db.now()))
    con.execute("UPDATE applications SET closed_reason = ?, closed_detail = ?, updated = ? WHERE job_id = ?",
                (reason, words or None, db.now(), job_id))
    from ..find import rules

    key = rules.repost_key(job)
    for d in con.execute("""SELECT id, company, title, location FROM jobs WHERE id != ?
                            AND status IN ('new','shortlisted','ready') AND lower(company) = lower(?)""",
                         (job_id, job["company"])).fetchall():
        if rules.repost_key(d) == key:
            db.set_status(con, d["id"], "skipped", "duplicate of a discarded job")
    con.commit()


def _close_quietly(job_id: str) -> None:
    from .queries import _close_quietly as close

    close(job_id)


def restore(con, job_id: str) -> None:
    """Back to Matches: Hal stops, and the job waits in the queue again."""
    if runner.stop_action(job_id):
        runner.POOL.submit(_close_quietly, job_id)
    db.set_status(con, job_id, "ready", "returned to the queue")
    con.execute("UPDATE applications SET closed_reason = NULL, closed_detail = NULL WHERE job_id = ?", (job_id,))
    con.commit()


def sent_myself(con, job_id: str) -> str:
    """I sent it myself: works at any step; Hal stops and the application moves to Sent."""
    job = _job(con, job_id)
    runner.stop_action(job_id)
    mark_applied(con, job, "you said you sent it yourself", sent_how="you_said")
    return "sent"


def change_status(con, job_id: str, status: str, words: str = "") -> str | None:
    """Moves an application by hand (interview booked, rejected, ...); returns its board column."""
    if status not in CHANGEABLE:
        raise ValueError(f"An application can be moved to {', '.join(CHANGEABLE)}.")
    _job(con, job_id)
    db.set_status(con, job_id, status, (words or "").strip())
    if status in STATUS_REASONS:
        _set_app(con, job_id, closed_reason=STATUS_REASONS[status], closed_detail=(words or "").strip() or None)
    con.commit()
    return column_of(status, _app(con, job_id)["stage"])


def add_note(con, job_id: str, text: str) -> None:
    """Keeps a note of the person's own on the application."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Write the note first.")
    _job(con, job_id)
    db.log(con, job_id, "note", text)
    con.commit()

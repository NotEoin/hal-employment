"""What the person says about a job: Save, "Not for me" with a reason, and putting a job back.

Every verdict is a `job_feedback` row, so the reasons stay for learning even after the job is put back.
"Not for me" is a status, not a scoring rule: the job becomes `skipped` and shows under Hidden as "You chose:
<reason>", which survives every re-check, because scoring never reads or changes the status.

Saving keeps the job in Saved and, when the advert says when it closes, adds a to-do due two days before the
closing day; the to-do list is what the summary email, the voice bridge and Home already read, so the
reminder reaches the person wherever they look. Unsaving or hiding the job ticks the to-do off.

The undo handler for "You hid a job" lives here rather than in an `undo.py`, because this module is imported by
the jobs routes, which are loaded whenever the server that serves Undo runs.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from jobhunt import db
from jobhunt.core import activity, clock, events

log = logging.getLogger(__name__)

REMIND_DAYS = 2             # the page promises "Hal will remind you 2 days before it closes"
OPEN_STATUSES = ("new", "shortlisted", "ready")
# The dialog's reasons and the codes `job_feedback.reason` keeps.
REASONS = {"Too far": "too_far", "Pay too low": "pay_low", "Not my kind of work": "not_my_work",
           "Too senior": "too_senior", "Not this employer": "not_employer", "The advert has closed": "closed",
           "Too much effort to apply": "too_much_effort"}
REASON_WORDS = {code: words for words, code in REASONS.items()}
NO_REASON = "no reason given"


def _job(con: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("That job is not in Hal's list.")
    return row


def _feedback(con: sqlite3.Connection, job_id: str, verdict: str, reason: str | None = None,
              words: str | None = None) -> None:
    con.execute("INSERT INTO job_feedback (job_id, at, verdict, reason, words) VALUES (?, ?, ?, ?, ?)",
                (job_id, clock.iso(), verdict, reason, words))


def reason_code(reason: str | None) -> tuple[str | None, str | None]:
    """(code, own words) for a reason from the dialog: a listed reason gives its code, anything else `other`."""
    reason = (reason or "").strip()
    if not reason:
        return None, None
    if reason in REASONS:
        return REASONS[reason], None
    if reason in REASON_WORDS:
        return reason, None
    return "other", reason


def reason_words(code: str | None, words: str | None) -> str:
    """The reason as the person chose it, for "You chose: ..."."""
    if words:
        return words
    return REASON_WORDS.get(code or "", NO_REASON)


def latest(con: sqlite3.Connection, job_id: str) -> dict | None:
    """The newest "Not for me" verdict on a job: {reason, words, at}, or None."""
    row = con.execute("""SELECT reason, words, at FROM job_feedback WHERE job_id = ? AND verdict = 'no'
                         ORDER BY id DESC LIMIT 1""", (job_id,)).fetchone()
    return dict(row) if row else None


def reminder_day(closes_at: str | None) -> date | None:
    """The day the closing reminder is due, or None when the closing date is unknown."""
    if not closes_at:
        return None
    try:
        closes = date.fromisoformat(closes_at[:10])
    except ValueError:
        log.info("unreadable closing date %r", closes_at)
        return None
    return closes - timedelta(days=REMIND_DAYS)


def _todo_source(job_id: str) -> str:
    return f"saved:{job_id}"


def _tick_reminder(con: sqlite3.Connection, job_id: str) -> None:
    con.execute("UPDATE todos SET done = ? WHERE source = ? AND done IS NULL", (clock.iso(), _todo_source(job_id)))


def save(con: sqlite3.Connection, job_id: str, saved: bool, *, session_id: str | None = None) -> dict:
    """Saves or unsaves a job; a saved job with a closing date gets a reminder to-do. Commits."""
    job = _job(con, job_id)
    if saved:
        con.execute("UPDATE jobs SET saved_at = COALESCE(saved_at, ?) WHERE id = ?", (clock.iso(), job_id))
        _feedback(con, job_id, "save")
        due = reminder_day(job["closes_at"])
        if due is not None:
            # the unique source makes saving twice one to-do; a ticked one is reopened
            con.execute("DELETE FROM todos WHERE source = ? AND done IS NOT NULL", (_todo_source(job_id),))
            db.add_todo(con, "apply", f"Apply for {job['title']} at {job['company']}: it closes on "
                        f"{date.fromisoformat(job['closes_at'][:10]):%A %d %B}", job_id=job_id,
                        detail="You saved this job.", priority=2, due=due.isoformat(), source=_todo_source(job_id))
    else:
        con.execute("UPDATE jobs SET saved_at = NULL WHERE id = ?", (job_id,))
        _feedback(con, job_id, "unsave")
        _tick_reminder(con, job_id)
    con.commit()
    events.publish("job", id=job_id)
    due = reminder_day(job["closes_at"]) if saved else None
    return {"saved": saved, "remind_on": due.isoformat() if due else None}


def not_for_me(con: sqlite3.Connection, job_id: str, reason: str | None, words: str | None = None, *,
               session_id: str | None = None) -> dict:
    """Hides a job with the person's reason; Undo puts it back. Commits."""
    job = _job(con, job_id)
    code, own = reason_code(reason)
    own = (words or "").strip() or own
    if job["status"] not in OPEN_STATUSES and job["status"] != "skipped":
        raise ValueError("This job is already in Applications. Discard it there instead.")
    _feedback(con, job_id, "no", code, own)
    if job["status"] != "skipped":
        db.set_status(con, job_id, "skipped", f"not for me: {reason_words(code, own)}")
    _tick_reminder(con, job_id)
    con.commit()
    activity.record("find", "find.not_for_me", f"Hid {job['title']} at {job['company']}",
                    f"You chose: {reason_words(code, own)}", job_id=job_id, icon="eye",
                    undo={"type": "job_hidden", "job_id": job_id, "status": job["status"]}, session_id=session_id)
    events.publish("job", id=job_id)
    return {"hidden": True, "suggestion_id": _suggestion(con) if code == "too_far" else None}


def _suggestion(con: sqlite3.Connection) -> str | None:
    """The "Too far" suggestion waiting once this job is hidden, so the reply can say so."""
    from jobhunt.learn import patterns  # learning reads feedback; imported here so this module loads alone

    try:
        return patterns.after_feedback(con)
    except Exception:  # the job is already hidden and committed; a learning fault must not undo the reply
        log.exception("could not look for a suggestion after hiding a job")
        return None


def put_back(con: sqlite3.Connection, job_id: str, *, status: str = "new", session_id: str | None = None) -> dict:
    """Brings a job the person hid back to Matches. Commits."""
    job = _job(con, job_id)
    if job["status"] != "skipped":
        return {"ok": True}
    _feedback(con, job_id, "put_back")
    db.set_status(con, job_id, status if status in OPEN_STATUSES else "new", "put back")
    con.commit()
    events.publish("job", id=job_id)
    return {"ok": True}


@activity.undo_handler("job_hidden")
def _undo_hidden(con: sqlite3.Connection, spec: dict, row: dict) -> str:
    put_back(con, spec["job_id"], status=spec.get("status") or "new")
    return "Put the job back in your matches"

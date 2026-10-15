"""Turns recruiting email and application progress into to-do items and alerts.

Reads the emails table that `jobhunt mail` fills. Each email needing action
(assessment, interview, offer, a person writing) becomes one to-do, ranked
above everything else, and one alert. Idempotent: run it as often as needed.

When the applicant's private answers (data/private.toml) ask for adjustments, an assessment
invitation also brings a to-do to ask for them before starting: timed tests rarely allow
extra time once begun.
"""
from . import db
from .config import private
from .gmail import ADDRESS

# How far back a first run looks; older invitations have long expired.
LOOKBACK = "-21 days"

ACTIONS = {
    # kind: (priority, title template, alert level)
    "offer": (0, "Respond to the offer from {company}", "urgent"),
    "interview": (1, "Book the interview with {company}", "urgent"),
    "assessment": (1, "Complete the {company} assessment", "urgent"),
    "human": (2, "Reply to {company}", "info"),
}


def thread_url(thrid: str | None) -> str | None:
    """Returns a Gmail link to the thread, in the job inbox's account."""
    if not thrid:
        return None
    return f"https://mail.google.com/mail/?authuser={ADDRESS}#all/{int(thrid):x}"


def sync(con) -> list[int]:
    """Creates to-dos and alerts for new emails needing action; returns the new to-do ids."""
    rows = con.execute(
        f"""SELECT e.*, j.title job_title, j.company job_company FROM emails e
            LEFT JOIN jobs j ON j.id = e.job_id
            WHERE e.kind IN ({','.join('?' * len(ACTIONS))}) AND e.date >= datetime('now', ?)
            ORDER BY e.date""", (*ACTIONS, LOOKBACK)).fetchall()
    made = []
    for e in rows:
        priority, template, level = ACTIONS[e["kind"]]
        company = e["job_company"] or e["company"] or e["sender"] or "the employer"
        title = template.format(company=company)
        todo = db.add_todo(con, e["kind"], title, job_id=e["job_id"], detail=e["subject"] or "",
                           url=thread_url(e["thrid"]), priority=priority, source=f"email:{e['msgid']}")
        if todo:
            made.append(todo)
            role = e["job_title"] or e["role"] or ""
            db.alert(con, level, title, f"{role}\n{e['subject'] or ''}".strip(), e["job_id"])
        if e["kind"] == "assessment" and private().get("adjustments"):
            asked = db.add_todo(con, "assessment", f"Ask {company} for extra time before starting the test",
                                job_id=e["job_id"], url=thread_url(e["thrid"]), priority=0,
                                detail="Reply to the invitation, or use its adjustments link, before you begin. "
                                       "What to ask for is in your private answers (data/private.toml).",
                                source=f"adjust:{e['msgid']}")
            made += [asked] if asked else []
    con.commit()
    return made


def after_submit(con, job) -> None:
    """Adds the follow-ups an application needs once it is sent."""
    db.add_todo(con, "message", f"Message the hiring manager at {job['company']}",
                job_id=job["id"], detail="The drafted message is on the application page.",
                priority=3, source=f"message:{job['id']}")
    con.commit()

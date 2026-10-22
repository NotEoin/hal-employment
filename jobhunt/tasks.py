"""Turns recruiting email and application progress into to-do items and alerts.

Reads the emails table that `jobhunt mail` fills. Each email needing action
(assessment, interview, offer, a person writing) becomes one to-do, ranked
above everything else, and one alert. Idempotent: run it as often as needed.

Every assessment invitation also brings the same neutral to-do, to read it before starting, because
timed tests rarely change once begun. It is the same for everyone, whatever private answers are saved,
so its presence says nothing about them; it names no private answer or file.
"""
from . import db, gmail

# A fixed inbox for the made-up world of the tests (tests/support/world.py patches it); None means
# `gmail.address()`.
ADDRESS: str | None = None

# How far back a first run looks; older invitations have long expired.
LOOKBACK = "-21 days"
CHECK_TITLE = "Check the test invitation for anything you want to ask before you start"
CHECK_DETAIL = "Read the invitation before you begin: a timed test rarely changes once it has started."

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
    return f"https://mail.google.com/mail/?authuser={ADDRESS or gmail.address()}#all/{int(thrid):x}"


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
        if e["kind"] == "assessment":
            # The source keeps the old prefix, so an invitation that already had its to-do gets no second one.
            asked = db.add_todo(con, "assessment", CHECK_TITLE, job_id=e["job_id"], url=thread_url(e["thrid"]),
                                priority=0, detail=CHECK_DETAIL, source=f"adjust:{e['msgid']}")
            made += [asked] if asked else []
    con.commit()
    return made


def after_sent(con, job) -> None:
    """Adds the follow-ups an application needs once the person has sent it."""
    db.add_todo(con, "message", f"Message the hiring manager at {job['company']}",
                job_id=job["id"], detail="The drafted message is on the application page.",
                priority=3, source=f"message:{job['id']}")
    con.commit()


# The old name, kept until its callers (apply/queries.py, tests/test_driver.py) move to `after_sent`.
after_submit = after_sent

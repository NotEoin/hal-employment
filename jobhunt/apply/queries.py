"""Reading and writing one application: the job, its saved documents and form, its site, its place in the queue.

Moved unchanged from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface.
`runner` and this module call each other, so each imports the other at its end, letting either load first.
"""
import json
import logging
import re
import traceback
from datetime import timedelta
from urllib.parse import urlparse

from .. import browser, db, documents, tasks
from ..core import clock, settings

log = logging.getLogger(__name__)

# Statuses that make up an application in progress or sent.
IN_FLIGHT = ("applying", "applied", "assessment", "interview", "offer")
# How an application reached Sent (migration 0009, `applications.sent_how`).
SENT_HOW = ("site_confirmed", "you_said", "email")
MESSAGE_OFFER = "Message the hiring manager? Hal drafted a note for you to send."
# Job boards that carry other employers' adverts; the employer's own page is preferred to them.
AGGREGATORS = ("indeed.", "adzuna.", "reed.co", "linkedin.", "glassdoor.", "totaljobs.", "ziprecruiter.",
               "cv-library.", "monster.", "jooble.", "talent.com", "simplyhired.", "jobrapido.", "whatjobs.")


def _job(con, job_id: str):
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise LookupError(f"no job {job_id}")
    return job


def _app(con, job_id: str) -> dict:
    row = con.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return {"stage": None, "form": None, "prep": None, "contacts": None}
    return {"stage": row["stage"], "form": json.loads(row["form"]) if row["form"] else None,
            "prep": row["prep"], "updated": row["updated"],
            "contacts": json.loads(row["contacts"]) if row["contacts"] else None}


def _set_app(con, job_id: str, **cols) -> None:
    _checkpoint(job_id)
    con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                (job_id, db.now()))
    for k, v in cols.items():
        if k == "form":
            v = json.dumps(v)
        con.execute(f"UPDATE applications SET {k} = ?, updated = ? WHERE job_id = ?", (v, db.now(), job_id))
    con.commit()


def applications(con) -> list[dict]:
    """Every application, furthest along first: those in progress and sent, and those that ended after starting."""
    # A job closed or skipped straight from 'applying' is an application dropped part way; one sent back to
    # the queue first and discarded there is not.
    rows = con.execute(f"""SELECT j.*, a.stage, a.updated,
            (SELECT MAX(at) FROM events e WHERE e.job_id = j.id AND e.kind = 'status') changed,
            EXISTS (SELECT 1 FROM events e WHERE e.job_id = j.id AND e.kind = 'effort') effort
            FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status IN ({','.join('?' * len(IN_FLIGHT))}) OR
                  (j.status IN ('rejected','withdrawn') AND a.job_id IS NOT NULL) OR
                  (j.status IN ('closed','skipped') AND (SELECT detail FROM events e WHERE e.job_id = j.id
                   AND e.kind = 'status' ORDER BY e.at DESC, e.id DESC LIMIT 1 OFFSET 1) LIKE 'applying%')
            ORDER BY CASE j.status WHEN 'offer' THEN 0 WHEN 'interview' THEN 1
              WHEN 'assessment' THEN 2 WHEN 'applying' THEN 3 WHEN 'applied' THEN 4 ELSE 5 END,
              changed DESC""", IN_FLIGHT).fetchall()
    return [{"id": r["id"], "company": r["company"], "title": r["title"], "status": r["status"], "stage": r["stage"],
             "changed": r["changed"], "location": r["location"], "url": r["url"], "effort": bool(r["effort"]),
             "state": (_app(con, r["id"])["form"] or {}).get("state"),
             "message": (_app(con, r["id"])["form"] or {}).get("message", "")} for r in rows]


def _card(con, r) -> dict:
    s = documents.summary(r)
    s["flags"] = [*effort_notes(con, r), *s["flags"]]
    return {"id": r["id"], "company": r["company"], "title": r["title"], "location": r["location"],
            "salary": r["salary"], "score": r["score"], "score_notes": r["score_notes"], "url": r["url"],
            "source": r["source"], "posted_at": r["posted_at"], "first_seen": r["first_seen"],
            "status": r["status"], "active": r["active"], "role": s["role"], "flags": s["flags"],
            "fit": s["fit"], "gaps": s["gaps"], "company_brief": s["company"]}


def _close_quietly(job_id: str) -> None:
    """Closes a job's tab once the browser is free; a browser that has gone has nothing to close."""
    try:
        browser.close(job_id)
    except Exception:  # noqa: BLE001 - the tab is closed with the window anyway
        traceback.print_exc()


def _site(url: str) -> str:
    """The site a page belongs to, without the employer's own part: acme.wd3.myworkdayjobs.com is myworkdayjobs.com."""
    parts = urlparse(url).netloc.lower().split(":")[0].split(".")
    # Second-level names such as .co.uk take a third label.
    keep = 3 if len(parts) > 2 and parts[-2] in ("co", "ac", "org", "gov", "com") and len(parts[-1]) == 2 else 2
    return ".".join(parts[-keep:])


def _site_of(con, job) -> str:
    """The site that holds a job's application form: the form's own, once Hal has reached it."""
    form = _app(con, job["id"])["form"] or {}
    return _site(form.get("url") or browser.application_url(job))


def in_progress_around(con, job_id: str) -> dict:
    """Where an application stands among those in progress, in the order they were chosen, best first.

    Returns its place, how many there are, and the applications before and after it.
    """
    # The event that made each one 'applying': its id orders them, and jobs chosen together keep their score order.
    ids = [r["id"] for r in con.execute("""
        SELECT j.id FROM jobs j WHERE j.status = 'applying'
        ORDER BY (SELECT MAX(e.id) FROM events e WHERE e.job_id = j.id AND e.kind = 'status'
                  AND e.detail LIKE 'applying%'), j.score DESC""")]
    if job_id not in ids:
        return {"place": 0, "count": len(ids), "prev": None, "next": ids[0] if ids else None}
    i = ids.index(job_id)
    return {"place": i + 1, "count": len(ids), "prev": ids[i - 1] if i else None,
            "next": ids[i + 1] if i + 1 < len(ids) else None}


def effort_notes(con, job) -> list[str]:
    """Reminders that its owner found an earlier application with this company or on this site too much effort.

    A discarded application its owner has since reopened no longer counts.
    """
    rows = con.execute("""SELECT j.id, j.company, e.detail FROM events e JOIN jobs j ON j.id = e.job_id
                          WHERE e.kind = 'effort' AND j.status = 'skipped' AND j.id != ?""",
                       (job["id"],)).fetchall()
    notes = []
    same = [r for r in rows if r["company"].lower() == job["company"].lower()]
    if same:
        notes.append(f"EFFORT: you discarded {len(same)} earlier application{'s' if len(same) > 1 else ''} "
                     f"to {job['company']} as too much effort")
    site = _site(browser.application_url(job))
    other = [r for r in rows if r["detail"] == site and r not in same]
    # A job board stands in front of most jobs, whatever their own forms are like.
    if site and other and not any(a in site for a in AGGREGATORS):
        notes.append(f"EFFORT: you discarded {len(other)} earlier application{'s' if len(other) > 1 else ''} "
                     f"on {site} as too much effort")
    return notes


def mark_applied(con, job, how: str, sent_how: str = "site_confirmed") -> None:
    """Moves an application to Sent: when, how it was seen, and the day to follow up.

    `sent_how` is one of `SENT_HOW`. The message to the hiring manager stays a draft; Hal never sends it.
    """
    if sent_how not in SENT_HOW:
        raise ValueError(f"sent_how must be one of {', '.join(SENT_HOW)}, not {sent_how!r}")
    _checkpoint(job["id"])
    db.set_status(con, job["id"], "applied", how)
    now = clock.now_utc()
    _set_app(con, job["id"], stage="submitted", sent_at=clock.iso(now), sent_how=sent_how,
             follow_up_at=clock.iso(now + timedelta(days=settings.get("notify.follow_up_days"))))
    db.alert(con, "good", f"Applied: {job['company']}", f"{job['title']}. {MESSAGE_OFFER}", job["id"])
    tasks.after_submit(con, job)
    con.commit()



# ------------------------------------------------------------------------------- the new screens

# The board's columns, in the words the board shows.
COLUMNS = (
    ("ready", "Getting ready", "Hal is preparing your CV and letter, or they are waiting for you to approve them."),
    ("form", "Form filled", "Hal has filled in the employer's form. Check it and press Submit on the site yourself."),
    ("sent", "Sent", "You sent it. Hal watches your email for replies."),
    ("interview", "Interview", "An interview or test is booked. Practice and notes live here."),
    ("offer", "Offer", "Offers you have received."),
)
IN_PROGRESS = ("ready", "form")
CLOSED_STATUSES = ("rejected", "withdrawn", "closed", "skipped")
# What the Closed list says for each `applications.closed_reason` (migration 0009).
CLOSED_WORDS = {
    "rejected": "Rejected by the employer",
    "withdrawn": "You withdrew it",
    "advert_closed": "The advert closed while you were applying",
    "too_much_effort": "You discarded it: too much effort",
    "not_interested": "You discarded it: not interested any more",
    "problem": "You discarded it: you found a problem with the job",
    "skipped_for_now": "You discarded it",
}
STEP_NAMES = ("Documents", "Form", "Sent", "Interview")
DAY = "%a %d %b"


def column_of(status: str, stage: str | None) -> str | None:
    """The board column for a job's status and its application's stage; None for a closed one."""
    if status == "applying":
        return "form" if stage in ("form", "filled") else "ready"
    return {"applied": "sent", "assessment": "interview", "interview": "interview", "offer": "offer"}.get(status)


def _app_row(con, job_id: str):
    return con.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()


def resolve(con, ref: str) -> str:
    """The job id an application route names: a job id, or the application's number ("12" or "0012")."""
    if re.fullmatch(r"\d{1,9}", ref):
        row = con.execute("SELECT job_id FROM applications WHERE number = ?", (int(ref),)).fetchone()
        if row is None:
            raise LookupError("There is no application with that number.")
        return row["job_id"]
    _job(con, ref)
    return ref


def _day(at: str | None) -> str:
    if not at:
        return ""
    try:
        return clock.parse(at).strftime(DAY)
    except (ValueError, AttributeError):
        return at[:10]


def _busy(job_id: str) -> bool:
    entry = ACTIONS.get(job_id)
    return bool(entry and entry.get("state") == "running")


def _form_counts(form: dict | None) -> tuple[int, int, int]:
    """(filled, total, to check) over the fields of the form's current page."""
    fields = [f for f in (form or {}).get("fields", []) if not f.get("skip")]
    filled = [f for f in fields if f.get("value") not in (None, "", [], False)]
    marks = driver_marks(form) if form and form.get("fields") else []
    return len(filled), len(fields), sum(1 for m in marks if m["state"] != "ok")


def _card_words(con, r, col: str) -> tuple[str, str, str]:
    """(next, tone, note) for one card on the board, in the board's own words."""
    app = _app(con, r["id"])
    form = app["form"] or {}
    if col == "ready":
        if _busy(r["id"]):
            return "Hal is writing your documents", "hal", ACTIONS[r["id"]].get("message") or ""
        return "Check and approve documents", "info", ""
    if col == "form":
        if _busy(r["id"]):
            return "Hal is filling in the form", "hal", ACTIONS[r["id"]].get("message") or ""
        filled, total, to_check = _form_counts(form)
        note = f"{filled} of {total} filled" if total else ""
        if to_check:
            need = "1 answer needs you" if to_check == 1 else f"{to_check} answers need you"
            return need, "warn", note
        return "Check it, then press Submit on the site yourself", "info", note
    row = _app_row(con, r["id"])
    if col == "sent":
        follow = row["follow_up_at"] if row is not None else None
        sent = row["sent_at"] if row is not None else None
        return (f"Follow up on {_day(follow)}" if follow else "Waiting for a reply"), "", (
            f"Sent {_day(sent)}" if sent else "")
    if col == "interview":
        return "Interview or test booked", "good", ""
    return "Offer received", "good", ""


def _closed_why(con, r) -> str:
    row = _app_row(con, r["id"])
    reason = row["closed_reason"] if row is not None else None
    words = CLOSED_WORDS.get(reason or "", "")
    if not words:
        words = {"rejected": CLOSED_WORDS["rejected"], "withdrawn": CLOSED_WORDS["withdrawn"],
                 "closed": CLOSED_WORDS["advert_closed"]}.get(r["status"], CLOSED_WORDS["skipped_for_now"])
    detail = (row["closed_detail"] if row is not None else None) or ""
    return f"{words} ({detail})" if detail else words


def board(con) -> dict:
    """The Applications board: its columns, each card's next step, and the Closed list."""
    columns = {key: [] for key, _, _ in COLUMNS}
    closed = []
    for r in con.execute("""SELECT j.*, a.stage, a.number FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
                            WHERE j.status IN ('applying','applied','assessment','interview','offer')
                               OR (j.status IN ('rejected','withdrawn','closed','skipped') AND a.job_id IS NOT NULL)
                            ORDER BY a.number IS NULL, a.number DESC, j.id""").fetchall():
        col = column_of(r["status"], r["stage"])
        if col is None:
            closed.append({"id": r["id"], "number": r["number"], "title": r["title"], "org": r["company"],
                           "why": _closed_why(con, r)})
            continue
        nxt, tone, note = _card_words(con, r, col)
        columns[col].append({"id": r["id"], "number": r["number"], "org": r["company"], "title": r["title"],
                             "next": nxt, "tone": tone, "note": note, "hal_busy": _busy(r["id"])})
    for col in IN_PROGRESS:
        order = in_progress_ids(con)
        columns[col].sort(key=lambda c: order.index(c["id"]) if c["id"] in order else len(order))
    return {"columns": [{"key": k, "title": t, "help": h, "items": columns[k]} for k, t, h in COLUMNS],
            "closed": closed}


def in_progress_ids(con) -> list[str]:
    """Applications in Getting ready and Form filled, in the order they were chosen, best first."""
    return [r["id"] for r in con.execute("""
        SELECT j.id FROM jobs j WHERE j.status = 'applying'
        ORDER BY (SELECT MAX(e.id) FROM events e WHERE e.job_id = j.id AND e.kind = 'status'
                  AND e.detail LIKE 'applying%'), j.score DESC""")]


def nav(con, job_id: str) -> dict:
    """Previous and Next through the applications in progress: {index, total, prev, next} (index from 1)."""
    ids = in_progress_ids(con)
    if job_id not in ids:
        return {"index": 0, "total": len(ids), "prev": None, "next": None}
    i = ids.index(job_id)
    return {"index": i + 1, "total": len(ids), "prev": ids[i - 1] if i else None,
            "next": ids[i + 1] if i + 1 < len(ids) else None}


def job_card(r) -> dict:
    """The parts of a JobCard the application page shows; the Jobs pages build the full card."""
    keys = r.keys()
    return {"id": r["id"], "title": r["title"], "org": r["company"], "place": r["location"] or "",
            "pay": r["salary"] or "", "closes": r["closes_at"] if "closes_at" in keys else None,
            "url": r["url"], "status": r["status"], "score": r["score"], "source": r["source"]}


def _steps(con, job_id: str, col: str | None, app: dict, row) -> tuple[int, list[dict]]:
    """The application's step (1 to 4) and the four steps' notes."""
    step = {"ready": 1, "form": 2, "sent": 3, "interview": 4, "offer": 4}.get(col or "", 1)
    _, total, to_check = _form_counts(app["form"])
    notes = [
        "Approved" if step > 1 else ("Hal is writing" if _busy(job_id) and col == "ready" else "Waiting for you"),
        ("Filled" if step > 2 else (f"{to_check} answer{'s' if to_check != 1 else ''} need you" if to_check
                                    else "Filled" if total else "Hal is filling it in")) if step >= 2
        else "Starts after you approve",
        (f"Sent {_day(row['sent_at'])}" if row is not None and row["sent_at"] else "Sent") if step >= 3 else "",
        "Booked" if step == 4 else "",
    ]
    states = ["done" if k + 1 < step else "now" if k + 1 == step else "later" for k in range(4)]
    return step, [{"name": n, "note": t, "state": s} for n, t, s in zip(STEP_NAMES, notes, states, strict=True)]


def _cv_changes(job, docs: dict) -> list[dict]:
    """The per-job CV changes, each with whether it is on."""
    from .. import cv

    try:
        found = cv.changes(cv.load(), documents.current_cv(job, docs))
    except Exception as e:  # a CV that cannot be read shows no changes, and the page still opens
        log.warning("could not list the CV changes for %s: %r", job["id"], e)
        return []
    undone = set(docs.get("undone_changes") or [])
    kinds = {"text": "Reworded", "order": "Moved", "added": "Added", "left out": "Left out"}
    return [{"id": c["path"], "kind": kinds.get(c["kind"], c["kind"]), "where": c["label"], "before": c["before"],
             "after": "", "why": "", "on": c["path"] not in undone} for c in found]


def _fields(form: dict | None) -> list[dict]:
    """The form's answers as the API's Field, with the words each source shows as."""
    if not form or not form.get("fields"):
        return []
    marks = {m["hid"]: m for m in driver_marks(form)}
    out = []
    for f in form["fields"]:
        if f.get("skip") or (f.get("type") == "password" and f["hid"] not in marks):
            continue
        src = source_of(f.get("source"))
        mark = marks.get(f["hid"])
        value = f.get("value")
        if src == "private":
            # A private answer never leaves the form in an API reply (gate G5).
            answer = PRIVATE_SHOWN
        elif isinstance(value, list):
            answer = ", ".join(map(str, value))
        else:
            answer = "" if value in (None, False) else str(value)
        out.append({"hid": f["hid"], "q": f.get("label") or "", "a": answer,
                    "ok": bool(mark is None or mark["state"] == "ok"), "src": src, "src_label": SOURCE_WORDS[src],
                    "why": f.get("why") or f.get("note") or SOURCE_WHY[src], "notes_used": f.get("notes_used") or [],
                    "problem": f.get("error") or ""})
    return out


# The engines' field sources ("profile: diversity", "documents: CV", ...) by their first word, as the API's
# Field.src, and the chip words ("Site rule" in plain words).
SOURCE_OF = {"profile": "profile", "documents": "cv", "cv": "cv", "record": "record", "your": "saved",
             "saved": "saved", "note": "note", "hal": "hal", "private": "private", "you": "you"}
PRIVATE_SHOWN = "Filled in from your private answers"


def source_of(source: str | None) -> str:
    """The Field.src for an engine's source words; anything unknown counts as written by Hal."""
    first = re.split(r"[:\s]", (source or "").strip().lower(), maxsplit=1)[0]
    return SOURCE_OF.get(first, "hal")

SOURCE_WORDS = {"profile": "Your profile", "cv": "Your CV", "record": "Your record", "saved": "Saved answer",
                "note": "Site note", "hal": "Written by Hal", "private": "Private answer", "you": "Your answer"}
SOURCE_WHY = {"profile": "From your profile (Settings, You).", "cv": "From your CV.", "record": "From your record.",
              "saved": "An answer you gave on an earlier form.", "note": "From a note about this job site.",
              "hal": "Written for this job by Hal. Please read it.",
              "private": "From your private answers. These are never shown to the AI or saved in logs.",
              "you": "You typed this."}


def application(con, ref: str) -> dict:
    """The application page (GET /applications/{id})."""
    job_id = resolve(con, ref)
    r = _job(con, job_id)
    app = _app(con, job_id)
    row = _app_row(con, job_id)
    col = column_of(r["status"], app["stage"])
    step, steps = _steps(con, job_id, col, app, row)
    docs = documents.load(con, r)
    letter = docs.get("letter") or ""
    entry = ACTIONS.get(job_id) or {}
    writing = ([{"text": entry.get("message") or entry.get("name") or "", "state": "running"}]
               if col == "ready" and _busy(job_id) else None)
    held = _running_for(job_id) if _busy(job_id) else None
    form = app["form"]
    filled, total, to_check = _form_counts(form)
    files = documents.files(r)
    out = {
        "id": job_id, "number": row["number"] if row is not None else None, "job": job_card(r), "step": step,
        "steps": steps, "nav": nav(con, job_id), "column": col, "action_id": held["id"] if held else None,
        "first_time": not settings.get("setup.first_app_seen"),
        "docs": {"state": "writing" if writing else "approved" if step > 1 or (row is not None and row["approved_at"])
                 else "ready",
                 "writing": writing, "approved_at": (row["approved_at"] if row is not None else None)
                 or docs.get("approved") or None,
                 "cv": {"changes": _cv_changes(r, docs), "preview_url": _file_url(files.get("cv")),
                        "version_n": None, "look": None,
                        "sent_as": (row["cv_sent_as"] if row is not None else None) or "hal_copy"},
                 "letter": {"text": letter, "words": len(letter.split()), "tone": None, "evidence": None,
                            "checked": None, "preview_url": _file_url(files.get("letter"))},
                 "answers": [{"q": a.get("question", ""), "a": a.get("answer", "")} for a in docs.get("answers", [])],
                 "message": {"text": docs.get("message") or "", "to": docs.get("message_to") or "", "to_why": ""}},
        "form": None, "sent": None, "interview": None,
    }
    if form is not None and step >= 2:
        out["form"] = {"state": form.get("state") or "", "message": form.get("message") or "", "filled": filled,
                       "total": total, "to_check": to_check, "fields": _fields(form),
                       "notes_used": form.get("notes_used") or [], "site": _site(form.get("url") or r["url"] or ""),
                       "platform": form.get("platform") or "", "mode": (row["mode"] if row is not None else None)
                       or browser.mode(), "last_page": bool(form.get("final")),
                       "handed_over": bool(row is not None and row["handed_over_at"]),
                       "words": form_words(form)}
    if step >= 3 and row is not None:
        timeline = []
        if row["sent_at"]:
            timeline.append({"t": "Sent", "at": row["sent_at"], "d": SENT_WORDS.get(row["sent_how"] or "", "")})
        if row["follow_up_at"]:
            timeline.append({"t": "Follow up", "at": row["follow_up_at"],
                             "d": "If there is no reply by then, Hal will draft a short, polite follow-up for you "
                                  "to send."})
        contacts = app["contacts"] or []
        people = [{"name": c.get("name", ""), "role": c.get("role") or c.get("title") or "",
                   "why": c.get("why") or "", "note": c.get("note") or ""} for c in contacts
                  if isinstance(c, dict)]
        out["sent"] = {"timeline": timeline, "people": people}
    if step == 4:
        out["interview"] = {"when": None, "where": None, "prep_md": app["prep"] or "", "questions": []}
    return out


SENT_WORDS = {"site_confirmed": "The site said it was received after you pressed Submit.",
              "you_said": "You said you sent it yourself.", "email": "An email from the employer confirmed it."}


def _running_for(job_id: str) -> dict | None:
    """The action Hal is running for this application, for the page's Stop button."""
    from ..core import runner

    return runner.running_for(job_id)


def _file_url(path: str | None) -> str | None:
    return f"/files?path={path}" if path else None


def form_words(form: dict) -> str:
    """What the Form step says, the same in every mode: see `driver.form_words`."""
    from .driver import form_words as words

    return words(form)


def driver_marks(form: dict) -> list[dict]:
    """The driver's marks for a form's fields (green fine, amber to check)."""
    from .driver import _marks

    return _marks(form)


from ..core.runner import ACTIONS, _checkpoint  # noqa: E402 - imported last; runner imports this module

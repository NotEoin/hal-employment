"""Reading and writing one application: the job, its saved documents and form, its site, its place in the queue.
"""
import json
import logging
import re
import traceback
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from jobhunt import browser, db, documents, tasks
from jobhunt.apply import rewrite
from jobhunt.core import clock, settings
from jobhunt.core.runner import ACTIONS, ACTIVE, checkpoint

log = logging.getLogger(__name__)

# Statuses that make up an application in progress or sent.
IN_FLIGHT = ("applying", "applied", "assessment", "interview", "offer")
# How an application reached Sent (migration 0009, `applications.sent_how`).
SENT_HOW = ("site_confirmed", "you_said", "email")
MESSAGE_OFFER = "Message the hiring manager? Hal drafted a note for you to send."
# Job boards that carry other employers' adverts; the employer's own page is preferred to them.
AGGREGATORS = ("indeed.", "adzuna.", "reed.co", "linkedin.", "glassdoor.", "totaljobs.", "ziprecruiter.",
               "cv-library.", "monster.", "jooble.", "talent.com", "simplyhired.", "jobrapido.", "whatjobs.")


def load_job(con, job_id: str):
    """The `jobs` row for `job_id`; LookupError when there is none."""
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise LookupError(f"no job {job_id}")
    return job


def load_app(con, job_id: str) -> dict:
    """An application's stage, form, prep and contacts; empty values when it has no row yet."""
    row = con.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return {"stage": None, "form": None, "prep": None, "contacts": None}
    return {"stage": row["stage"], "form": json.loads(row["form"]) if row["form"] else None,
            "prep": row["prep"], "updated": row["updated"],
            "contacts": json.loads(row["contacts"]) if row["contacts"] else None}


def set_app(con, job_id: str, **cols) -> None:
    """Writes columns of a job's application row (made if missing); stops an action its application dropped."""
    checkpoint(job_id)
    con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                (job_id, db.now()))
    for k, v in cols.items():
        if k == "form":
            v = json.dumps(v)
        con.execute(f"UPDATE applications SET {k} = ?, updated = ? WHERE job_id = ?", (v, db.now(), job_id))
    con.commit()


def record_form_state(con, job_id: str, state: str, message: str) -> None:
    """Records where an application stands, for its page: working, ready, needs, stuck or sent."""
    try:
        form = load_app(con, job_id)["form"]
        if form is not None:
            form.update(state=state, message=message)
            set_app(con, job_id, form=form)
    except Exception as e:  # the action's own message still reaches the page
        log.debug("could not copy the action's state to job %s's form: %s", job_id, e)


def form_stuck(con, job_id: str, message: str) -> None:
    """`runner` hook: a failed action shows on its application's page as stuck."""
    record_form_state(con, job_id, "stuck", message)


def _close_quietly(job_id: str) -> None:
    """Closes a job's tab once the browser is free; a browser that has gone has nothing to close."""
    try:
        browser.close(job_id)
    except Exception:  # noqa: BLE001 - the tab is closed with the window anyway
        traceback.print_exc()


def _platform(url: str) -> str:
    """The application system's name for "Something wrong here?" ("Workable"), or "" for an employer's own site."""
    from jobhunt.sites import notes

    ats = notes.ats_of(url)
    return notes.ATS_NAMES.get(ats, ats) if ats else ""


def site_of(con, job) -> str:
    """The site that holds a job's application form: the form's own, once Hal has reached it."""
    form = load_app(con, job["id"])["form"] or {}
    return browser.site_key(form.get("url") or browser.application_url(job))


def mark_applied(con, job, how: str, sent_how: str = "site_confirmed") -> None:
    """Moves an application to Sent: when, how it was seen, and the day to follow up.

    `sent_how` is one of `SENT_HOW`. The message to the hiring manager stays a draft; Hal never sends it.
    """
    if sent_how not in SENT_HOW:
        raise ValueError(f"sent_how must be one of {', '.join(SENT_HOW)}, not {sent_how!r}")
    checkpoint(job["id"])
    db.set_status(con, job["id"], "applied", how)
    now = clock.now_utc()
    set_app(con, job["id"], stage="submitted", sent_at=clock.iso(now), sent_how=sent_how,
             follow_up_at=clock.iso(now + timedelta(days=settings.get("notify.follow_up_days"))))
    db.alert(con, "good", f"Applied: {job['company']}", f"{job['title']}. {MESSAGE_OFFER}", job["id"])
    tasks.after_sent(con, job)
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
    load_job(con, ref)
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


def _need_words(to_check: int) -> str:
    return "1 answer needs you" if to_check == 1 else f"{to_check} answers need you"


STOPPED_WORDS = {"interrupted": "Hal was restarted part way through", "stopped": "you stopped it"}
STOPPED = ("Hal stopped before the form was filled: {why}. Press \"Fill it in again\", or open the form yourself "
           "in Hal's browser window and press \"Fill in from this page\".")


def _stopped_why(con, job_id: str, form: dict | None) -> str | None:
    """Why Hal is no longer working on a form it left unfinished; None while it works, or once it finished.

    A run that failed or was cut off left the form saying Hal was filling it, or waiting for the person and
    carrying on afterwards, long after Hal had stopped (testers, 2026-11-04).
    """
    if not form or form.get("state") not in ("working", "needs"):
        return None
    if _queued(con, job_id):
        return None
    last = con.execute("""SELECT state, error FROM actions WHERE job_id = ? AND kind LIKE 'apply.%'
                          ORDER BY created DESC LIMIT 1""", (job_id,)).fetchone()
    if last is None or last["state"] == "done":
        return None
    why = (last["error"] or STOPPED_WORDS.get(last["state"], "")).strip().rstrip(".")
    return why or "Hal did not finish"


def _card_words(con, r, col: str) -> tuple[str, str, str]:
    """(next, tone, note) for one card on the board, in the board's own words."""
    app = load_app(con, r["id"])
    form = app["form"] or {}
    if col == "ready":
        if _busy(r["id"]):
            return "Hal is writing your documents", "hal", ACTIONS[r["id"]].get("message") or ""
        if not _has_documents(r):
            if _queued(con, r["id"]):
                return "Waiting for Hal to write your documents", "hal", ""
            return "Documents not made: make them again", "warn", _prepare_stopped(con, r["id"])
        return "Check and approve documents", "info", ""
    if col == "form":
        if _busy(r["id"]):
            return "Hal is filling in the form", "hal", ACTIONS[r["id"]].get("message") or ""
        why = _stopped_why(con, r["id"], form)
        if why:
            return "Hal stopped: fill it in again, or from your tab", "warn", why
        filled, total, to_check = _form_counts(form)
        note = f"{filled} of {total} filled" if total else ""
        if to_check:
            return _need_words(to_check), "warn", note
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


def _queued(con, job_id: str) -> bool:
    """True when an action for this job is waiting its turn or under way."""
    return con.execute(f"SELECT 1 FROM actions WHERE job_id = ? AND state IN ({','.join('?' * len(ACTIVE))})",
                       (job_id, *ACTIVE)).fetchone() is not None


def _prepare_stopped(con, job_id: str) -> str:
    """Why the job's documents were last left unmade, in a few words; "" when Hal never began them."""
    last = con.execute("""SELECT state, error FROM actions WHERE job_id = ? AND kind = 'docs.prepare'
                          ORDER BY created DESC LIMIT 1""", (job_id,)).fetchone()
    if last is None or last["state"] == "done":
        return ""
    return (last["error"] or STOPPED_WORDS.get(last["state"], "")).strip()


def _has_documents(job) -> bool:
    """True when Hal has made this application's CV, so its documents can be approved."""
    return "cv" in documents.files(job)


def _active_jobs(con) -> set[str]:
    """Jobs with an action waiting its turn or under way, in one query for the whole board."""
    return {r[0] for r in con.execute(f"SELECT DISTINCT job_id FROM actions WHERE job_id IS NOT NULL AND state IN "
                                      f"({','.join('?' * len(ACTIVE))})", ACTIVE)}


def _mark_out_of_date(con, cards: list[dict], approved: set[str]) -> None:
    """Gives each card `out_of_date` (why, or "") and `rewritable`: out of date, ready to approve and with no
    action of Hal's queued, so Write the out-of-date documents again can take it. One pass for the board."""
    stale = rewrite.out_of_date(con, [c["id"] for c in cards if c.get("approvable") and c["id"] not in approved])
    active = _active_jobs(con) if stale else set()
    for c in cards:
        c["out_of_date"] = stale.get(c["id"], "")
        c["rewritable"] = bool(c["out_of_date"]) and c["id"] not in active


def board(con) -> dict:
    """The Applications board: its columns, each card's next step, and the Closed list."""
    columns = {key: [] for key, _, _ in COLUMNS}
    closed, approved = [], set()
    for r in con.execute("""SELECT j.*, a.stage, a.number, a.approved_at FROM jobs j
                            LEFT JOIN applications a ON a.job_id = j.id
                            WHERE j.status IN ('applying','applied','assessment','interview','offer')
                               OR (j.status IN ('rejected','withdrawn','closed','skipped') AND a.job_id IS NOT NULL)
                            ORDER BY a.number IS NULL, a.number DESC, j.id""").fetchall():
        col = column_of(r["status"], r["stage"])
        if col is None:
            closed.append({"id": r["id"], "number": r["number"], "title": r["title"], "org": r["company"],
                           "why": _closed_why(con, r)})
            continue
        nxt, tone, note = _card_words(con, r, col)
        busy = _busy(r["id"])
        columns[col].append({"id": r["id"], "number": r["number"], "org": r["company"], "title": r["title"],
                             "next": nxt, "tone": tone, "note": note, "hal_busy": busy,
                             "approvable": col == "ready" and not busy and _has_documents(r),
                             "remakeable": col == "ready" and not busy and not _has_documents(r)
                             and not _queued(con, r["id"])})
        if r["approved_at"]:
            approved.add(r["id"])
    _mark_out_of_date(con, [c for col in columns.values() for c in col], approved)
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
        ("Filled" if step > 2 else (_need_words(to_check) if to_check
                                    else "Filled" if total else "Hal is filling it in")) if step >= 2
        else "Starts after you approve",
        (f"Sent {_day(row['sent_at'])}" if row is not None and row["sent_at"] else "Sent") if step >= 3 else "",
        "Booked" if step == 4 else "",
    ]
    states = ["done" if k + 1 < step else "now" if k + 1 == step else "later" for k in range(4)]
    return step, [{"name": n, "note": t, "state": s} for n, t, s in zip(STEP_NAMES, notes, states, strict=True)]


def _cv_changes(job, docs: dict) -> list[dict]:
    """The per-job CV changes, each with whether it is on: the kept change list, or for an older application
    the differences from the main CV."""
    from jobhunt.cvstudio import cv, tailor

    if tailor.kept(job["id"]):
        return [{k: c[k] for k in ("id", "kind", "where", "before", "after", "why", "ev", "on")}
                for c in tailor.shown(job["id"])["changes"]]
    try:
        found = cv.changes(cv.load(), documents.proposed_cv(job, docs))
    except Exception as e:  # a CV that cannot be read shows no changes, and the page still opens
        log.warning("could not list the CV changes for %s: %r", job["id"], e)
        return []
    undone = set(docs.get("undone_changes") or [])
    kinds = {"text": "Reworded", "order": "Moved", "added": "Added", "left out": "Left out"}
    return [{"id": c["id"], "kind": kinds.get(c["kind"], c["kind"]), "where": c["label"], "before": c["before"],
             "after": c.get("after", ""), "why": "", "on": c["id"] not in undone} for c in found]


def _cv_kept(con, job_id: str) -> dict:
    """`schema` 2 when the application's CV can be edited line by line (`tailor.kept`), else 1, and the number
    of the version it was approved as."""
    from jobhunt.cvstudio import store, tailor

    vid = tailor.version_of(job_id, con)
    return {"schema": 2 if tailor.kept(job_id, con) else 1,
            "version_n": store.version(vid, con=con)["n"] if vid else None}


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
            # A private answer never leaves the form in an API reply.
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
# Field.src, and the chip words, where a site note reads "Site rule".
SOURCE_OF = {"profile": "profile", "documents": "cv", "cv": "cv", "record": "record", "your": "saved",
             "saved": "saved", "note": "note", "hal": "hal", "private": "private", "you": "you", "site": "site"}
PRIVATE_SHOWN = "Filled in from your private answers"


def source_of(source: str | None) -> str:
    """The Field.src for an engine's source words; anything unknown counts as written by Hal."""
    first = re.split(r"[:\s]", (source or "").strip().lower(), maxsplit=1)[0]
    return SOURCE_OF.get(first, "hal")

SOURCE_WORDS = {"profile": "Your profile", "cv": "Your CV", "record": "Your record", "saved": "Saved answer",
                "note": "Site note", "hal": "Written by Hal", "private": "Private answer", "you": "Your answer",
                "site": "Filled in by the site"}
SOURCE_WHY = {"profile": "From your profile (Settings, You).", "cv": "From your CV.", "record": "From your record.",
              "saved": "An answer you gave on an earlier form.", "note": "From a note about this job site.",
              "hal": "Written for this job by Hal. Please read it.",
              "private": "From your private answers. These are never shown to the AI or saved in logs.",
              "you": "You typed this.", "site": "The site had already filled this in, and Hal left it as it was."}


def _cv_pick(con, job) -> list[str]:
    """The job's requirements to tick before Hal adjusts its CV (`tailor.to_pick`); empty when not asked."""
    from jobhunt.cvstudio import tailor

    return tailor.to_pick(dict(job), con)


def _letter_notes(job, docs: dict) -> dict:
    """The letter's Tone, Evidence and Checked: kept with a letter drafted at form time, else in its pack."""
    from jobhunt import pack

    return docs.get("letter_notes") or pack.read_letter_notes(job)


def application(con, ref: str) -> dict:
    """The application page."""
    job_id = resolve(con, ref)
    r = load_job(con, job_id)
    app = load_app(con, job_id)
    row = _app_row(con, job_id)
    col = column_of(r["status"], app["stage"])
    step, steps = _steps(con, job_id, col, app, row)
    docs = documents.load(con, r)
    letter = docs.get("letter") or ""
    notes = _letter_notes(r, docs) if letter else {}
    writing = _writing(con, job_id) if col == "ready" else None
    held = _running_for(job_id) if _busy(job_id) or writing else None
    form = app["form"]
    filled, total, to_check = _form_counts(form)
    files = documents.files(r)
    again = _again(con, r, row, step, files) if col == "ready" and not writing else {}
    out = {
        "id": job_id, "number": row["number"] if row is not None else None, "job": job_card(r), "step": step,
        "steps": steps, "nav": nav(con, job_id), "column": col, "action_id": held["id"] if held else None,
        # A closed application kept its step, so its page offered Approve or Fill for a job that had closed.
        "closed": _closed_why(con, r) if r["status"] in CLOSED_STATUSES else None,
        "first_time": not settings.get("setup.first_app_seen"),
        "docs": {"state": "writing" if writing else "approved" if step > 1 or (row is not None and row["approved_at"])
                 else "failed" if again.get("failed") else "ready",
                 "stopped": again.get("stopped"), "out_of_date": again.get("out_of_date"),
                 "own_edits": bool(again.get("own_edits")),
                 "writing": writing, "approved_at": (row["approved_at"] if row is not None else None)
                 or docs.get("approved") or None,
                 "cv": {"changes": _cv_changes(r, docs), "preview_url": _file_url(files.get("cv")),
                        **_cv_kept(con, job_id), "look": None,
                        "pick": _cv_pick(con, r) if step == 1 and not writing else [],
                        "sent_as": (row["cv_sent_as"] if row is not None else None)
                        or ("original" if documents.original_file() else "hal_copy")},
                 "letter": {"text": letter, "words": len(letter.split()), "tone": notes.get("tone"),
                            "evidence": notes.get("evidence"), "checked": notes.get("checked"),
                            "preview_url": _file_url(files.get("letter"))},
                 "answers": [{"q": a.get("question", ""), "a": a.get("answer", "")} for a in docs.get("answers", [])],
                 "message": {"text": docs.get("message") or "", "to": docs.get("message_to") or "", "to_why": ""}},
        "form": None, "sent": None, "interview": None,
    }
    if form is not None and step >= 2:
        form_url = form.get("url") or r["url"] or ""
        out["form"] = {"state": form.get("state") or "", "message": form.get("message") or "", "filled": filled,
                       "total": total, "to_check": to_check, "fields": _fields(form),
                       "notes_used": form.get("notes_used") or [], "site": browser.site_key(form_url),
                       "platform": form.get("platform") or _platform(form_url),
                       "mode": (row["mode"] if row is not None else None) or browser.mode(),
                       "last_page": bool(form.get("final")),
                       "handed_over": bool(row is not None and row["handed_over_at"]),
                       "words": form_words(form)}
        why = _stopped_why(con, job_id, form)
        if why:
            out["form"].update(state="stopped", words=STOPPED.format(why=why))
            out["steps"][1]["note"] = "Hal stopped"
        elif held and form.get("state") == "working":
            # While Hal works, the page says what it is doing rather than nothing (testers, 2026-11-04).
            doing = (ACTIONS.get(job_id) or {}).get("message") or "opening the form"
            out["form"].update(words=f"Hal is working on the form: {doing[0].lower()}{doing[1:]}.".replace("..", "."))
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


WAITING_TO_WRITE = "Waiting for Hal to start writing, after the work ahead of it"


def _writing(con, job_id: str) -> list[dict] | None:
    """The Documents step's writing state: what Hal is doing while it works on the application, or that a
    write is queued for it; None otherwise. A queued write shows too, so nobody edits documents that are about
    to be written again."""
    if _busy(job_id):
        entry = ACTIONS.get(job_id) or {}
        return [{"text": entry.get("message") or entry.get("name") or "", "state": "running"}]
    queued = con.execute(f"""SELECT 1 FROM actions WHERE job_id = ? AND kind = 'docs.prepare'
                             AND state IN ({','.join('?' * len(ACTIVE))})""", (job_id, *ACTIVE)).fetchone()
    return [{"text": WAITING_TO_WRITE, "state": "running"}] if queued else None


def _again(con, job, row, step: int, files: dict) -> dict:
    """What the Documents step says about writing them again: `failed` (the last write failed or stopped before
    the CV was made, and Hal is not making them now), `stopped` (why the last write did
    not finish), `out_of_date` (why, `rewrite.out_of_date`) and `own_edits` (`rewrite.has_own_edits`, so the
    page asks before writing everything fresh)."""
    if step > 1 or (row is not None and row["approved_at"]):
        return {}
    stopped = _prepare_stopped(con, job["id"]) if not _queued(con, job["id"]) else ""
    if "cv" not in files and stopped:
        return {"failed": True, "stopped": stopped}
    return {"stopped": stopped or None, "out_of_date": rewrite.out_of_date(con, [job["id"]]).get(job["id"]),
            "own_edits": rewrite.has_own_edits(con, job)}


SENT_WORDS = {"site_confirmed": "The site said it was received after you pressed Submit.",
              "you_said": "You said you sent it yourself.", "email": "An email from the employer confirmed it."}


def _running_for(job_id: str) -> dict | None:
    """The action Hal is running for this application, for the page's Stop button."""
    from jobhunt.core import runner

    return runner.running_for(job_id)


def _file_url(path: str | None) -> str | None:
    """The `/files` address of a stored file, with the path encoded so "+", "#" and "&" survive.

    The file's change time rides along (`m`, ignored by the server), so a preview shows the new PDF as soon
    as an Undo or an edit makes it again, rather than the browser's copy of the old one.
    """
    if not path:
        return None
    try:
        made = Path(path).stat().st_mtime_ns
    except OSError:
        made = 0
    return f"/files?path={quote(path)}&m={made}"


def form_words(form: dict) -> str:
    """What the Form step says, the same in every mode: see `driver.form_words`."""
    from jobhunt.apply.driver import form_words as words

    return words(form)


def driver_marks(form: dict) -> list[dict]:
    """The driver's marks for a form's fields (green fine, amber to check)."""
    from jobhunt.apply.driver import _marks

    return _marks(form)


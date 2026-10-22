"""Reading and writing one application: the job, its saved documents and form, its site, its place in the queue.

Moved unchanged from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface.
`runner` and this module call each other, so each imports the other at its end, letting either load first.
"""
import json
import traceback
from urllib.parse import urlparse

from .. import browser, db, documents, tasks

# Statuses that make up an application in progress or sent.
IN_FLIGHT = ("applying", "applied", "assessment", "interview", "offer")
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


def mark_applied(con, job, how: str) -> None:
    _checkpoint(job["id"])
    db.set_status(con, job["id"], "applied", how)
    _set_app(con, job["id"], stage="submitted")
    db.alert(con, "good", f"Applied: {job['company']}", job["title"], job["id"])
    tasks.after_submit(con, job)
    con.commit()


from ..core.runner import _checkpoint  # noqa: E402 - imported last; runner imports this module

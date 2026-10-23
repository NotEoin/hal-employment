"""Routes of My CV's Checks tab.

- GET answers from the `cv_checks` cache, so it stays fast; the printed PDF joins the scanner score once
  the printer keeps one per version and look (without one the document's own text is read).
- "Against a job" needs no AI on this page: the type's keywords found in the advert, and the job's
  requirements Hal already worked out (`job_requirements`), each with the CV's evidence or a note.
- "Fix it" and "Does not matter" change only what they name; a fix is a new version made by the person.
"""
from __future__ import annotations

import json
import logging

from jobhunt import cvscore, db
from jobhunt.core import runner, settings
from jobhunt.cvstudio import (
    checks,
    checks_actions,  # noqa: F401 - registers cv.recruiter before a route starts it
)
from jobhunt.web.router import BadRequest, NotFound, Request, route

log = logging.getLogger(__name__)
NO_CV = "There is no CV in Hal yet. Add yours first."
JOB_CHOICES = 20
OPEN_STATUSES = ("applying", "applied", "assessment", "interview", "offer")
NOTE_MISSING = "The advert asks for this. Your CV does not show it yet."
NOTE_DESIRABLE = "Desirable, not essential"


def _current():
    try:
        return checks.current_cv()
    except LookupError as e:
        raise NotFound(NO_CV) from e


def _recruiter(con, version_id: str, look_id: str) -> dict | None:
    row = con.execute("SELECT recruiter, recruiter_at FROM cv_checks WHERE version_id = ? AND look_id = ?",
                      (version_id, look_id)).fetchone()
    if not row or not row["recruiter"]:
        return None
    read = json.loads(row["recruiter"])
    return {"text": read.get("text", ""), "ai_words": read.get("ai_words", ""), "at": row["recruiter_at"]}


def _jobs(con) -> list[dict]:
    marks = ",".join("?" * len(OPEN_STATUSES))
    rows = con.execute(f"SELECT id, title, company FROM jobs WHERE saved_at IS NOT NULL OR status IN ({marks})"
                       " ORDER BY coalesce(saved_at, first_seen) DESC LIMIT ?",
                       (*OPEN_STATUSES, JOB_CHOICES)).fetchall()
    return [{"id": r["id"], "title": r["title"], "company": r["company"]} for r in rows]


def against_job(con, job_id: str, cv_text: str, t) -> dict:
    """What the CV has evidence for in one advert, and what it does not, leaving out what does not matter."""
    job = con.execute("SELECT id, title, company, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise NotFound("That job is not in Hal's list.")
    off = {r["term"].lower() for r in con.execute("SELECT term FROM cv_keywords_off WHERE job_id = ?", (job_id,))}
    found = cvscore.keywords(job["description"] or "", cv_text, terms=t.keywords.get("skills", []),
                             aliases=t.keywords.get("aliases", {}))
    have = [k for k in found["have"] if k not in off]
    missing = [{"k": k, "note": NOTE_MISSING} for k in found["missing"] if k not in off]
    for r in con.execute("SELECT text, essential, state FROM job_requirements WHERE job_id = ? ORDER BY n", (job_id,)):
        if r["state"] == "off" or r["text"].lower() in off:
            continue
        if r["state"] in ("met", "part"):
            have.append(r["text"])
        else:
            missing.append({"k": r["text"], "note": NOTE_DESIRABLE if r["essential"] == 0 else NOTE_MISSING})
    return {"job_id": job["id"], "title": job["title"], "company": job["company"], "have": have, "missing": missing}


@route("GET", "/api/v2/cv/checks")
def checks_tab(req: Request) -> dict:
    """The Checks tab: scanner score, the type's checks, a recruiter's read and, with `job_id`, one job."""
    version_id, doc, t, row = _current()
    profile = checks.profile_now()
    look = row["look_id"]
    found = checks.for_version(req.con, version_id, doc, t, look, profile, spelling=settings.get("cvdocs.spelling"),
                               columns=checks.columns_of(look))
    job_id = req.query.get("job_id")
    text = "\n".join(checks.doc_lines(doc, profile))
    return {"version_id": version_id, "scanner": found["scanner"],
            "type": checks.link_suggestions(req.con, found["type"]),
            "recruiter": _recruiter(req.con, version_id, look),
            "job": against_job(req.con, job_id, text, t) if job_id else None, "jobs": _jobs(req.con)}


@route("POST", "/api/v2/cv/checks/fix/{fix_id}")
def fix_it(req: Request) -> dict:
    """Fixes one scanner problem in a new version."""
    from jobhunt.cvstudio import store

    if req.params["fix_id"] not in checks.FIXES:
        raise NotFound("Hal has no fix for that.")
    version_id, doc, t, _ = _current()
    try:
        fixed, what = checks.fix(req.params["fix_id"], doc, t)
    except LookupError as e:
        raise NotFound(str(e)) from e
    new_id = store.new_version(fixed, origin="improved", what=f"You pressed Fix it: {what}", by="you",
                               parent=version_id)
    return {"version_id": new_id, "what": what}


@route("POST", "/api/v2/cv/checks/recruiter")
def ask_recruiter(req: Request) -> dict:
    """Starts a recruiter's read (an action; the answer arrives on the event stream)."""
    version_id, doc, t, row = _current()
    text = "\n".join(checks.doc_lines(doc, checks.profile_now()))
    action_id = runner.start("cv.recruiter", subject="cv.recruiter", replace=True,
                             params={"version_id": version_id, "type_id": t.id, "look_id": row["look_id"],
                                     "text": text},
                             session_id=req.session.id if req.session else None, remote=req.remote)
    return {"action_id": action_id}


@route("POST", "/api/v2/cv/keywords/off")
def keyword_off(req: Request) -> dict:
    """"Does not matter": the term is left out of this job's match, and a requirement of that name is off."""
    body = req.body or {}
    job_id, term = str(body.get("job_id") or ""), str(body.get("term") or "").strip()
    if not job_id or not term:
        raise BadRequest("Say which job and which word.", field="term")
    if req.con.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
        raise NotFound("That job is not in Hal's list.")
    req.con.execute("INSERT OR IGNORE INTO cv_keywords_off (job_id, term, at) VALUES (?, ?, ?)",
                    (job_id, term.lower(), db.now()))
    req.con.execute("UPDATE job_requirements SET state = 'off', updated = ? WHERE job_id = ? AND lower(text) = ?",
                    (db.now(), job_id, term.lower()))
    req.con.commit()
    return {"ok": True}

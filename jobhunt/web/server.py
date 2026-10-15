"""Hal Employment: the local web interface to the job pipeline.

Serves a single-page app and a JSON API on 127.0.0.1. A worker thread keeps
the queue full and turns email into to-dos; browser and model work for one
application runs in the background and is reported through /api/state.
Stdlib only, apart from Playwright for form filling.
"""
import base64
import json
import mimetypes
import re
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import (adopt, autofill, browser, contacts, cv, cvbuild, cvscore, db, documents, extension, gpu, pack, prep,
                record, replenish, stats, tasks)
from ..config import CONFIG

STATIC = Path(__file__).resolve().parent / "static"
# Files the interface may show: packs (CVs, letters) and form screenshots.
SERVABLE = (pack.PACKS.resolve(), browser.SHOTS.resolve())
# Statuses that make up an application in progress or sent.
IN_FLIGHT = ("applying", "applied", "assessment", "interview", "offer")
WORKER_PAUSE = 60  # seconds between queue checks when there is nothing to do
WAIT_FOR_YOU = 15 * 60  # seconds Hal waits on a page only the applicant can pass
# What Hal asks for when a page on the way to the form needs the applicant.
ASKS = {"sign-in": "sign in to {site}", "check": "pass the check on {site}",
        "code": "type the code {site} emailed you"}
MAX_STEPS = 12   # pages of one form Hal works through before stopping to ask
FIX_ROUNDS = 2   # times a step is filled again for answers the form refused or questions it added
# Job boards that carry other employers' adverts; the employer's own page is preferred to them.
AGGREGATORS = ("indeed.", "adzuna.", "reed.co", "linkedin.", "glassdoor.", "totaljobs.", "ziprecruiter.",
               "cv-library.", "monster.", "jooble.", "talent.com", "simplyhired.", "jobrapido.", "whatjobs.")
CSRF_HEADER = "X-Hal"

WORKER = {"activity": "starting", "detail": "", "at": db.now(), "paused": False}
ACTIONS: dict[str, dict] = {}
KEPT: dict[str, dict] = {}  # by job, the answers its owner typed, by field, kept when a form is filled again
WATCHING: set[str] = set()  # jobs whose tab Hal is watching for new pages and the site's confirmation
# Most of an action's time is spent waiting on the browser thread, the model, or the applicant.
PAGES: dict[tuple, bytes] = {}  # document pages drawn as images, by file, its change time and page number
POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="action")


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
            (SELECT MAX(at) FROM events e WHERE e.job_id = j.id AND e.kind = 'status') changed
            FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status IN ({','.join('?' * len(IN_FLIGHT))}) OR
                  (j.status IN ('rejected','withdrawn') AND a.job_id IS NOT NULL) OR
                  (j.status IN ('closed','skipped') AND (SELECT detail FROM events e WHERE e.job_id = j.id
                   AND e.kind = 'status' ORDER BY e.at DESC, e.id DESC LIMIT 1 OFFSET 1) LIKE 'applying%')
            ORDER BY CASE j.status WHEN 'offer' THEN 0 WHEN 'interview' THEN 1
              WHEN 'assessment' THEN 2 WHEN 'applying' THEN 3 WHEN 'applied' THEN 4 ELSE 5 END,
              changed DESC""", IN_FLIGHT).fetchall()
    return [{"id": r["id"], "company": r["company"], "title": r["title"], "status": r["status"], "stage": r["stage"],
             "changed": r["changed"], "location": r["location"], "url": r["url"],
             "state": (_app(con, r["id"])["form"] or {}).get("state"),
             "message": (_app(con, r["id"])["form"] or {}).get("message", "")} for r in rows]


def _card(con, r) -> dict:
    s = documents.summary(r)
    return {"id": r["id"], "company": r["company"], "title": r["title"], "location": r["location"],
            "salary": r["salary"], "score": r["score"], "score_notes": r["score_notes"], "url": r["url"],
            "source": r["source"], "posted_at": r["posted_at"], "first_seen": r["first_seen"],
            "status": r["status"], "active": r["active"], "role": s["role"], "flags": s["flags"],
            "fit": s["fit"], "gaps": s["gaps"], "company_brief": s["company"]}


# Background actions ------------------------------------------------------------

def run_action(job_id: str, name: str, fn, *args) -> dict:
    """Starts fn(con, *args) in the background unless an action is already running for the job."""
    current = ACTIONS.get(job_id)
    if current and current["state"] == "running":
        raise RuntimeError(f"Hal is still busy with '{current['name']}' for this job")
    ACTIONS[job_id] = {"name": name, "state": "running", "message": "", "at": db.now()}

    def wrapper():
        con = db.connect()
        try:
            _carry_out(con, job_id, fn, *args)
        finally:
            con.close()

    POOL.submit(wrapper)
    return ACTIONS[job_id]


def _carry_out(con, job_id: str, fn, *args) -> None:
    """Runs an action already recorded as running, and records how it ended."""
    try:
        msg = fn(con, *args) or "done"
        ACTIONS[job_id].update(state="done", message=msg, at=db.now())
    except Exception as e:  # noqa: BLE001 - shown to the user, who can retry
        if not isinstance(e, RuntimeError):
            traceback.print_exc()
        ACTIONS[job_id].update(state="error", message=f"{e}", at=db.now())
        _note(con, job_id, "stuck", f"{e}")


def _say(job_id: str, text: str) -> None:
    """Shows what Hal is doing for a job, on its page and in the queue."""
    if job_id in ACTIONS:
        ACTIONS[job_id]["message"] = text


def _note(con, job_id: str, state: str, message: str) -> None:
    """Records where an application stands, for its page: working, ready, needs, stuck or sent."""
    try:
        form = _app(con, job_id)["form"]
        if form is not None:
            form.update(state=state, message=message)
            _set_app(con, job_id, form=form)
    except Exception:  # noqa: BLE001 - the action's own message still reaches the page
        pass


def _past_walls(con, job, found: dict) -> dict:
    """Waits while the page needs the applicant, then lets Hal carry on to the form.

    A page whose Apply button would send a stored CV at once is not waited on:
    that press is a submission, made when the application is approved.
    """
    job_id = job["id"]
    deadline = time.time() + WAIT_FOR_YOU
    while found["needs"] and found["needs"] != "apply" and not found["sent"]:
        need, wall = found["needs"], found["url"]
        ask = ASKS[need].format(site=found["site"])
        _say(job_id, f"waiting for you to {ask} in {browser.where()}; Hal carries on by itself")
        _note(con, job_id, "needs", f"Hal needs you to {ask} in {browser.where()}. It carries on by itself afterwards.")
        browser.show(job_id)
        db.alert(con, "urgent", f"Hal needs you: {job['company']}", f"{ask[0].upper()}{ask[1:]} in {browser.where()}.", job_id)
        con.commit()
        while True:
            if time.time() > deadline:
                raise RuntimeError(f"gave up waiting for you to {ask}; do that in {browser.where()}, then press \"Carry on from this page\"")
            time.sleep(3)
            now = browser.peek(job_id)
            if now.get("gone"):
                raise browser.TabGone()
            if now.get("busy"):
                continue
            if now["form"] or now["sent"] or now["needs"] != need:
                break
        _say(job_id, "carrying on to the form")
        found = browser.resume(job_id, need, wall)
    return found


def _find_posting(job, avoid: str) -> str | None:
    """Searches the web for the employer's own page for a job whose advert site refuses Hal's browser."""
    words = [w for w in re.findall(r"[a-z0-9]+", job["title"].lower()) if len(w) > 2]
    company = re.sub(r"[^a-z0-9]", "", job["company"].lower())
    try:
        pages, _refused = browser.search([f'"{job["company"]}" "{job["title"]}" apply',
                                          f'{job["company"]} {job["title"]} careers'], pause=2)
    except Exception:  # noqa: BLE001 - no search, no other way in
        return None
    for r in [r for page in pages for r in page]:
        host = urlparse(r["url"]).netloc.lower()
        if avoid in host or any(a in host for a in AGGREGATORS):
            continue
        text = f"{r['title']} {r['snippet']}".lower()
        named = company in re.sub(r"[^a-z0-9]", "", host + text)
        if named and sum(w in text for w in words) >= max(1, len(words) * 2 // 3):
            return r["url"]
    return None


def _reach(con, job, found: dict) -> dict:
    """Gets from the advert to its application form, however the site lays out the way.

    Past pages only the applicant can pass, through pages whose way on is not a
    plain Apply button (the model picks the control), and round a site that
    refuses Hal's browser, by finding the employer's own page for the job.
    """
    job_id = job["id"]
    tried: list[str] = []
    searched = False
    while True:
        if found["needs"] == "check" and not searched and any(a in found["site"] for a in AGGREGATORS):
            # A job board's human check is not Hal's to pass; the employer's own page rarely has one.
            searched = True
            _say(job_id, f"{found['site']} wants a human check; looking for the employer's own page instead")
            url = _find_posting(job, found["site"])
            if url:
                found = browser.open_form(job_id, url)
                continue
        found = _past_walls(con, job, found)
        if found["fields"] or found["sent"] or found["closed"] or found["applied"] or found["needs"]:
            return found
        if not searched and (found["blocked"] or not found["controls"]):
            searched = True
            _say(job_id, f"{found['site']} would not show the advert; looking for the employer's own page")
            url = _find_posting(job, found["site"])
            if url:
                found = browser.open_form(job_id, url)
                continue
        if len(tried) >= 3:
            return found
        _say(job_id, "no Apply button recognised; working out the way to the form")
        left = {**found, "controls": [c for c in found["controls"] if c["name"] not in tried]}
        name = autofill.choose_control(job, left)
        if not name:
            return found
        tried.append(name)
        _say(job_id, f"pressing \"{name}\"")
        found = browser.press_control(job_id, name)


def _answer(con, job, found: dict, step: int, done: list) -> dict:
    """Proposes answers for the fields Hal found and saves them as the application's current step."""
    job_id = job["id"]
    docs = documents.load(con, job)
    if not docs.get("letter") and any(
            (f["kind"] == "file" and re.search(r"cover", f"{f.get('label')} {f.get('context', '')}", re.I))
            or (f["kind"] in ("text", "textarea") and re.search(r"cover letter", f.get("label") or "", re.I))
            for f in found["fields"]):
        _say(job_id, "writing a cover letter, which this form asks for")
        if documents.draft_letter(con, job):
            docs = documents.load(con, job)
    _say(job_id, f"answering {len(found['fields'])} questions" + (f" on step {step}" if step > 1 else ""))
    fields = autofill.propose(job, found["fields"], docs, documents.files(job), con)
    # Answers its owner gave on an earlier run at this form stand.
    for f in fields:
        mine = KEPT.get(job_id, {}).get(f["sig"])
        if mine is not None and f["kind"] != "file":
            f.update(value=mine, source="you", note="", skip=False)
    _answered, waiting = autofill.ask_model(job, fields, docs, con)
    form = {"url": found["url"], "title": found["title"], "captcha": found["captcha"], "fields": fields,
            "model": waiting or "", "filled": None, "submit": None, "step": step, "done": done,
            "final": not found["next"], "state": "working", "message": "", "press": ""}
    _set_app(con, job_id, stage="form", form=form)
    return form


def _shot(job_id: str, name: str = "filled.png") -> Path:
    return browser.SHOTS / pack.slug(job_id) / name


def _enter(con, job, form: dict, only: list | None = None) -> dict:
    """Types the answers into the form and deals with what the form does in return.

    Answers the form would not take are put to the model again with the form's
    own complaint, and questions that appear once others are answered are
    answered too. Returns the last fill's report.
    """
    job_id = job["id"]
    docs = documents.load(con, job)
    fields = form["fields"]
    _say(job_id, "filling in the form")
    out = browser.fill(job_id, only if only is not None else fields, _shot(job_id))
    for _ in range(FIX_ROUNDS):
        fresh = [f for f in out["new"] if f["sig"] not in {x["sig"] for x in fields}]
        retry = {hid: msg for hid, msg in out["errors"].items() if msg.startswith("no option")
                 and any(f["hid"] == hid and f["source"] == "hal" for f in fields)}
        if not fresh and not retry:
            break
        if fresh:
            _say(job_id, f"answering {len(fresh)} more questions the form has just shown")
            fields.extend(autofill.propose(job, fresh, docs, documents.files(job), con))
        autofill.ask_model(job, fields, docs, con, problems=retry)
        out = browser.fill(job_id, fields, _shot(job_id))
    for f in fields:
        f["error"] = out["errors"].get(f["hid"], "")
    form["final"] = not out["next"]
    form["filled"] = {"at": db.now(), "screenshot": out["screenshot"], "errors": len(out["errors"]),
                      "next": out["next"], "password": out["password"]}
    return out


def _open_points(form: dict) -> tuple[list, list, list]:
    """Returns what a person must look at on a step: unanswered required fields, refused answers, prose."""
    fields = form["fields"]
    empty = [f for f in fields if f.get("required") and f.get("value") in (None, "", [], False) and not f.get("skip")
             and f.get("type") != "password"]
    refused = [f for f in fields if f.get("error")]
    prose = [f for f in fields if f.get("written") or "unsure" in (f.get("note") or "")]
    return empty, refused, prose


def _hold(con, job, form: dict) -> str:
    """Stops with the filled form on screen and says what it is waiting for."""
    job_id = job["id"]
    empty, refused, prose = _open_points(form)
    parts = []
    if empty:
        parts.append(f"{len(empty)} required question{'s' if len(empty) > 1 else ''} Hal could not answer")
    if refused:
        parts.append(f"{len(refused)} answer{'s' if len(refused) > 1 else ''} the form would not take")
    if form["filled"].get("password"):
        parts.append("a password to type in the form yourself")
    last = form["final"]
    does = "submit" if last else "move to the next step"
    if browser.mode() == "extension":
        return _hold_in_tab(con, job, form, parts)
    if parts:
        state, message = "needs", f"Filled in, apart from {', and '.join(parts)}. Finish those, then approve to {does}."
    else:
        state = "ready"
        message = (f"Filled in and ready. Check the form in {browser.where()}, then approve to {does}."
                   if last or prose else "")
    form.update(state=state, message=message)
    _set_app(con, job_id, stage="filled", form=form)
    if last or parts:
        db.alert(con, "urgent" if parts else "info",
                 f"{'Needs you' if parts else 'Ready to approve'}: {job['company']}", message, job_id)
        con.commit()
    return message


def _hold_in_tab(con, job, form: dict, parts: list[str]) -> str:
    """Leaves the filled page in the applicant's tab, marked up for checking, and watches for what they do next."""
    job_id = job["id"]
    press = "Submit" if form["final"] else "Next"
    if parts:
        state, message = "needs", f"Filled in, apart from {', and '.join(parts)}. Finish those in the job's tab, then press {press} there."
    else:
        state, message = "ready", f"Filled in. Check the highlighted answers in the job's tab, then press {press} there."
    form.update(state=state, message=message)
    _set_app(con, job_id, stage="filled", form=form)
    _mark(job, form)
    if form["final"] or parts:
        db.alert(con, "urgent" if parts else "info", f"{'Needs you' if parts else 'Ready for you'}: {job['company']}",
                 message, job_id)
        con.commit()
    watch_tab(job_id)
    return message


def _marks(form: dict) -> list[dict]:
    """Says, for each field, how it should look in the page: fine, to check, to answer, or refused."""
    out = []
    for f in form["fields"]:
        value, note = f.get("value"), f.get("error") or f.get("note") or ""
        blank = value in (None, "", [], False)
        if f.get("type") == "password":
            state, note = "answer", "type this yourself"
        elif f.get("error"):
            state = "error"
        elif blank and f.get("required") and not f.get("skip"):
            state = "answer"
        elif blank or f.get("skip"):
            continue
        elif f.get("source") != "you" and (f.get("written") or f.get("source") == "hal"
                                           or re.search(r"unsure|looked wrong|check", note)):
            state = "check"
        else:
            state = "ok"
        out.append({"hid": f["hid"], "kind": f["kind"], "state": state, "label": (f.get("label") or "")[:120],
                    "note": note[:160], "written": bool(f.get("written"))})
    return out


def _mark(job, form: dict) -> None:
    """Outlines the answers in the applicant's tab and shows the panel listing what to look at."""
    try:
        browser.mark(job["id"], _marks(form), {
            "company": job["company"], "title": job["title"], "step": form.get("step", 1), "final": form["final"],
            "docs": sorted(documents.files(job)), "message": form.get("message", "")})
    except RuntimeError:
        pass  # the tab moved on or closed; the next page is marked when it is filled


def _same_answer(f: dict, shown) -> bool:
    """True when the page shows the answer Hal entered, allowing for the ways sites reformat one."""
    def norm(v):
        if isinstance(v, list):
            return sorted(norm(x) for x in v)
        return " ".join(str(v).split()).lower() if v not in (None, False) else ""
    value = f.get("value")
    if f.get("maxlength") and isinstance(value, str):
        value = value[:f["maxlength"]]
    return norm(value) == norm(shown)


def _learn(con, job, form: dict, values: dict) -> int:
    """Takes the answers its owner changed or added in the form itself, and remembers them for the next form."""
    changed = []
    for f in form.get("fields", []):
        if f.get("sig") not in values or f["kind"] == "file" or f.get("type") == "password":
            continue
        shown = values[f["sig"]]
        if shown in (None, "", [], False) or _same_answer(f, shown):
            continue
        f.update(value=shown, source="you", note="changed by you in the form", skip=False)
        changed.append(f)
    if changed:
        autofill.remember(con, changed, job["company"])
        autofill.mark_written(form["fields"])
    return len(changed)


def act_page(con, job_id: str, last: dict | None = None) -> str:
    """Fills what the job's tab shows that is still empty: a new page of the form, or questions just added.

    Args:
        last: What the fields of the page before showed when last seen, by field, to learn the owner's changes from.
    """
    job = _job(con, job_id)
    form = _app(con, job_id)["form"] or {}
    _say(job_id, "reading the page")
    found = browser.read_here(job_id)
    known = {f.get("sig") for f in form.get("fields", [])}
    seen = {f["sig"] for f in found["fields"]}
    if not form.get("fields") or len(known & seen) * 2 < len(known):
        # A different page: what was entered on the one before is kept, then this one is answered.
        step, done = 1, []
        if form.get("fields"):
            _learn(con, job, form, last or {})
            step, done = form.get("step", 1) + 1, [*form.get("done", []), _summary(form)]
        return _drive(con, job, found, step, done)
    # The same page: its owner's changes stand, and only what is empty is filled.
    shown = {f["sig"]: f.get("current") for f in found["fields"]}
    _learn(con, job, form, shown)
    docs = documents.load(con, job)
    fresh = autofill.propose(job, [f for f in found["fields"] if f["sig"] not in known], docs, documents.files(job), con)
    form["fields"].extend(fresh)
    autofill.ask_model(job, form["fields"], docs, con)
    by_sig = {f["sig"]: f for f in found["fields"]}
    empty = [{**f, "hid": by_sig[f["sig"]]["hid"]} for f in form["fields"]
             if f.get("sig") in by_sig and shown.get(f["sig"]) in (None, "", [], False)
             and f.get("value") not in (None, "", [], False) and not f.get("skip")]
    if empty:
        _say(job_id, f"filling in {len(empty)} empty answers")
        out = browser.fill(job_id, empty, _shot(job_id))
        for f in form["fields"]:
            if f.get("sig") in by_sig:
                f["hid"] = by_sig[f["sig"]]["hid"]
                f["error"] = out["errors"].get(f["hid"], f.get("error", "") if f in empty else "")
        form["final"] = not out["next"]
    return _hold(con, job, form)


def act_adopt(con, job_id: str, url: str) -> str:
    """Fills an application the applicant opened in a tab of their own and handed to Hal."""
    job = _job(con, job_id)
    if job["status"] in ("new", "shortlisted", "ready"):
        db.set_status(con, job_id, "applying", "opened by you in your own browser")
        con.commit()
    form = _app(con, job_id)["form"] or {}
    if form.get("fields") and adopt.address(form.get("url", "")) != adopt.address(url):
        # A form Hal filled elsewhere for this job is not this one; this tab starts afresh.
        _set_app(con, job_id, stage="form", form={**form, "fields": [], "step": 1, "done": [], "url": url})
    documents.render(job, documents.load(con, job))
    if (_app(con, job_id)["form"] or {}).get("fields"):
        return act_page(con, job_id)
    _say(job_id, "reading your tab")
    found = browser.read_here(job_id)
    if not found["fields"]:
        # An advert rather than its form: Hal finds the way on, as it does from a link.
        found = browser.resume(job_id, "", url)
    return _drive(con, job, found, 1, [])


def _terminal(con, job, found: dict) -> str | None:
    """Deals with a page that ends the way to the form; returns what to tell the applicant, or None."""
    job_id = job["id"]
    if found["closed"]:
        db.set_status(con, job_id, "closed", f"the advert has gone from {found['site']}")
        db.alert(con, "info", f"No longer open: {job['company']}", job["title"], job_id)
        con.commit()
        browser.close(job_id)
        return "the advert has been taken down; the job is marked closed"
    if found["applied"]:
        mark_applied(con, job, f"{found['site']} shows the application as sent")
        return f"{found['site']} says you have already applied; marked as applied"
    if found["sent"]:
        mark_applied(con, job, f"{found['site']} confirmed the application")
        return "application sent"
    form = {"url": found["url"], "title": found["title"], "captcha": found["captcha"], "fields": [], "model": "",
            "filled": None, "submit": None, "step": 1, "done": [], "final": True, "press": ""}
    if found["needs"] == "apply":
        # Signed in, this site's Apply button sends the stored CV with no form in between.
        form.update(state="ready", press="apply", filled={"at": db.now(), "screenshot": None, "errors": 0, "next": False},
                    message=f"You are signed in to {found['site']}, where Apply sends your stored CV at once. "
                            f"Check the advert in {browser.where()}, then approve to press Apply.")
        _set_app(con, job_id, stage="filled", form=form)
        db.alert(con, "info", f"Ready to approve: {job['company']}", form["message"], job_id)
        con.commit()
        return form["message"]
    if not found["fields"]:
        why = (f"{found['site']} refuses Hal's browser" if found["blocked"]
               else f"no application form found on {found['site']}")
        form.update(state="stuck", message=f"{why}. Open the form in {browser.where()}, then press "
                                           "\"Carry on from this page\".")
        _set_app(con, job_id, stage="form", form=form)
        db.alert(con, "urgent", f"Hal needs you: {job['company']}", form["message"], job_id)
        con.commit()
        browser.show(job_id)
        raise RuntimeError(form["message"])
    return None


def _drive(con, job, found: dict, step: int, done: list) -> str:
    """Works through the application from the page in `found` until it is sent or needs a person.

    Each step is answered and filled in. A step is passed without asking only
    when it is not the last and holds nothing a person should read; the last
    step always stops, with the filled form on screen, for approval.
    """
    job_id = job["id"]
    while True:
        found = _reach(con, job, found)
        ended = _terminal(con, job, found)
        if ended:
            return ended
        form = _answer(con, job, found, step, done)
        _enter(con, job, form)
        empty, refused, prose = _open_points(form)
        if browser.mode() == "extension" or form["final"] or empty or refused or prose \
                or form["filled"].get("password") or step >= MAX_STEPS:
            return _hold(con, job, form)
        found, step, done = _move_on(con, job, form)


def _summary(form: dict) -> dict:
    """Keeps what was entered on a step, for the record once the form has moved on."""
    return {"step": form["step"], "title": form["title"],
            "fields": [{"label": f["label"], "value": f.get("value"), "source": f.get("source"), "kind": f["kind"],
                        "sig": f.get("sig")}
                       for f in form["fields"] if not f.get("skip") and f.get("type") != "password"]}


def _move_on(con, job, form: dict) -> tuple[dict, int, list]:
    """Presses Next and returns the page that follows; a refusal is answered again once."""
    job_id = job["id"]
    docs = documents.load(con, job)
    for attempt in range(2):
        _say(job_id, f"moving on from step {form['step']}")
        moved = browser.next_step(job_id)
        if not moved["stayed"]:
            return moved, form["step"] + 1, [*form["done"], _summary(form)]
        problems = {p["hid"]: p["message"] for p in moved["problems"] if p["hid"]}
        said = "; ".join(dict.fromkeys(p["message"] for p in moved["problems"]))[:300]
        if attempt or not problems:
            for f in form["fields"]:
                f["error"] = problems.get(f["hid"], f.get("error", ""))
            form.update(state="needs", message=f"The form would not move on{': ' + said if said else ''}. "
                                               f"Put it right here or in {browser.where()}, then approve again.")
            _set_app(con, job_id, stage="filled", form=form)
            raise RuntimeError(form["message"])
        _say(job_id, "the form refused some answers; trying again")
        autofill.ask_model(job, form["fields"], docs, con, problems=problems)
        _enter(con, job, form, only=[f for f in form["fields"] if f["hid"] in problems])
    raise AssertionError("unreachable")


def act_auto(con, job_id: str, here: bool = False) -> str:
    """Takes an application as far as it can go without its owner: to the filled form, ready to approve."""
    job = _job(con, job_id)
    if job["status"] in ("new", "shortlisted", "ready"):
        db.set_status(con, job_id, "applying", "chosen from the queue")
        con.commit()
    form = _app(con, job_id)["form"] or {}
    step, done = (form.get("step", 1), form.get("done", [])) if here else (1, [])
    past = [*form.get("fields", []), *(f for s in form.get("done", []) for f in s["fields"])]
    KEPT[job_id] = {**KEPT.get(job_id, {}), **{f["sig"]: f["value"] for f in past if f.get("source") == "you"
                                                 and f.get("sig") and f.get("value") not in (None, "", [])}}
    _set_app(con, job_id, stage="form", form={"url": "", "title": "", "captcha": False, "fields": [], "model": "",
                                              "filled": None, "submit": None, "step": step, "done": done,
                                              "final": False, "state": "working", "message": "", "press": ""})
    # The cover letter's PDF is drawn from the current text before any form asks for it.
    documents.render(job, documents.load(con, job))
    for attempt in range(2):
        try:
            _say(job_id, "reading the page" if here else "opening the application")
            found = browser.read_here(job_id) if here else browser.open_form(job_id, browser.application_url(job))
            return _drive(con, job, found, step, done)
        except browser.TabGone:
            # The window was closed under Hal; the application starts again in a new tab.
            if attempt:
                raise
            here, step, done = False, 1, []
    raise AssertionError("unreachable")


def act_approve(con, job_id: str, edits: list[dict]) -> str:
    """Carries out an approval: enters the owner's changes, then submits, or moves on from a middle step."""
    try:
        return _approve(con, job_id, edits)
    except browser.TabGone:
        # The approval was of a form that is no longer on screen; a fresh one must be seen before it is sent.
        _say(job_id, "the form's window had been closed; filling it in again")
        act_auto(con, job_id)
        raise RuntimeError("the form's window had been closed, so Hal has filled it in again and sent nothing; "
                           "check the form, then approve once more") from None


def _approve(con, job_id: str, edits: list[dict]) -> str:
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    changed = _apply_edits(form, edits)
    _set_app(con, job_id, form=form)
    autofill.remember(con, form["fields"], job["company"])
    if form.get("press") == "apply":
        _say(job_id, "pressing Apply")
        return _drive(con, job, browser.resume(job_id, "apply", form["url"]), 1, [])
    if changed:
        _enter(con, job, form, only=changed)
        refused = [f for f in changed if f.get("error")]
        if refused:
            _hold(con, job, form)
            raise RuntimeError(f"the form would not take {len(refused)} of your changes; see the form")
    if not form["final"]:
        found, step, done = _move_on(con, job, form)
        return _drive(con, job, found, step, done)
    return _send(con, job, form)


def _apply_edits(form: dict, edits: list[dict]) -> list[dict]:
    """Copies the owner's changes onto the stored fields and returns the fields that changed."""
    values = {e["hid"]: e for e in edits}
    changed = []
    for f in form["fields"]:
        e = values.get(f["hid"])
        if e is None:
            continue
        f["skip"] = bool(e.get("skip"))
        if e.get("edited"):
            f.update(value=e.get("value"), source="you", note="")
            changed.append(f)
    autofill.mark_written(form["fields"])
    return changed


def _send(con, job, form: dict) -> str:
    """Presses the form's submit button, on the owner's approval, and sees the application through."""
    job_id = job["id"]
    if browser.mode() == "extension":
        watch_tab(job_id)
        return "approved; press Submit in the job's tab, and Hal marks it applied when the site confirms"
    _say(job_id, "submitting")
    db.log(con, job_id, "note", "approved for sending in the web interface")
    out = browser.submit(job_id, _shot(job_id, "submitted.png"))
    if out.get("code"):
        out = _security_code(con, job, out)
    form["submit"] = {k: out.get(k) for k in ("submitted", "screenshot", "url", "excerpt", "pressed")} | {"at": db.now()}
    _set_app(con, job_id, form=form)
    if out["submitted"]:
        mark_applied(con, job, "submitted by Hal on your approval")
        browser.close(job_id)
        return "application submitted"
    if out["challenge"] or out.get("needs") == "check":
        return _await_confirmation(con, job, form, "pass the site's human check")
    if out.get("needs") == "code":
        return _await_confirmation(con, job, form, "type the code the site emailed you, and submit")
    if out["stayed"]:
        problems = {p["hid"]: p["message"] for p in out["problems"] if p["hid"]}
        for f in form["fields"]:
            f["error"] = problems.get(f["hid"], "")
        said = "; ".join(dict.fromkeys(p["message"] for p in out["problems"]))[:300]
        form.update(state="needs", message=f"The form was not accepted{': ' + said if said else ''}. "
                                           f"Put it right here or in {browser.where()}, then approve again.")
        _set_app(con, job_id, stage="filled", form=form)
        raise RuntimeError(form["message"])
    if out.get("fields"):
        # The button led to another page of questions rather than sending the application.
        return _drive(con, job, out, form["step"] + 1, [*form["done"], _summary(form)])
    return _await_confirmation(con, job, form, "check whether the application went through")


def _security_code(con, job, out: dict) -> dict:
    """Enters the code a site emails to prove the address, read from the job inbox."""
    from .. import gmail
    job_id = job["id"]
    _say(job_id, "the site emailed a security code; reading it from the job inbox")
    since = time.time()
    for _ in range(12):
        code = gmail.security_code(since - 60)
        if code:
            _say(job_id, "entering the security code")
            return browser.enter_code(job_id, code, _shot(job_id, "submitted.png"))
        time.sleep(5)
    return {**out, "needs": "code", "submitted": False}


def _await_confirmation(con, job, form: dict, ask: str) -> str:
    """Waits for the site to confirm an approved application that needs its owner for a last step."""
    job_id = job["id"]
    form.update(state="needs", message=f"Approved, but the site needs you to {ask} in {browser.where()}. "
                                       "Hal marks it applied when the site confirms it.")
    _set_app(con, job_id, stage="filled", form=form)
    _say(job_id, f"waiting for you to {ask} in {browser.where()}")
    browser.show(job_id)
    db.alert(con, "urgent", f"Hal needs you: {job['company']}", f"{ask[0].upper()}{ask[1:]} in {browser.where()}.", job_id)
    con.commit()
    deadline = time.time() + WAIT_FOR_YOU
    while time.time() < deadline:
        time.sleep(4)
        now = browser.peek(job_id)
        if now.get("gone"):
            raise RuntimeError("the form's tab was closed before the site confirmed the application; "
                               "press \"I applied myself\" if it went through")
        # A form's own wording can thank the applicant; only the page after it counts.
        if not now.get("busy") and not now["form"] and (now["sent"] or now["applied"]):
            mark_applied(con, job, "the site confirmed the application")
            browser.close(job_id)
            return "application submitted"
    raise RuntimeError(f"no confirmation seen; if you did {ask} and it went through, press \"I applied myself\"")


def act_draft_rest(con, job_id: str) -> str:
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    answered, waiting = autofill.ask_model(job, form["fields"], documents.load(con, job), con)
    form["model"] = waiting or ""
    _set_app(con, job_id, form=form)
    if waiting:
        raise RuntimeError(waiting)
    return f"Hal answered {answered} more"


def act_refill(con, job_id: str, edits: list[dict]) -> str:
    """Enters the owner's changes into the form without sending anything."""
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    changed = _apply_edits(form, edits)
    _enter(con, job, form, only=changed or None)
    return _hold(con, job, form) or "form filled"


WATCH_PAUSE = 3  # seconds between looks at a tab Hal is watching
WATCH_FOR = 3 * 60 * 60  # seconds a tab is watched after Hal last filled it


def watch_tab(job_id: str) -> None:
    """Watches the job's tab while its owner works through the form: fills each new page or question
    as it appears, and marks the job applied once the site confirms it."""
    if job_id in WATCHING:
        return
    WATCHING.add(job_id)

    def watch():
        con = db.connect()
        seen: dict = {}
        try:
            deadline = time.time() + WATCH_FOR
            while time.time() < deadline:
                time.sleep(WATCH_PAUSE)
                busy = ACTIONS.get(job_id, {}).get("state") == "running"
                if busy:
                    deadline = time.time() + WATCH_FOR
                    continue
                if not _watch_step(con, job_id, seen):
                    return
        except Exception:  # noqa: BLE001 - a watch that fails leaves the form as it is
            traceback.print_exc()
        finally:
            WATCHING.discard(job_id)
            con.close()

    threading.Thread(target=watch, name="watch", daemon=True).start()


def _watch_step(con, job_id: str, seen: dict) -> bool:
    """Takes one look at the job's tab and acts on what changed; returns False when there is nothing left to watch.

    `seen` carries what the page's fields showed at the last look, which is what the
    owner sent once the page has gone.
    """
    if _app(con, job_id)["stage"] == "submitted" or _job(con, job_id)["status"] != "applying":
        return False
    try:
        now = browser.peek(job_id)
    except RuntimeError:
        return True
    if now.get("gone"):
        return False
    if now.get("busy"):
        return True
    form = _app(con, job_id)["form"] or {}
    if not now["form"]:
        # A form's own wording can thank the applicant; only the page after it counts.
        if now["sent"] or now["applied"]:
            job = _job(con, job_id)
            if _learn(con, job, form, seen.get("values", {})):
                _set_app(con, job_id, form=form)
            mark_applied(con, job, "the site confirmed the application")
            return False
        return True
    values = now.get("values", {})
    known = {f.get("sig") for f in form.get("fields", [])}
    moved = len(known & set(values)) * 2 < len(known)
    added = [sig for sig in values if sig not in known]
    if moved or added:
        ACTIONS[job_id] = {"name": "filling the next page" if moved else "answering new questions",
                           "state": "running", "message": "", "at": db.now()}
        _carry_out(con, job_id, act_page, job_id, seen.get("values") if moved else None)
        seen.clear()
        return True
    seen["values"] = values
    return True


BASE_CV = pack.PACKS / "_base-cv"  # where the base CV's preview is drawn


def base_cv() -> dict:
    """The base CV for the builder: as Hal has it, as the website has it, and its preview."""
    pdf = BASE_CV / "Firstname-Lastname-CV.pdf"
    if not pdf.exists():
        cv.render(cv.load(), BASE_CV)
    built = json.loads(cv.BUILT.read_text()) if cv.BUILT.exists() else None
    review = json.loads(REVIEW.read_text()) if REVIEW.exists() else None
    if review:
        review["stale"] = review.get("of") != cvscore.fingerprint(cv.load())
    return {"score": cvscore.score(cv.load(), pdf), "review": review,"cv": cv.for_editing(), "site": cv._apply(cv.origin(), [], keep_hidden=True), "origin": cv.origin_name(),
            "changes": len(cv.overrides()), "pdf": str(pdf), "sources": cvbuild.sources(),
            "options": {**cvbuild.OPTIONS, **((built or {}).get("options") or {})},
            "built": built and {"at": built["built"], "flags": built["flags"],
                                "pdf": str(cvbuild.PREVIEW / "Firstname-Lastname-CV.pdf")}}


REVIEW = cv.DATA / "cv-review.json"  # the model's last read of the base CV


def act_review_cv(con, _job_id: str) -> str:
    """Has the local model read the base CV as a recruiter would."""
    base = cv.load()
    out = cvscore.review(cv.facts_text_of(base))
    if out["note"]:
        raise RuntimeError(out["note"])
    REVIEW.write_text(json.dumps({**out, "at": db.now(), "of": cvscore.fingerprint(base)}) + "\n")
    return f"reviewed: {out['average']} out of 5 on average"


def _match_path(job) -> Path:
    return documents.pack_dir(job) / "match.json"


def act_match(con, job_id: str) -> str:
    """Checks this application's CV against its advert: requirements met, and skills named."""
    job = _job(con, job_id)
    docs = documents.load(con, job)
    mine = documents.current_cv(job, docs)
    out = cvscore.match(job, cv.facts_text_of(mine), docs.get("letter") or "")
    if out["note"] and out["coverage"] is None:
        raise RuntimeError(out["note"])
    path = _match_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**out, "at": db.now()}) + "\n")
    return f"{out['label']}: {out['coverage']}% of what the advert asks for"


def act_build_cv(con, _job_id: str, options: dict) -> str:
    """Builds a draft CV from the applicant's material, for them to read before using it."""
    out = cvbuild.build(options, lambda message: _say("_cvbuild", message))
    n = len(out["flags"])
    return f"your CV is built; {n} thing{'s' if n != 1 else ''} to check" if n else "your CV is built"


def act_rerender_cvs(con, _job_id: str) -> str:
    """Draws again the CVs of applications still to be sent, from the base CV as it now stands."""
    rows = con.execute("SELECT * FROM jobs WHERE status IN ('ready', 'applying')").fetchall()
    made = 0
    for job in rows:
        docs = documents.load(con, job)
        if docs.get("cv"):
            continue  # an application's own edit of its CV stands
        cv.render(documents.tailored(job, docs), documents.pack_dir(job))
        made += 1
    return f"{made} application CVs made again from your base CV"


def act_prep(con, job_id: str) -> str:
    reason = gpu.busy()
    if reason:
        raise RuntimeError(reason)
    prep.build(con, _job(con, job_id))
    return "preparation notes ready"


def act_contacts(con, job_id: str) -> str:
    found = contacts.find(con, _job(con, job_id))
    count = len(found["people"])
    return (f"{count} people suggested" if count else "nobody found to suggest") \
        + (f"; {found['note']}" if found["note"] else "")


def act_mail(con, _job_id: str) -> str:
    from .. import gmail
    from ..notify import notify
    attention = gmail.sync(con)
    made = tasks.sync(con)
    for a in attention:
        notify(f"{a['kind'].title()}: {a['company'] or a['sender']}", a["subject"], urgent=a["kind"] != "human")
    return f"mail synced; {len(attention)} need you, {len(made)} new to-dos"


def mark_applied(con, job, how: str) -> None:
    db.set_status(con, job["id"], "applied", how)
    _set_app(con, job["id"], stage="submitted")
    db.alert(con, "good", f"Applied: {job['company']}", job["title"], job["id"])
    tasks.after_submit(con, job)
    con.commit()


# Worker -------------------------------------------------------------------------

RECORD_EVERY = 10 * 60  # seconds between looks for new or changed notes in the employment record
RECORD_SYNC: dict = {"at": 0.0, "last": None}


def worker() -> None:
    """Keeps the queue full and the to-do list current, forever."""
    con = db.connect()
    while True:
        worked = False
        try:
            tasks.sync(con)
            if WORKER["paused"]:
                WORKER.update(activity="paused", detail="queue filling is paused", at=db.now())
            else:
                worked, msg = replenish.step(
                    con, lambda m: WORKER.update(activity="working", detail=m, at=db.now()))
                WORKER.update(activity="working" if worked else "idle", detail=msg, at=db.now())
            worked = contacts.step(con) or worked
            if time.time() - RECORD_SYNC["at"] >= RECORD_EVERY:
                RECORD_SYNC["at"] = time.time()
                try:
                    RECORD_SYNC["last"] = record.sync()
                except record.RecordUnavailable as e:
                    RECORD_SYNC["last"] = {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - the worker must outlive any one failure
            traceback.print_exc()
            WORKER.update(activity="error", detail=str(e), at=db.now())
        if not worked:
            time.sleep(WORKER_PAUSE)


# HTTP ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "HalEmployment/1"

    def log_message(self, fmt, *args):  # noqa: D102 - keep the journal to errors only
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the extension's browser closed while its request was held open

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode(), "application/json")

    def _host_ok(self) -> bool:
        # Refuses DNS-rebinding requests that reach the port under another name.
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        try:
            if url.path.startswith("/api/"):
                return self._json(self.api_get(url.path[5:], parse_qs(url.query)))
            if url.path == "/files":
                return self.serve_file(parse_qs(url.query).get("path", [""])[0])
            if url.path == "/pages":
                q = parse_qs(url.query)
                return self.serve_page(q.get("path", [""])[0], int(q.get("page", ["1"])[0]))
            m = re.fullmatch(r"/doc/([^/]+)/(cv|letter)", url.path)
            if m:
                return self.serve_doc(unquote(m.group(1)), m.group(2))
            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/").removeprefix("static/")
            target = (STATIC / name).resolve()
            if STATIC not in target.parents or not target.is_file():
                target = STATIC / "index.html"
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except Exception as e:  # noqa: BLE001 - report every failure to the page
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def do_POST(self):  # noqa: N802
        # Browsers cannot add a custom header to a cross-site request without CORS, which is never granted.
        if not self._host_ok() or self.headers.get(CSRF_HEADER) != "1":
            return self._send(403, b"forbidden", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            return self._json(self.api_post(urlparse(self.path).path[5:], body))
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except (RuntimeError, ValueError) as e:
            self._json({"error": str(e)}, 409)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def serve_file(self, raw: str) -> None:
        path = Path(unquote(raw)).resolve()
        if not any(root in path.parents for root in SERVABLE) or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def serve_page(self, raw: str, page: int) -> None:
        """Draws one page of a PDF as an image, so a document reads at the full width of the pane showing it."""
        path = Path(unquote(raw)).resolve()
        if not any(root in path.parents for root in SERVABLE) or path.suffix != ".pdf" or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        key = (str(path), path.stat().st_mtime, page)
        if key not in PAGES:
            try:
                out = subprocess.run(["pdftoppm", "-png", "-r", "160", "-f", str(page), "-l", str(page), str(path)],
                                     capture_output=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return self._send(404, b"no renderer", "text/plain")
            if out.returncode or not out.stdout:
                return self._send(404, b"no such page", "text/plain")
            if len(PAGES) > 64:
                PAGES.clear()
            PAGES[key] = out.stdout
        self._send(200, PAGES[key], "image/png")

    def serve_doc(self, job_id: str, which: str) -> None:
        """Shows a job's CV or cover letter full size, for the panel in the job's tab."""
        con = db.connect()
        try:
            path = documents.files(_job(con, job_id)).get(which)
        finally:
            con.close()
        if not path:
            return self._send(404, f"no {which} for this job yet".encode(), "text/plain")
        self._send(200, Path(path).read_bytes(), "application/pdf")

    # GET routes

    def api_get(self, route: str, q: dict):
        con = db.connect()
        try:
            if route == "state":
                return self.state(con, int(q.get("since", ["0"])[0]))
            if route == "queue":
                rows = con.execute("""SELECT * FROM jobs WHERE status = 'ready' AND active = 1
                                      ORDER BY score DESC, first_seen DESC""").fetchall()
                return {"jobs": [_card(con, r) for r in rows[:replenish.TARGET]],
                        "reserve": max(0, len(rows) - replenish.TARGET), "target": replenish.TARGET}
            if route == "applications":
                return {"jobs": applications(con)}
            if route == "todos":
                rows = con.execute("""SELECT t.*, j.company, j.title job_title FROM todos t
                                      LEFT JOIN jobs j ON j.id = t.job_id
                                      WHERE t.done IS NULL OR t.done >= datetime('now', '-2 days')
                                      ORDER BY t.done IS NOT NULL, t.priority, t.due IS NULL, t.due, t.created DESC""")
                return {"todos": [dict(r) for r in rows]}
            if route == "alerts":
                rows = con.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 100")
                return {"alerts": [dict(r) for r in rows]}
            if route == "stats":
                return stats.summary(con)
            if route == "cv":
                return base_cv()
            if route == "record":
                return {**record.status(), "last_sync": RECORD_SYNC["last"]}
            if route.startswith("job/"):
                return self.job(con, unquote(route[4:]))
            raise LookupError(f"no route {route}")
        finally:
            con.close()

    def state(self, con, since: int) -> dict:
        one = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "queue": replenish.queued(con), "target": replenish.TARGET,
            "applying": one("SELECT COUNT(*) FROM jobs WHERE status = 'applying'"),
            "todos": one("SELECT COUNT(*) FROM todos WHERE done IS NULL"),
            "urgent": one("SELECT COUNT(*) FROM todos WHERE done IS NULL AND priority <= 1"),
            "unread": one("SELECT COUNT(*) FROM alerts WHERE read = 0"),
            "new_alerts": [dict(r) for r in con.execute(
                "SELECT * FROM alerts WHERE id > ? ORDER BY id LIMIT 10", (since,))],
            "last_alert": one("SELECT COALESCE(MAX(id), 0) FROM alerts"),
            "worker": WORKER, "actions": ACTIONS,
            "browser": browser.mode(), "where": browser.where(), "extension": extension.connected(),
        }

    def job(self, con, job_id: str) -> dict:
        r = _job(con, job_id)
        events = con.execute("SELECT at, kind, detail FROM events WHERE job_id = ? ORDER BY at DESC", (job_id,))
        todos = con.execute("SELECT * FROM todos WHERE job_id = ? ORDER BY done IS NOT NULL, priority", (job_id,))
        emails = con.execute("SELECT date, kind, subject, thrid FROM emails WHERE job_id = ? ORDER BY date DESC",
                             (job_id,))
        docs = documents.load(con, r)
        return {"job": {**_card(con, r), "description": r["description"]},
                "summary": documents.summary(r), "docs": docs, "files": documents.files(r),
                "cv": documents.current_cv(r, docs), "cv_edited": bool(docs.get("cv")),
                "match": json.loads(_match_path(r).read_text()) if _match_path(r).exists() else None,
                "cv_score": cvscore.score(documents.current_cv(r, docs), documents.pack_dir(r) / "Firstname-Lastname-CV.pdf")
                            if (documents.pack_dir(r) / "Firstname-Lastname-CV.pdf").exists() else None,
                "application": _app(con, job_id), "events": [dict(e) for e in events],
                "todos": [dict(t) for t in todos],
                "emails": [{**dict(e), "url": tasks.thread_url(e["thrid"])} for e in emails],
                "action": ACTIONS.get(job_id), "apply_url": browser.application_url(r)}

    # POST routes

    def api_post(self, route: str, body: dict):
        if route == "ext/poll":
            return extension.poll()
        if route == "ext/result":
            extension.deliver(body)
            return {"ok": True}
        con = db.connect()
        try:
            if route == "ext/adopt":
                # A tab the applicant opened: the extension registers it under this job, then starts the fill.
                job, made = adopt.adopt(con, body)
                return {"job": job["id"], "made": made, "company": job["company"], "title": job["title"]}
            if route.startswith("job/"):
                quoted, _, action = route[4:].rpartition("/")
                if not quoted or "/" in quoted:
                    raise LookupError(f"no route {route}")
                return self.job_action(con, unquote(quoted), action, body)
            if route == "todos":
                todo = db.add_todo(con, "task", body["title"].strip(), job_id=body.get("job_id"),
                                   due=body.get("due") or None, priority=int(body.get("priority", 5)))
                con.commit()
                return {"id": todo}
            m = re.fullmatch(r"todos/(\d+)/(done|undo|delete)", route)
            if m:
                tid, op = int(m.group(1)), m.group(2)
                if op == "delete":
                    con.execute("DELETE FROM todos WHERE id = ?", (tid,))
                else:
                    con.execute("UPDATE todos SET done = ? WHERE id = ?", (db.now() if op == "done" else None, tid))
                con.commit()
                return {"ok": True}
            if route == "alerts/read":
                con.execute("UPDATE alerts SET read = 1" + ("" if body.get("all") else " WHERE id = ?"),
                            () if body.get("all") else (int(body["id"]),))
                con.commit()
                return {"ok": True}
            if route == "cv":
                changes = cv.save_base(body["cv"])
                cv.render(cv.load(), BASE_CV)
                run_action("_cv", "updating application CVs", act_rerender_cvs, "_cv")
                return {**base_cv(), "saved": len(changes)}
            if route == "cv/source":
                data = base64.b64decode(body["data"]) if body.get("data") else None
                name = cvbuild.add(body.get("name") or "notes", body.get("text") or "", data)
                return {"name": name, "sources": cvbuild.sources()}
            if route == "cv/source/remove":
                cvbuild.remove(body["name"])
                return {"sources": cvbuild.sources()}
            if route == "cv/build":
                return run_action("_cvbuild", "building your CV", act_build_cv, "_cvbuild", body.get("options") or {})
            if route == "record/sync":
                RECORD_SYNC["at"], RECORD_SYNC["last"] = time.time(), record.sync()
                return {**record.status(), "last_sync": RECORD_SYNC["last"]}
            if route == "record/ask":
                question = (body.get("question") or "").strip()
                if not question:
                    raise ValueError("ask a question")
                return record.ask(question)
            if route == "cv/words":
                cvscore.add_words(body.get("words") or [])
                return {"ok": True}
            if route == "cv/review":
                return run_action("_cvreview", "reading your CV as a recruiter", act_review_cv, "_cvreview")
            if route == "cv/origin":
                cv.set_origin(body["origin"])
                cv.render(cv.load(), BASE_CV)
                run_action("_cv", "updating application CVs", act_rerender_cvs, "_cv")
                return base_cv()
            if route == "worker":
                WORKER["paused"] = bool(body.get("paused"))
                return WORKER
            if route == "mail":
                return run_action("_mail", "checking email", act_mail, "_mail")
            if route == "queue/apply":
                # Starts the best jobs in the queue; each stops at its filled form, for approval.
                rows = con.execute("""SELECT id FROM jobs WHERE status = 'ready' AND active = 1
                                      ORDER BY score DESC, first_seen DESC LIMIT ?""",
                                   (max(1, min(int(body.get("count", 10)), replenish.TARGET)),)).fetchall()
                for r in rows:
                    run_action(r["id"], "applying", act_auto, r["id"], False)
                return {"started": [r["id"] for r in rows]}
            raise LookupError(f"no route {route}")
        finally:
            con.close()

    def job_action(self, con, job_id: str, action: str, body: dict):
        job = _job(con, job_id)
        if action == "discard":
            reason = (body.get("reason") or "").strip()
            if ACTIONS.get(job_id, {}).get("state") == "running":
                raise RuntimeError("Hal is still working on this application; discard it when it has finished")
            # Started applications are often found closed only at the form: "closed" keeps that apart from a choice.
            where = "mid-application" if job["status"] == "applying" else "from the queue"
            db.set_status(con, job_id, "closed" if body.get("closed") else "skipped",
                          f"discarded {where}{': ' + reason if reason else ''}")
            # The same advert listed twice must not come straight back into the queue.
            for d in con.execute("""SELECT id FROM jobs WHERE id != ? AND status IN ('new','shortlisted','ready')
                                    AND lower(company) = lower(?) AND lower(title) = lower(?)""",
                                 (job_id, job["company"], job["title"])).fetchall():
                db.set_status(con, d["id"], "skipped", "duplicate of a discarded job")
            con.commit()
            return {"ok": True}
        if action == "apply":
            # Hal goes straight to the form and fills it in; nothing is sent until it is approved.
            return run_action(job_id, "applying", act_auto, job_id, False)
        if action == "restore":
            db.set_status(con, job_id, "ready", "returned to the queue")
            con.commit()
            return {"ok": True}
        if action == "docs":
            docs = {**documents.load(con, job), **body.get("docs", {})}
            documents.save(con, job, docs)
            form = _app(con, job_id)["form"]
            if form and form.get("filled"):
                # The form holds the PDFs as they were when it was filled.
                form["stale"] = True
                _set_app(con, job_id, form=form)
            return {"files": documents.files(job)}
        if action == "docs-reset":
            con.execute("UPDATE applications SET docs = NULL WHERE job_id = ?", (job_id,))
            con.commit()
            return {"docs": documents.drafts(job)}
        if action in ("auto", "form-open"):
            return run_action(job_id, "applying", act_auto, job_id, bool(body.get("here")))
        if action == "adopt":
            return run_action(job_id, "filling your tab", act_adopt, job_id, body.get("url", ""))
        if action == "match":
            return run_action(job_id, "checking your CV against the advert", act_match, job_id)
        if action == "page":
            # Asked from the job's tab: fill what that page shows that is still empty.
            return run_action(job_id, "filling this page", act_page, job_id)
        if action == "form-draft":
            return run_action(job_id, "drafting answers", act_draft_rest, job_id)
        if action == "form-save":
            form = _app(con, job_id)["form"]
            _apply_edits(form, body.get("fields", []))
            _set_app(con, job_id, form=form)
            return {"ok": True}
        if action == "form-fill":
            return run_action(job_id, "filling the form", act_refill, job_id, body.get("fields", []))
        if action == "form-look":
            return browser.look(job_id, _shot(job_id, "look.png"))
        if action == "form-show":
            if not browser.show(job_id):
                raise RuntimeError("the form's tab is closed; press \"Start again\" to open it")
            return {"ok": True}
        if action in ("approve", "submit"):
            # The one way an application is sent: its owner's approval of the filled form.
            app = _app(con, job_id)
            if app["stage"] != "filled":
                raise RuntimeError("Hal has not finished filling the form in yet")
            last = app["form"]["final"]
            return run_action(job_id, "submitting" if last else "moving on", act_approve, job_id,
                              body.get("fields", []))
        if action == "applied":
            mark_applied(con, job, body.get("how") or "submitted by hand")
            return {"ok": True}
        if action == "status":
            db.set_status(con, job_id, body["status"], body.get("note", ""))
            con.commit()
            return {"ok": True}
        if action == "prep":
            return run_action(job_id, "preparing notes", act_prep, job_id)
        if action == "contacts":
            return run_action(job_id, "finding who to message", act_contacts, job_id)
        if action == "note":
            db.log(con, job_id, "note", body["text"])
            con.commit()
            return {"ok": True}
        raise LookupError(f"no action {action}")


def serve(port: int | None = None, open_browser: bool = False) -> None:
    """Runs the interface and its worker until interrupted."""
    port = port or int(CONFIG.get("web_port", 8737))
    threading.Thread(target=worker, name="worker", daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Hal Employment on {url}", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

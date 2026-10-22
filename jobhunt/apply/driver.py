"""Hal's form driver: reaching an application form, filling it page by page, and watching the applicant's tab.

Moved unchanged from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface
and the tests.
"""
import re
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from .. import adopt, autofill, browser, db, documents, pack
from ..apply.queries import AGGREGATORS, _app, _job, _set_app, mark_applied
from ..core.runner import ACTIONS, _carry_out, _checkpoint, _note, _say

WAIT_FOR_YOU = 15 * 60  # seconds Hal waits on a page only the applicant can pass
# What Hal asks for when a page on the way to the form needs the applicant.
ASKS = {"sign-in": "sign in to {site}", "check": "pass the check on {site}",
        "code": "type the code {site} emailed you"}
MAX_STEPS = 12   # pages of one form Hal works through before stopping to ask
FIX_ROUNDS = 2   # times a step is filled again for answers the form refused or questions it added
KEPT: dict[str, dict] = {}  # by job, the answers its owner typed, by field, kept when a form is filled again
WATCHING: set[str] = set()  # jobs whose tab Hal is watching for new pages and the site's confirmation


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
                raise RuntimeError(f"gave up waiting for you to {ask}; do that in {browser.where()}, then press \"Fill in the form\"")
            time.sleep(3)
            _checkpoint(job_id)
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
            _checkpoint(job_id)
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
            _checkpoint(job_id)
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
        _checkpoint(job_id)
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
    _checkpoint(job_id)
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
                                           "\"Fill in the form\".")
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


def act_auto(con, job_id: str, here: bool = False, fresh: bool = False) -> str:
    """Takes an application as far as it can go without its owner: to the filled form, ready to approve.

    Args:
        here: Fill the page the job's tab shows, and never press anything to get to a form.
        fresh: Open the application from the advert even when the job's tab already shows a form.
    """
    job = _job(con, job_id)
    if not here and not fresh:
        # A form already open in the job's tab is the one meant: opening the advert again would close it.
        try:
            here = bool(browser.peek(job_id).get("form"))
        except RuntimeError:
            here = False
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
            if here and not found["fields"]:
                raise RuntimeError(f"Hal cannot see a form in {browser.where()}. Open the form there (press the "
                                   "site's Apply button if it has one), then ask Hal again.")
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
        _checkpoint(job_id)
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
        _checkpoint(job_id)
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

"""Hal's form driver: reaching an application form, filling it page by page, and watching the applicant's tab.

Hal never presses Submit, in any mode. On the last page Hal hands the form over: it says
what to check, brings the form forward and watches for the site's thanks, while the person presses
Submit on the site. On a signed-in job board Hal never presses Apply either, since it may send the
saved CV at once; it waits for the person to press it and follows. A code the site emails is typed
into its box, and the person presses the button next to it.
"""
import re
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from .. import adopt, autofill, browser, db, documents, pack
from ..apply import guard
from ..apply.queries import AGGREGATORS, _app, _job, _set_app, mark_applied
from ..core import settings
from ..core.runner import ACTIONS, _carry_out, _checkpoint, _note, _say

WAIT_FOR_YOU = 15 * 60  # seconds Hal waits on a page only the applicant can pass
# What Hal asks for when a page on the way to the form needs the applicant.
ASKS = {"sign-in": "sign in to {site}", "check": "pass the check on {site}",
        "code": "type the code {site} emailed you",
        "apply": ("press Apply on {site} yourself; here it may send your saved CV straight away. "
                  "Hal fills the form after"),
        # Once Hal has typed an emailed code, the button beside it is the person's to press.
        "code-typed": "press the button next to the code Hal typed in from {site}'s email"}
CODE_TRIES = 12      # looks at the job inbox for an emailed code
CODE_PAUSE = 5       # seconds between those looks
CODE_LOOKBACK = 60   # seconds before the wall a code may have been sent
HANDED_OVER = "Check the marked answers, then press Submit on the employer's site yourself."
LAST_PAGE = "This is the last page. Check it, then press Submit on the site yourself."
MIDDLE_PAGE = "Then press \"Carry on to the next page\" here, or press Next on the site."
APPLY_YOURSELF = ("Press Apply on the site yourself; here it may send your saved CV straight away. "
                  "Hal fills the form after: press \"Fill in the form\" once the form is on screen.")
MAX_STEPS = 12   # pages of one form Hal works through before stopping to ask
FIX_ROUNDS = 2   # times a step is filled again for answers the form refused or questions it added
KEPT: dict[str, dict] = {}  # by job, the answers its owner typed, by field, kept when a form is filled again
WATCHING: set[str] = set()  # jobs whose tab Hal is watching for new pages and the site's confirmation


def _past_walls(con, job, found: dict) -> dict:
    """Waits while the page needs the applicant, then lets Hal carry on to the form.

    A signed-in page whose Apply button may send the saved CV at once ("apply")
    is waited on like a sign-in: the person presses Apply, and Hal follows.
    An emailed code is typed in for the person when `email.codes` is on.
    """
    job_id = job["id"]
    deadline = time.time() + WAIT_FOR_YOU
    while found["needs"] and not found["sent"]:
        need, wall = found["needs"], found["url"]
        typed = need == "code" and _type_code(con, job, found.get("boxes") or [])
        ask = ASKS["code-typed" if typed else need].format(site=found["site"])
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
            if need == "apply":
                # The advert stays until the person presses Apply; then the form, or the site's thanks, shows.
                if now["form"] or now["sent"] or now["applied"] or now.get("url", wall) != wall:
                    break
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
        # Submit-like controls are never offered to the model, and never pressed if it names one anyway.
        page = {"form": bool(found["fields"]), "final": bool(found["fields"])}
        safe = [c for c in found["controls"] if c["name"] not in tried and not guard.never_press(c, page)]
        name = autofill.choose_control(job, {**found, "controls": safe})
        if not name:
            return found
        picked = next((c for c in found["controls"] if c["name"] == name), {"name": name})
        guard.check(job_id, found["url"], picked, page)
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
    notes = browser.notes_for(found["url"], job_id)
    fields = autofill.propose(job, found["fields"], docs, documents.files(job), con, notes=notes)
    # Answers its owner gave on an earlier run at this form stand.
    for f in fields:
        mine = KEPT.get(job_id, {}).get(f["sig"])
        if mine is not None and f["kind"] != "file":
            autofill.by_you(f, mine)
    _answered, waiting = autofill.ask_model(job, fields, docs, con, notes=notes)
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
    notes = browser.notes_for(form.get("url") or "", job_id)
    _say(job_id, "filling in the form")
    out = browser.fill(job_id, only if only is not None else fields, _shot(job_id))
    _note_uses(form, out)
    for _ in range(FIX_ROUNDS):
        fresh = [f for f in out["new"] if f["sig"] not in {x["sig"] for x in fields}]
        retry = {hid: msg for hid, msg in out["errors"].items() if msg.startswith("no option")
                 and any(f["hid"] == hid and f["source"] == "hal" for f in fields)}
        if not fresh and not retry:
            break
        if fresh:
            _say(job_id, f"answering {len(fresh)} more questions the form has just shown")
            fields.extend(autofill.propose(job, fresh, docs, documents.files(job), con, notes=notes))
        autofill.ask_model(job, fields, docs, con, problems=retry, notes=notes)
        _checkpoint(job_id)
        out = browser.fill(job_id, fields, _shot(job_id))
        _note_uses(form, out)
    for f in fields:
        f["error"] = out["errors"].get(f["hid"], "")
    _count_note_answers(form, job_id)
    form["final"] = not out["next"]
    form["filled"] = {"at": db.now(), "screenshot": out["screenshot"], "errors": len(out["errors"]),
                      "next": out["next"], "password": out["password"]}
    return out


def _note_uses(form: dict, out: dict) -> None:
    """Keeps the trace of each site note a fill used: on its field, and in the form's "Notes Hal used on this
    site" list (`form.notes_used`), each line once."""
    lines = out.get("notes_used") or []
    if not lines:
        return
    for f in form.get("fields", []):
        mine = [line for line in lines if line["field"] == (f.get("label") or "")]
        if mine:
            # kept beside what autofill listed (a note that answered the question), each line once
            have = f.get("notes_used") or []
            said = {x.get("what") for x in have}
            f["notes_used"] = [*have, *(line for line in mine if line["what"] not in said)]
    _add_notes_used(form, lines)


def _add_notes_used(form: dict, lines: list[dict]) -> None:
    """Adds trace lines to the form's "Notes Hal used on this site" list, each line once."""
    known = {line.get("what") or line.get("id") for line in form.get("notes_used") or []}
    form["notes_used"] = [*(form.get("notes_used") or []),
                          *(line for line in lines if (line.get("what") or line.get("id")) not in known)]


def _count_note_answers(form: dict, job_id: str) -> None:
    """Counts each site note that answered or skipped a question (autofill lists it on the field), once per
    question, as working when the form took the answer, and adds it to the form's list of notes used."""
    from ..sites import notes

    counted = set(form.get("notes_counted") or [])
    for f in form.get("fields", []):
        for n in f.get("notes_used") or []:
            key = f"{n.get('id')}|{f.get('sig') or f.get('label')}"
            if "what" in n or not n.get("id") or key in counted:
                continue   # an engine's trace line, which the engine counted when it used the note
            notes.used(n["id"], job_id, ok=not f.get("error"), detail=f.get("why") or "")
            counted.add(key)
            _add_notes_used(form, [n])
    form["notes_counted"] = sorted(counted)


def _open_points(form: dict) -> tuple[list, list, list]:
    """Returns what a person must look at on a step: unanswered required fields, refused answers, prose."""
    fields = form["fields"]
    empty = [f for f in fields if f.get("required") and f.get("value") in (None, "", [], False) and not f.get("skip")
             and f.get("type") != "password"]
    refused = [f for f in fields if f.get("error")]
    prose = [f for f in fields if f.get("written") or "unsure" in (f.get("note") or "")]
    return empty, refused, prose


def form_words(form: dict) -> str:
    """What the Form step says about a filled page, the same words in every mode.

    "Hal filled 23 of 25 answers. 2 need you: ... Check them here or in the form." and, on the last page,
    "This is the last page. Check it, then press Submit on the site yourself."
    """
    if not form.get("fields"):
        return form.get("message") or ""
    marks = _marks(form)
    shown = [f for f in form["fields"] if not f.get("skip") and f.get("type") != "password"]
    filled = [f for f in shown if f.get("value") not in (None, "", [], False)]
    words = [f"Hal filled {len(filled)} of {len(shown)} answers."]
    to_check = sum(1 for m in marks if m["state"] != "ok")
    if to_check:
        empty, refused, _ = _open_points(form)
        parts = []
        if empty:
            parts.append(f"{len(empty)} required question{'s' if len(empty) > 1 else ''} Hal could not answer")
        if refused:
            parts.append(f"{len(refused)} answer{'s' if len(refused) > 1 else ''} the form would not take")
        if (form.get("filled") or {}).get("password"):
            parts.append("a password to type in the form yourself")
        detail = f": {', and '.join(parts)}" if parts else ""
        words.append(f"{to_check} need{'s' if to_check == 1 else ''} you{detail}. Check them here or in the form.")
    words.append(LAST_PAGE if form.get("final") else MIDDLE_PAGE if to_check else "")
    return " ".join(w for w in words if w)


def _hold(con, job, form: dict) -> str:
    """Stops with the filled form on screen and says what it is waiting for, in the same words in every mode."""
    job_id = job["id"]
    empty, refused, _ = _open_points(form)
    needs = bool(empty or refused or form["filled"].get("password"))
    state, message = ("needs" if needs else "ready"), form_words(form)
    form.update(state=state, message=message)
    _set_app(con, job_id, stage="filled", form=form)
    if browser.mode() == "extension":
        _mark(job, form)
    if form["final"] or needs:
        db.alert(con, "urgent" if needs else "info",
                 f"{'Needs you' if needs else 'Ready for you'}: {job['company']}", message, job_id)
        con.commit()
    if browser.mode() == "extension":
        # The person may carry on in their own tab; the watch fills each new page and sees the site's thanks.
        # In Hal's window the watch starts at the hand-over (`_hand_over`).
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
        autofill.by_you(f, shown, "changed by you in the form")
        changed.append(f)
    if changed:
        autofill.remember(con, changed, job["company"], job=job)
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
    notes = browser.notes_for(found["url"], job_id)
    new = [f for f in found["fields"] if f["sig"] not in known]
    fresh = autofill.propose(job, new, docs, documents.files(job), con, notes=notes)
    form["fields"].extend(fresh)
    autofill.ask_model(job, form["fields"], docs, con, notes=notes)
    by_sig = {f["sig"]: f for f in found["fields"]}
    empty = [{**f, "hid": by_sig[f["sig"]]["hid"]} for f in form["fields"]
             if f.get("sig") in by_sig and shown.get(f["sig"]) in (None, "", [], False)
             and f.get("value") not in (None, "", [], False) and not f.get("skip")]
    if empty:
        _say(job_id, f"filling in {len(empty)} empty answers")
        out = browser.fill(job_id, empty, _shot(job_id))
        _note_uses(form, out)
        for f in form["fields"]:
            if f.get("sig") in by_sig:
                f["hid"] = by_sig[f["sig"]]["hid"]
                f["error"] = out["errors"].get(f["hid"], f.get("error", "") if f in empty else "")
        _count_note_answers(form, job_id)
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
    when it is not the last, holds nothing a person should read and
    `applying.press_next` is on; the last step always stops, with the filled
    form on screen, for approval. A press the guard refuses stops it too.
    """
    try:
        return _drive_steps(con, job, found, step, done)
    except guard.WouldSubmit as e:
        return _refused(con, job, e)


def _drive_steps(con, job, found: dict, step: int, done: list) -> str:
    while True:
        found = _reach(con, job, found)
        ended = _terminal(con, job, found)
        if ended:
            return ended
        form = _answer(con, job, found, step, done)
        _enter(con, job, form)
        empty, refused, prose = _open_points(form)
        # Both modes press Next on a middle page with nothing to check.
        if form["final"] or empty or refused or prose \
                or form["filled"].get("password") or step >= MAX_STEPS or not settings.get("applying.press_next"):
            return _hold(con, job, form)
        found, step, done = _move_on(con, job, form)


def _refused(con, job, e: guard.WouldSubmit) -> str:
    """Stops where the guard refused a press, and leaves the page to the person."""
    job_id = job["id"]
    form = _app(con, job_id)["form"] or {}
    message = (f"{guard.STOPPED}: {e.reason}. Check the page in {browser.where()}, then press Submit on the "
               "site yourself, or press Next there and Hal fills the next page.")
    form.update(state="needs", message=message)
    _set_app(con, job_id, stage="filled" if form.get("fields") else "form", form=form)
    db.alert(con, "urgent", f"Hal needs you: {job['company']}", message, job_id)
    con.commit()
    watch_tab(job_id)
    return message


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
    """Carries out an approval: enters the owner's changes, then hands the form over on the last page, or
    moves on from a middle step. Never sends anything."""
    try:
        return _approve(con, job_id, edits)
    except browser.TabGone:
        # The approval was of a form that is no longer on screen; a fresh one must be seen before it is sent.
        _say(job_id, "the form's window had been closed; filling it in again")
        act_auto(con, job_id)
        raise RuntimeError("the form's window had been closed, so Hal has filled it in again and sent nothing; "
                           "check the form, then approve once more") from None
    except guard.WouldSubmit as e:
        return _refused(con, _job(con, job_id), e)


def _approve(con, job_id: str, edits: list[dict]) -> str:
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    changed = _apply_edits(form, edits)
    _set_app(con, job_id, form=form)
    autofill.remember(con, form["fields"], job["company"], job=job)
    if form.get("press") == "apply":
        # A form stored before Hal stopped pressing Apply on signed-in sites.
        raise RuntimeError(APPLY_YOURSELF)
    if changed:
        _enter(con, job, form, only=changed)
        refused = [f for f in changed if f.get("error")]
        if refused:
            _hold(con, job, form)
            raise RuntimeError(f"the form would not take {len(refused)} of your changes; see the form")
    if not form["final"]:
        found, step, done = _move_on(con, job, form)
        return _drive(con, job, found, step, done)
    return _hand_over(con, job, form)


def _apply_edits(form: dict, edits: list[dict]) -> list[dict]:
    """Copies the owner's changes onto the stored fields and returns the fields that changed."""
    values = {e["hid"]: e for e in edits}
    changed = []
    for f in form["fields"]:
        e = values.get(f["hid"])
        if e is None:
            continue
        if e.get("edited"):
            autofill.by_you(f, e.get("value"))
            changed.append(f)
        f["skip"] = bool(e.get("skip"))
    autofill.mark_written(form["fields"])
    return changed


def _hand_over(con, job, form: dict) -> str:
    """Hands the filled last page to the person, who presses Submit on the site; Hal watches for the thanks.

    Brings the form forward, marks its answers in the helper's tab, records `handed_over_at` and starts
    the watch, in both modes. Nothing is pressed.
    """
    job_id = job["id"]
    now = db.now()
    form.update(state="ready", message=HANDED_OVER, handed_over=now)
    _set_app(con, job_id, stage="filled", form=form, handed_over_at=now)
    db.log(con, job_id, "note", "handed over to you to press Submit on the employer's site")
    _say(job_id, "ready for you to send")
    if browser.mode() == "extension":
        _mark(job, form)
    browser.show(job_id)
    db.alert(con, "info", f"Ready for you to send: {job['company']}", HANDED_OVER, job_id)
    con.commit()
    watch_tab(job_id)
    return HANDED_OVER


def _type_code(con, job, boxes: list[str]) -> bool:
    """Reads the code a site emailed from the job inbox and types it into its box; True when it did.

    Only with `email.codes` on. The person presses the button next to the code; Hal never does.
    """
    from .. import gmail
    if not settings.get("email.codes"):
        return False
    job_id = job["id"]
    boxes = boxes or browser.peek(job_id).get("code") or []
    if not boxes:
        return False
    _say(job_id, "the site emailed a code; reading it from the job inbox")
    since = time.time()
    for _ in range(CODE_TRIES):
        _checkpoint(job_id)
        code = gmail.security_code(since - CODE_LOOKBACK)
        if code:
            try:
                browser.type_code(job_id, code, boxes)
            except RuntimeError as e:
                _say(job_id, f"could not type the code: {e}")
                return False
            db.log(con, job_id, "note", "typed the code the site emailed; you press the button next to it")
            con.commit()
            return True
        time.sleep(CODE_PAUSE)
    return False


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
WATCH_STOP_S = 30  # one look at a tab takes a few seconds at most; `stop_watching` waits this long in all
_WATCH_STOP = threading.Event()   # set by `stop_watching`; every watch ends at its next look
_WATCHERS: dict[str, threading.Thread] = {}


def stop_watching(timeout: float = WATCH_STOP_S) -> bool:
    """Ends every tab watch and waits for each to close its database connection; True when all ended.

    A watch holds its connection for hours, and Windows cannot move or remove an open database, so whatever
    replaces or removes the data folder (a test's clean-up, a restore) stops the watches first.
    """
    _WATCH_STOP.set()
    try:
        deadline = time.monotonic() + timeout
        for thread in list(_WATCHERS.values()):
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in list(_WATCHERS.values()))
    finally:
        _WATCH_STOP.clear()


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
                if _WATCH_STOP.wait(WATCH_PAUSE):
                    return
                busy = ACTIONS.get(job_id, {}).get("state") == "running"
                if busy:
                    deadline = time.time() + WATCH_FOR
                    continue
                if not _watch_step(con, job_id, seen):
                    return
        except Exception:  # noqa: BLE001 - a watch that fails leaves the form as it is
            traceback.print_exc()
        finally:
            con.close()
            if _WATCHERS.get(job_id) is threading.current_thread():
                del _WATCHERS[job_id]
            WATCHING.discard(job_id)

    thread = threading.Thread(target=watch, name="watch", daemon=True)
    _WATCHERS[job_id] = thread
    thread.start()


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
    if now.get("code") and not seen.get("code"):
        # The site asks for the code it emailed once the person pressed Submit; Hal types it, they press.
        seen["code"] = now.get("code_sigs") or []
        if _type_code(con, _job(con, job_id), now["code"]):
            site = browser._site(now.get("url") or "")
            said = ASKS["code-typed"].format(site=site)
            form.update(state="ready", message=f"{said[0].upper()}{said[1:]}.")
            _set_app(con, job_id, form=form)
    known = {f.get("sig") for f in form.get("fields", [])}
    moved = len(known & set(values)) * 2 < len(known)
    added = [sig for sig in values if sig not in known and sig not in (seen.get("code") or [])]
    if moved or added:
        ACTIONS[job_id] = {"name": "filling the next page" if moved else "answering new questions",
                           "state": "running", "message": "", "at": db.now()}
        _carry_out(con, job_id, act_page, job_id, seen.get("values") if moved else None)
        seen.clear()
        return True
    seen["values"] = values
    return True

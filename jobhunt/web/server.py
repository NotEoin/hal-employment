"""Hal Employment: the local web interface to the job pipeline.

Serves a single-page app and a JSON API on 127.0.0.1. A worker thread keeps
the queue full and turns email into to-dos; browser and model work for one
application runs in the background and is reported through /api/state.
Stdlib only, apart from Playwright for form filling.
"""
import json
import mimetypes
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import autofill, browser, contacts, db, documents, gpu, pack, prep, replenish, tasks
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
        "apply": "press Apply on {site} (you are signed in there, so it may send your stored CV at once)"}
CSRF_HEADER = "X-Hal"

WORKER = {"activity": "starting", "detail": "", "at": db.now(), "paused": False}
ACTIONS: dict[str, dict] = {}
POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="action")


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
            msg = fn(con, *args) or "done"
            ACTIONS[job_id].update(state="done", message=msg, at=db.now())
        except Exception as e:  # noqa: BLE001 - shown to the user, who can retry
            traceback.print_exc()
            ACTIONS[job_id].update(state="error", message=f"{e}", at=db.now())
        finally:
            con.close()

    POOL.submit(wrapper)
    return ACTIONS[job_id]


def _past_walls(con, job, found: dict) -> dict:
    """Waits while the page needs the applicant, then lets Hal carry on to the form."""
    job_id = job["id"]
    deadline = time.time() + WAIT_FOR_YOU
    while found["needs"] and not found["sent"]:
        need, wall = found["needs"], found["url"]
        ask = ASKS[need].format(site=found["site"])
        ACTIONS[job_id]["message"] = f"waiting for you to {ask} in Hal's window; Hal carries on by itself"
        browser.show(job_id)
        db.alert(con, "urgent", f"Hal needs you: {job['company']}", f"{ask[0].upper()}{ask[1:]} in Hal's window.", job_id)
        con.commit()
        while True:
            if time.time() > deadline:
                raise RuntimeError(f"gave up waiting for you to {ask}; do that in Hal's window, then read the page there")
            time.sleep(3)
            now = browser.peek(job_id)
            if now.get("gone"):
                raise RuntimeError("the form's tab was closed; ask Hal to open the form again")
            if now.get("busy"):
                continue
            if now["form"] or (now["url"] != wall or now["sent"] if need == "apply" else now["needs"] != need):
                break
        ACTIONS[job_id]["message"] = "carrying on to the form"
        found = browser.resume(job_id, need, wall)
    return found


def _take_form(con, job, found: dict, step: int) -> str:
    """Proposes answers for the fields Hal found and saves them as the application's current step."""
    job_id = job["id"]
    docs = documents.load(con, job)
    fields = autofill.propose(job, found["fields"], docs, documents.files(job))
    answered, waiting = autofill.ask_model(job, fields, docs)
    form = {"url": found["url"], "title": found["title"], "captcha": found["captcha"], "fields": fields,
            "model": waiting or "", "filled": None, "submit": None, "step": step}
    if found["closed"]:
        raise RuntimeError("the advert says this job is no longer open; discard it from the queue")
    if found["applied"]:
        mark_applied(con, job, f"{found['site']} shows the application as sent")
        return f"{found['site']} says you have already applied; marked as applied"
    if found["sent"]:
        _set_app(con, job_id, stage="form", form=form)
        return "the site says your application was sent; check Hal's window, then press \"I applied myself\""
    if not fields and step > 1:
        # A step with nothing to answer, such as a review page, is shown as it stands.
        out = browser.fill(job_id, [], browser.SHOTS / pack.slug(job_id) / "filled.png")
        form["filled"] = {"at": db.now(), "screenshot": out["screenshot"], "errors": 0, "next": out["next"]}
        _set_app(con, job_id, stage="filled", form=form)
        return f"step {step} has nothing to fill in; check it, then " + ("move on" if out["next"] else "submit")
    _set_app(con, job_id, stage="form", form=form)
    if not fields:
        return "no form found on that page; open it in the browser window, then read it again"
    left = sum(1 for f in fields if f.get("value") in (None, "", []) and not f.get("skip"))
    note = f"; Hal answered {answered}" if answered else (f"; {waiting}" if waiting else "")
    return (f"step {step}: " if step > 1 else "") + f"{len(fields)} fields read, {left} need you{note}"


def act_open_form(con, job_id: str, here: bool) -> str:
    job = _job(con, job_id)
    if here:
        step = (_app(con, job_id)["form"] or {}).get("step", 1)
        return _take_form(con, job, browser.read_here(job_id), step)
    found = browser.open_form(job_id, browser.application_url(job))
    return _take_form(con, job, _past_walls(con, job, found), 1)


def act_next(con, job_id: str) -> str:
    """Moves a form of several pages on to its next step and proposes answers for it."""
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    found = browser.next_step(job_id)
    if found["stayed"]:
        marked = f"; it marks {found['invalid']} fields as wrong" if found["invalid"] else ""
        raise RuntimeError(f"the form did not move on{marked}; check Hal's window")
    return _take_form(con, job, _past_walls(con, job, found), form.get("step", 1) + 1)


def act_draft_rest(con, job_id: str) -> str:
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    answered, waiting = autofill.ask_model(job, form["fields"], documents.load(con, job))
    form["model"] = waiting or ""
    _set_app(con, job_id, form=form)
    if waiting:
        raise RuntimeError(waiting)
    return f"Hal answered {answered} more"


def act_fill(con, job_id: str) -> str:
    form = _app(con, job_id)["form"]
    out = browser.fill(job_id, form["fields"], browser.SHOTS / pack.slug(job_id) / "filled.png")
    for f in form["fields"]:
        f["error"] = out["errors"].get(f["hid"], "")
    form["filled"] = {"at": db.now(), "screenshot": out["screenshot"], "errors": len(out["errors"]),
                      "next": out["next"]}
    _set_app(con, job_id, stage="filled", form=form)
    if out["errors"]:
        return f"form filled with {len(out['errors'])} problems"
    if out["next"] and any(f.get("type") == "password" for f in form["fields"]):
        return "form filled, apart from your password; type it in Hal's window, then press Next step"
    if out["next"]:
        return act_next(con, job_id)
    return "form filled; check it, then submit"


def act_submit(con, job_id: str) -> str:
    job = _job(con, job_id)
    form = _app(con, job_id)["form"]
    out = browser.submit(job_id, browser.SHOTS / pack.slug(job_id) / "submitted.png")
    form["submit"] = {**out, "at": db.now()}
    _set_app(con, job_id, form=form)
    if out["submitted"]:
        mark_applied(con, job, "submitted by Hal from the web interface")
        return "application submitted"
    if out["challenge"]:
        raise RuntimeError("the site wants a captcha solved; finish it in the browser window, then mark it applied")
    if out["invalid"]:
        raise RuntimeError(f"the form reports {out['invalid']} invalid fields; see the screenshot")
    raise RuntimeError("no confirmation seen; check the browser window before marking it applied")


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
        self.wfile.write(body)

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
                rows = con.execute(f"""SELECT j.*, a.stage, a.updated,
                        (SELECT MAX(at) FROM events e WHERE e.job_id = j.id AND e.kind = 'status') changed
                        FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
                        WHERE j.status IN ({','.join('?' * len(IN_FLIGHT))}) OR
                              (j.status IN ('rejected','withdrawn') AND a.job_id IS NOT NULL)
                        ORDER BY CASE j.status WHEN 'offer' THEN 0 WHEN 'interview' THEN 1
                          WHEN 'assessment' THEN 2 WHEN 'applying' THEN 3 WHEN 'applied' THEN 4 ELSE 5 END,
                          changed DESC""", IN_FLIGHT).fetchall()
                return {"jobs": [{"id": r["id"], "company": r["company"], "title": r["title"],
                                  "status": r["status"], "stage": r["stage"], "changed": r["changed"],
                                  "location": r["location"], "url": r["url"]} for r in rows]}
            if route == "todos":
                rows = con.execute("""SELECT t.*, j.company, j.title job_title FROM todos t
                                      LEFT JOIN jobs j ON j.id = t.job_id
                                      WHERE t.done IS NULL OR t.done >= datetime('now', '-2 days')
                                      ORDER BY t.done IS NOT NULL, t.priority, t.due IS NULL, t.due, t.created DESC""")
                return {"todos": [dict(r) for r in rows]}
            if route == "alerts":
                rows = con.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 100")
                return {"alerts": [dict(r) for r in rows]}
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
        }

    def job(self, con, job_id: str) -> dict:
        r = _job(con, job_id)
        events = con.execute("SELECT at, kind, detail FROM events WHERE job_id = ? ORDER BY at DESC", (job_id,))
        todos = con.execute("SELECT * FROM todos WHERE job_id = ? ORDER BY done IS NOT NULL, priority", (job_id,))
        emails = con.execute("SELECT date, kind, subject, thrid FROM emails WHERE job_id = ? ORDER BY date DESC",
                             (job_id,))
        return {"job": {**_card(con, r), "description": r["description"]},
                "summary": documents.summary(r), "docs": documents.load(con, r), "files": documents.files(r),
                "application": _app(con, job_id), "events": [dict(e) for e in events],
                "todos": [dict(t) for t in todos],
                "emails": [{**dict(e), "url": tasks.thread_url(e["thrid"])} for e in emails],
                "action": ACTIONS.get(job_id), "apply_url": browser.application_url(r)}

    # POST routes

    def api_post(self, route: str, body: dict):
        con = db.connect()
        try:
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
            if route == "worker":
                WORKER["paused"] = bool(body.get("paused"))
                return WORKER
            if route == "mail":
                return run_action("_mail", "checking email", act_mail, "_mail")
            raise LookupError(f"no route {route}")
        finally:
            con.close()

    def job_action(self, con, job_id: str, action: str, body: dict):
        job = _job(con, job_id)
        if action == "discard":
            reason = (body.get("reason") or "").strip()
            db.set_status(con, job_id, "skipped", f"discarded from the queue{': ' + reason if reason else ''}")
            # The same advert listed twice must not come straight back into the queue.
            for d in con.execute("""SELECT id FROM jobs WHERE id != ? AND status IN ('new','shortlisted','ready')
                                    AND lower(company) = lower(?) AND lower(title) = lower(?)""",
                                 (job_id, job["company"], job["title"])).fetchall():
                db.set_status(con, d["id"], "skipped", "duplicate of a discarded job")
            con.commit()
            return {"ok": True}
        if action == "apply":
            if job["status"] in ("new", "shortlisted", "ready"):
                db.set_status(con, job_id, "applying", "chosen from the queue")
            _set_app(con, job_id)
            return {"ok": True}
        if action == "restore":
            db.set_status(con, job_id, "ready", "returned to the queue")
            con.commit()
            return {"ok": True}
        if action == "docs":
            docs = {**documents.load(con, job), **body.get("docs", {})}
            if body.get("approve"):
                docs["approved"] = True
            documents.save(con, job, docs)
            if body.get("approve"):
                _set_app(con, job_id, stage="form")
            return {"files": documents.files(job)}
        if action == "docs-reset":
            con.execute("UPDATE applications SET docs = NULL WHERE job_id = ?", (job_id,))
            con.commit()
            return {"docs": documents.drafts(job)}
        if action == "form-open":
            return run_action(job_id, "reading the form", act_open_form, job_id, bool(body.get("here")))
        if action == "form-draft":
            return run_action(job_id, "drafting answers", act_draft_rest, job_id)
        if action == "form-save":
            form = _app(con, job_id)["form"]
            values = {f["hid"]: f for f in body.get("fields", [])}
            for f in form["fields"]:
                if f["hid"] in values:
                    f["value"] = values[f["hid"]].get("value")
                    f["skip"] = bool(values[f["hid"]].get("skip"))
                    if values[f["hid"]].get("edited"):
                        f["source"] = "you"
                        f["note"] = ""
            _set_app(con, job_id, form=form)
            return {"ok": True}
        if action == "form-fill":
            return run_action(job_id, "filling the form", act_fill, job_id)
        if action == "form-next":
            return run_action(job_id, "moving to the next step", act_next, job_id)
        if action == "form-look":
            return browser.look(job_id, browser.SHOTS / pack.slug(job_id) / "look.png")
        if action == "form-show":
            if not browser.show(job_id):
                browser.open_form(job_id, browser.application_url(job))
            return {"ok": True}
        if action == "submit":
            app = _app(con, job_id)
            if app["stage"] != "filled":
                raise RuntimeError("fill the form and check it before submitting")
            if app["form"]["filled"].get("next"):
                raise RuntimeError("this step is not the last; move on to the next step first")
            return run_action(job_id, "submitting", act_submit, job_id)
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

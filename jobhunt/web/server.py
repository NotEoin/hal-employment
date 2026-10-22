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
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import (
    adopt,
    autofill,
    browser,
    cv,
    cvbuild,
    cvscore,
    db,
    documents,
    extension,
    record,
    replenish,
    stats,
    tasks,
)
from ..apply import actions as apply_actions
from ..apply.driver import (
    ASKS,
    FIX_ROUNDS,
    KEPT,
    MAX_STEPS,
    WAIT_FOR_YOU,
    WATCH_FOR,
    WATCH_PAUSE,
    WATCHING,
    _answer,
    _apply_edits,
    _approve,
    _drive,
    _enter,
    _find_posting,
    _hand_over,
    _hold,
    _hold_in_tab,
    _learn,
    _mark,
    _marks,
    _move_on,
    _open_points,
    _past_walls,
    _reach,
    _refused,
    _same_answer,
    _shot,
    _summary,
    _terminal,
    _type_code,
    _watch_step,
    act_adopt,
    act_approve,
    act_auto,
    act_draft_rest,
    act_page,
    act_refill,
    watch_tab,
)
from ..apply.queries import (
    AGGREGATORS,
    IN_FLIGHT,
    _app,
    _card,
    _close_quietly,
    _job,
    _set_app,
    _site,
    _site_of,
    applications,
    effort_notes,
    in_progress_around,
    mark_applied,
)
from ..config import CONFIG
from ..core import naming
from ..core.runner import _RUN, ACTIONS, POOL, Stopped, _carry_out, _checkpoint, _note, _say, run_action, stop_action
from ..core.worker import RECORD_EVERY, RECORD_SYNC, WORKER, WORKER_PAUSE, worker
from ..cvstudio import legacy_actions, pdfread
from ..cvstudio.legacy_actions import (
    _match_path,
    act_build_cv,
    act_match,
    act_rerender_cvs,
    act_review_cv,
    base_cv,
)
from ..docs.legacy_actions import act_contacts, act_mail, act_prep

__all__ = [
    "ACTIONS",
    "AGGREGATORS",
    "ASKS",
    "BASE_CV",  # noqa: F822 - resolved by the module __getattr__ below (PEP 562)
    "CSRF_HEADER",
    "FIX_ROUNDS",
    "IN_FLIGHT",
    "KEPT",
    "MAX_STEPS",
    "PAGES",
    "POOL",
    "RECORD_EVERY",
    "RECORD_SYNC",
    "REVIEW",  # noqa: F822 - resolved by the module __getattr__ below (PEP 562)
    "SERVABLE",  # noqa: F822 - resolved by the module __getattr__ below (PEP 562)
    "STATIC",
    "WAIT_FOR_YOU",
    "WATCHING",
    "WATCH_FOR",
    "WATCH_PAUSE",
    "WORKER",
    "WORKER_PAUSE",
    "_RUN",
    "Handler",
    "Stopped",
    "_answer",
    "_app",
    "_apply_edits",
    "_approve",
    "_card",
    "_carry_out",
    "_checkpoint",
    "_close_quietly",
    "_drive",
    "_enter",
    "_find_posting",
    "_hand_over",
    "_hold",
    "_hold_in_tab",
    "_job",
    "_learn",
    "_mark",
    "_marks",
    "_match_path",
    "_move_on",
    "_note",
    "_open_points",
    "_past_walls",
    "_reach",
    "_refused",
    "_same_answer",
    "_say",
    "_set_app",
    "_shot",
    "_site",
    "_site_of",
    "_summary",
    "_terminal",
    "_type_code",
    "_watch_step",
    "act_adopt",
    "act_approve",
    "act_auto",
    "act_build_cv",
    "act_contacts",
    "act_draft_rest",
    "act_mail",
    "act_match",
    "act_page",
    "act_prep",
    "act_refill",
    "act_rerender_cvs",
    "act_review_cv",
    "applications",
    "autofill",
    "base_cv",
    "browser",
    "db",
    "documents",
    "effort_notes",
    "in_progress_around",
    "mark_applied",
    "run_action",
    "serve",
    "stop_action",
    "tasks",
    "watch_tab",
    "worker",
]


STATIC = Path(__file__).resolve().parent / "static" / "classic"


def _servable() -> tuple[Path, ...]:
    """Folders the interface may show: application folders, old packs, form screenshots and the cache."""
    from ..core import paths

    return tuple(p.resolve() for p in (paths.applications_dir(), paths.legacy_packs_dir(), paths.shots_dir(),
                                        paths.cache_dir()))


def __getattr__(name):  # resolved when used, so they follow the home; tests assign their own (PEP 562)
    if name == "SERVABLE":
        return _servable()
    if name in ("BASE_CV", "REVIEW"):
        return getattr(legacy_actions, name)
    raise AttributeError(name)


CSRF_HEADER = "X-Hal"
NEVER_SUBMIT = "Hal never presses Submit. Press it on the employer's site."


LEGACY_WAIT_S = 2.0          # a converted Classic route answers in today's shape within this
PAGE_SCALE = 160 / 72        # the 160 dpi Classic's document panes were drawn at, as a PDF scale
PAGES_KEPT = 64              # drawn pages kept in memory before the cache starts again


def _page_png(path: Path, page: int) -> bytes:
    """One page of a PDF as PNG bytes, through Hal's PDF reader (`cvstudio.pdfread.page_png`)."""
    try:
        return pdfread.page_png(path, page, PAGE_SCALE)
    except pdfread.Unreadable as e:  # the reader's own error, not an OSError
        raise ValueError(f"cannot read {path.name}: {e}") from e


def _synced(result) -> dict:
    """Classic's reply to "Read it now": the record's status and what the last read found."""
    RECORD_SYNC["at"], RECORD_SYNC["last"] = time.time(), result
    return {**record.status(), "last_sync": RECORD_SYNC["last"]}


def _as_action(kind: str, params: dict, shape, fallback, *, subject: str | None = None) -> dict:
    """Runs a slow Classic route as a persisted action.

    Answers in today's shape (`shape(result)`) when the action ends within `LEGACY_WAIT_S`, else
    `{"started": true}`; Classic shows the result on its next poll. A kind not yet registered (one a
    same-wave package adds) runs as before, through `fallback()`.
    """
    from ..core import runner

    if kind not in (runner.KINDS or runner.load_kinds()):
        return fallback()
    action_id = runner.start(kind, subject=subject, job_id=params.get("job_id"), params=params)
    done = runner.wait(action_id, LEGACY_WAIT_S)
    if done["state"] == "done":
        return shape(done["result"])
    if done["state"] in ("error", "stopped", "interrupted"):
        raise RuntimeError(done["error"] or done["message"] or "Hal could not finish that")
    return {"started": True, "action_id": action_id}


class Gone(LookupError):
    """A route that no longer does what it did: answered 410 with its words."""
PAGES: dict[tuple, bytes] = {}  # document pages drawn as images, by file, its change time and page number


class Handler(BaseHTTPRequestHandler):
    server_version = "HalEmployment/1"

    def log_message(self, fmt, *args):  # noqa: D102 - keep the journal to errors only
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
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
        except Gone as e:
            self._json({"error": str(e)}, 410)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except (RuntimeError, ValueError) as e:
            self._json({"error": str(e)}, 409)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def serve_file(self, raw: str) -> None:
        path = Path(unquote(raw)).resolve()
        if not any(root in path.parents for root in globals().get("SERVABLE") or _servable()) or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def serve_page(self, raw: str, page: int) -> None:
        """Draws one page of a PDF as an image, so a document reads at the full width of the pane showing it."""
        path = Path(unquote(raw)).resolve()
        servable = globals().get("SERVABLE") or _servable()
        if not any(root in path.parents for root in servable) or path.suffix != ".pdf" or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        key = (str(path), path.stat().st_mtime, page)
        if key not in PAGES:
            try:
                png = _page_png(path, page)
            except (OSError, ValueError, IndexError):
                return self._send(404, b"no such page", "text/plain")
            if not png:
                return self._send(404, b"no such page", "text/plain")
            if len(PAGES) > PAGES_KEPT:
                PAGES.clear()
            PAGES[key] = png
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
        # A saved copy carries the name employers see, never the name the file has in its folder.
        name = naming.file_name(which)
        self._send(200, Path(path).read_bytes(), "application/pdf",
                   extra={"Content-Disposition": f'inline; filename="{name}"'})

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
        summary = documents.summary(r)
        summary["flags"] = [*effort_notes(con, r), *summary["flags"]]
        app = _app(con, job_id)
        current = documents.current_cv(r, docs)
        files = documents.files(r)
        return {"job": {**_card(con, r), "description": r["description"]},
                "summary": summary, "docs": docs, "files": files,
                "cv": current, "cv_edited": bool(docs.get("cv")), "cv_base": cv.load(),
                "cv_changes": cv.changes(cv.load(), current),
                # An application Hal had started on before documents were approved counts as approved.
                "docs_approved": docs.get("approved") or (app["updated"] if app["form"] else None),
                "nav": in_progress_around(con, job_id),
                "match": json.loads(_match_path(r).read_text()) if _match_path(r).exists() else None,
                "cv_score": cvscore.score(current, Path(files["cv"])) if files.get("cv") else None,
                "application": app, "events": [dict(e) for e in events],
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
                return _as_action("apply.match_tab", {"page": body}, lambda done: done,
                                  lambda: apply_actions.matched_tab(con, body))
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
                cv.render(cv.load(), legacy_actions.base_cv_dir())
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
                return _as_action("record.sync", {}, _synced, lambda: _synced(record.sync()), subject="_record")
            if route == "record/ask":
                question = (body.get("question") or "").strip()
                if not question:
                    raise ValueError("ask a question")
                return _as_action("record.ask", {"q": question}, lambda done: done, lambda: record.ask(question))
            if route == "cv/words":
                cvscore.add_words(body.get("words") or [])
                return {"ok": True}
            if route == "cv/review":
                return run_action("_cvreview", "reading your CV as a recruiter", act_review_cv, "_cvreview")
            if route == "cv/origin":
                cv.set_origin(body["origin"])
                cv.render(cv.load(), legacy_actions.base_cv_dir())
                run_action("_cv", "updating application CVs", act_rerender_cvs, "_cv")
                return base_cv()
            if route == "worker":
                WORKER["paused"] = bool(body.get("paused"))
                return WORKER
            if route == "mail":
                return run_action("_mail", "checking email", act_mail, "_mail")
            if route == "queue/apply":
                # Moves the best jobs in the queue to In progress, best first. None is opened yet: each waits
                # for its documents to be approved, and its owner goes through them one at a time.
                rows = con.execute("""SELECT id FROM jobs WHERE status = 'ready' AND active = 1
                                      ORDER BY score DESC, first_seen DESC LIMIT ?""",
                                   (max(1, min(int(body.get("count", 10)), replenish.TARGET)),)).fetchall()
                for r in rows:
                    db.set_status(con, r["id"], "applying", "chosen from the queue")
                con.commit()
                return {"started": [r["id"] for r in rows]}
            raise LookupError(f"no route {route}")
        finally:
            con.close()

    def job_action(self, con, job_id: str, action: str, body: dict):
        job = _job(con, job_id)
        if action == "discard":
            # Classic's words and choices map onto the reasons the new screens keep apart.
            reason = ("advert_closed" if body.get("closed") else "too_much_effort" if body.get("effort")
                      else "skipped_for_now")
            apply_actions.discard(con, job_id, reason, (body.get("reason") or "").strip())
            return {"ok": True}
        if action == "apply":
            # The application starts with its documents; Hal opens the form once they are approved.
            if job["status"] in ("new", "shortlisted", "ready"):
                db.set_status(con, job_id, "applying", "chosen from the queue")
                con.commit()
            return {"ok": True}
        if action == "docs-approve":
            docs = {**documents.load(con, job), **body.get("docs", {}), "approved": db.now()}
            documents.save(con, job, docs)
            db.log(con, job_id, "note", "documents approved")
            con.commit()
            return run_action(job_id, "applying", act_auto, job_id, False, replace=True)
        if action == "restore":
            if stop_action(job_id):
                POOL.submit(_close_quietly, job_id)
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
            # Starting again is how a stuck or wandering run is put right, so it never waits for one.
            here = bool(body.get("here"))
            return run_action(job_id, "reading the page" if here else "applying", act_auto, job_id, here,
                              bool(body.get("fresh")), replace=True)
        if action == "stop":
            if stop_action(job_id):
                _note(con, job_id, "stuck", "You stopped Hal. Press \"Fill in the form\" to start again.")
            return {"ok": True}
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
            return _as_action("apply.look", {"job_id": job_id}, lambda done: done,
                              lambda: browser.look(job_id, _shot(job_id, "look.png")), subject=f"{job_id}:look")
        if action == "form-show":
            if not browser.show(job_id):
                raise RuntimeError("the form's tab is closed; press \"Fill in the form\" to open it again")
            return {"ok": True}
        if action == "submit":
            raise Gone(NEVER_SUBMIT)
        if action == "approve":
            # Puts the owner's changes in, moves on from a middle page, and on the last page hands the form
            # over: the owner presses Submit on the employer's site.
            app = _app(con, job_id)
            if app["stage"] != "filled":
                raise RuntimeError("Hal has not finished filling the form in yet")
            last = app["form"]["final"]
            return run_action(job_id, "putting your changes in" if last else "moving on", act_approve, job_id,
                              body.get("fields", []))
        if action == "applied":
            stop_action(job_id)
            mark_applied(con, job, body.get("how") or "submitted by hand", sent_how="you_said")
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

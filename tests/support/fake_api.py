"""A stand-in for Hal's server that the UI tests run the new app against.

It serves `jobhunt/web/static/app/` exactly as the real server will (the page at `/`, its files under
`/app/`, the same Content-Security-Policy with the import map's hash) and answers the few API v2 routes
the shell calls with made-up data, plus a made-up reply for each built page's own GET routes
(`MADE_UP_PAGES`), so the shell's route test draws every page without a 404. Stdlib only, bound to
127.0.0.1 on a free port; nothing here reads the person's data or Hal's database.

The real server replaces this for the pages' own tests; the shell's tests keep using it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

APP_DIR = Path(__file__).resolve().parents[2] / "jobhunt" / "web" / "static" / "app"
IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.S)
SSE_PING_S = 1.0          # often enough that a stopping server ends its streams quickly
SSE_MAX_S = 120           # no test page stays open longer; the stream thread always ends
MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
        ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def csp_header(index_html: str) -> str:
    """The Content-Security-Policy for the app page: scripts only from this server plus the inline import map."""
    m = IMPORT_MAP.search(index_html)
    if not m:
        raise ValueError("index.html has no inline import map")
    digest = base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii")
    return ("default-src 'self'; script-src 'self' 'sha256-" + digest + "'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; frame-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'")


def made_up_session(**over: Any) -> dict:
    """A /session reply for a made-up person on their own computer."""
    s = {"remote": False, "remote_host": None, "developer": False, "version": "2.0.0.dev0", "platform": "linux",
         "setup_done": True, "first_name": "Robin", "simple_menu": False, "theme": "system", "classic": True,
         "install_id": "0f1e2d3c-0000-4000-8000-000000000000",
         "caps": {"open_folder": True, "window_mode": True, "sign_in_here": True, "start_at_login": True}}
    s.update(over)
    return s


def made_up_status(**over: Any) -> dict:
    """A /status reply: Hal idle, with its next two jobs."""
    s = {"busy": False, "text": "", "step_n": 0, "step_total": None, "action_id": None, "why_activity_id": None,
         "next": [{"what": "job search", "at": "13:30"}, {"what": "email check", "at": "12:45"}], "paused": False,
         "problems": 1, "ai_limited_until": None, "reconnecting": False}
    s.update(over)
    return s


MADE_UP_HOME = {"date_words": "Thursday 22 October", "greeting": "Good morning, Robin", "summary": "",
                "needs": [{"id": "n1", "icon": "file", "tone": "hal", "title": "Check a cover letter", "detail": "",
                           "due": None, "act": {"label": "Open", "route": "apps"}}],
                "new_matches": [], "first_search": None,
                "counts": {"jobs": 3, "apps": 1, "inbox": 2, "cv": 4, "memory": 1}}

MADE_UP_SEARCH = [{"label": "Museum Assistant, Riverside Trust", "kind": "Job", "route": "job-demo%3A1"},
                  {"label": "Search for jobs now", "kind": "Action", "action": "/jobs/search"}]

# The Applications board and one application.
MADE_UP_STEPS = [{"name": "Documents", "note": "Approved", "state": "done"},
                 {"name": "Form", "note": "1 answer needs you", "state": "now"},
                 {"name": "Sent", "note": "", "state": "later"}, {"name": "Interview", "note": "", "state": "later"}]
MADE_UP_APP = {
    "id": "demo:1", "number": 1, "step": 2, "steps": MADE_UP_STEPS, "first_time": True, "column": "form",
    "job": {"id": "demo:1", "title": "Museum Assistant", "org": "Riverside Trust", "place": "Leeds", "pay": "",
            "url": "https://jobs.example.org/riverside/1", "status": "applying", "score": 80, "source": "greenhouse"},
    "nav": {"index": 1, "total": 2, "prev": None, "next": "demo:2"},
    "docs": {"state": "approved", "writing": None, "approved_at": "2026-10-22T09:00:00Z",
             "cv": {"changes": [], "preview_url": None, "version_n": None, "look": None, "sent_as": "hal_copy"},
             "letter": {"text": "A made-up letter.", "words": 3, "tone": None, "evidence": None, "checked": None,
                        "preview_url": None},
             "answers": [], "message": {"text": "", "to": "", "to_why": ""}},
    "form": {"state": "needs", "message": "", "filled": 1, "total": 2, "to_check": 1, "site": "jobs.example.org",
             "platform": "", "mode": "helper", "last_page": True, "handed_over": False, "notes_used": [],
             "words": "Hal filled 1 of 2 answers. 1 needs you. Check them here or in the form.",
             "fields": [{"hid": "n", "q": "Full name", "a": "Robin Testwell", "ok": True, "src": "profile",
                         "src_label": "Your profile", "why": "From your profile.", "notes_used": [], "problem": ""},
                        {"hid": "w", "q": "Why this job?", "a": "A made-up answer.", "ok": False, "src": "hal",
                         "src_label": "Written by Hal", "why": "Written for this job by Hal.", "notes_used": [],
                         "problem": ""}]},
    "sent": None, "interview": None}
MADE_UP_BOARD = {"columns": [
    {"key": "ready", "title": "Getting ready", "help": "Waiting for you to approve them.", "items": [
        {"id": "demo:2", "number": 2, "org": "Hillside Library", "title": "Library Assistant",
         "next": "Check and approve documents", "tone": "info", "note": "", "hal_busy": False}]},
    {"key": "form", "title": "Form filled", "help": "Check it and press Submit on the site yourself.", "items": [
        {"id": "demo:1", "number": 1, "org": "Riverside Trust", "title": "Museum Assistant",
         "next": "1 answer needs you", "tone": "warn", "note": "1 of 2 filled", "hal_busy": False}]},
    {"key": "sent", "title": "Sent", "help": "You sent it.", "items": []},
    {"key": "interview", "title": "Interview", "help": "An interview or test is booked.", "items": []},
    {"key": "offer", "title": "Offer", "help": "Offers you have received.", "items": []}],
    "closed": [{"id": "demo:3", "number": 3, "title": "Archive Intern", "org": "Old Mill Museum",
                "why": "You discarded it: too much effort"}]}


# One job, for the Jobs pages.
MADE_UP_JOB = {"id": "demo:1", "title": "Museum Assistant", "org": "Riverside Trust", "place": "Leeds",
               "pattern": "Hybrid", "pay": "\u00a324,000", "pay_min": 24000, "pay_max": 24000, "closes": None,
               "closes_in": "", "posted": "Today", "source": "Adzuna", "platform": None, "kind": "graduate_job",
               "kind_label": "Graduate and entry-level jobs", "hours": None, "contract": None, "is_new": True,
               "saved": False, "status": "new", "score": 72, "band": "good",
               "top_points": [["In Leeds, your first choice of place", 10]], "reqs": None, "eligibility_line": None,
               "application_id": None, "hidden_rule": None}

# Made-up replies for the built pages' own GET routes, by path without the query. A page that lands adds
# the routes it calls when it first draws; a page's own tests run it against the real server instead.
MADE_UP_PAGES = {
    # The setup guide
    "/setup": {"step": "welcome", "done": False, "finished": False, "found": {"usb_install": None, "old_helper": None},
               "steps": [{"id": i, "name": n, "sub": "", "optional": i in ("email", "phone", "updates"),
                          "state": "todo"}
                         for i, n in (("welcome", "Welcome"), ("cv", "Your CV"), ("about", "About you"),
                                      ("goal", "What you're looking for"), ("record", "Your record"),
                                      ("ai", "How Hal thinks"), ("helper", "Browser helper"), ("email", "Email"),
                                      ("phone", "Phone alerts"), ("updates", "Updates"), ("done", "All set"))]},
    # Documents, Messages and Activity
    "/documents": {"apps": [{"id": 1, "number": 1, "job_id": "demo:1", "title": "Museum Assistant",
                             "org": "Riverside Trust", "column": "ready", "column_title": "Getting ready",
                             "kinds": ["CV", "Letter"]}]},
    "/documents/1": {"id": 1, "number": 1, "job_id": "demo:1", "title": "Museum Assistant", "org": "Riverside Trust",
                     "column": "ready", "column_title": "Getting ready",
                     "cv": {"version_n": None, "type": None, "look": None, "sent_as": None},
                     "approved_at": "2026-10-22T10:44:00Z", "folder_words": "0001 Riverside Trust - Museum Assistant",
                     "files": [{"kind": "cv_pdf", "name": "CV (PDF)", "sub": "Robin Example CV.pdf", "file_id": "f1",
                                "file_name": "Robin Example CV.pdf", "has_text": False, "text_kind": None,
                                "acts": ["open", "download"]}],
                     "answers_text": "", "message_text": "", "sent_myself_ok": True},
    "/documents/details": {"rows": [{"label": "Full name", "value": "Robin Example"}]},
    "/messages": {"items": [{"id": "a:1", "kind": "alert", "unread": True, "title": "A made-up alert", "from": "Hal",
                             "at": "2026-10-22T09:00:00Z", "when": "09:00", "text": "Nothing was sent.",
                             "action": None, "tone": "warn", "employer": False}]},
    "/activity": {"days": [{"day": "Today", "items": []}], "more": None},
    # What Hal remembers
    "/memory/waiting": [{"id": "m1", "title": "Lower your distance from 35 to 20 miles?", "area": "Finding jobs",
                         "why": 'You chose "Too far" for 4 jobs this week.', "effect": "A made-up effect.",
                         "evidence": [{"label": "Researcher, Barnsley (22 miles)", "route": "job-demo:1"}]}],
    "/memory/learned": [{"id": "l1", "what": "Travel up to 20 miles, not 35", "from": "A made-up reason",
                         "when": "Today", "area": "Finding jobs", "on": True}],
    # Notes about job sites and saved answers, the two other tabs of What Hal remembers
    "/sites": [{"key": "apply.workable.com/civic-example", "name": "Civic Example", "platform": "Workable",
                "ats": "workable", "account": "", "account_state": None, "seen": "2026-10-22T09:00:00Z",
                "effort": False, "conflicts": [],
                "notes": [{"id": "n1", "kind": "note", "text": "A made-up note about this site.", "scope": "site",
                           "scope_key": "apply.workable.com/civic-example", "where": "Just this site",
                           "origin": "you", "origin_label": "Your note", "when": "2026-10-22", "used": 1,
                           "worked": 1, "on": True, "proposed": False, "may_be_out_of_date": False,
                           "built_in": False, "can_share": True, "said": ""}]}],
    "/answers": [{"id": "1", "q": "Do you hold a full driving licence?", "a": "Yes", "scope": "everywhere",
                  "scope_key": "", "scope_label": "Everywhere", "uses": 2, "kind": "fixed", "needs_choice": False,
                  "auto": False, "choices": [{"scope": "everywhere", "scope_key": "", "label": "Everywhere"},
                                             {"scope": "never", "scope_key": "", "label": "Never reused"}]}],
    # Applications
    "/applications": MADE_UP_BOARD,
    "/applications/1": MADE_UP_APP,
    # Jobs and one job
    "/jobs": {"jobs": [MADE_UP_JOB], "counts": {"matches": 1, "saved": 0, "hidden": 0, "sources_on": 0},
              "filters": {"places": ["Leeds"], "patterns": ["Hybrid", "Remote", "On site"], "kinds": []},
              "cursor": None},
    "/jobs/hidden": {"groups": [], "yours": []},
    "/jobs/demo:1": {"job": {**MADE_UP_JOB, "advert": "A made-up advert.", "advert_saved_at": "2026-10-20T09:00:00Z",
                             "url": None, "apply_url": None, "found_on": "Adzuna"},
                     "points": {"base": 40, "items": [{"rule": "place_first", "label": "In Leeds, your first choice "
                                                       "of place", "points": 10, "quote": ""}], "total": 50},
                     "hidden": None, "reqs": None, "flags": [], "company": [], "eligibility": None,
                     "application_id": None},
    # Settings and its panels
    "/settings/schema": {"groups": [
        {"id": "you", "name": "You", "icon": "user", "items": [
            {"key": "you.name", "kind": "text", "label": "Your name", "help": "", "choices": [], "min": None,
             "max": None, "unit": "", "restart": False, "rescore": False, "advanced": False, "visible_if": "",
             "to": "", "action": "", "disabled_why": ""}]},
        {"id": "goal", "name": "What you're looking for", "icon": "target", "items": []}]},
    "/settings": {"values": {"you.name": "Robin"}, "changed": [], "display": {}, "secrets": {},
                  "store": {"name": "a protected file", "locked": False}, "restart_needed": False},
    "/private": {"questions": []},
    "/prompts": [],
    "/health": {"checks": [], "worst": "ok"},
    "/health/browsers": {"browsers": []},
    "/privacy": {"where": {"data": "", "config": "", "documents": "", "record": ""}, "can_open": True,
                 "leaves": [], "stored": []},
    "/goal/versions": [],
    # Your goal and Where Hal looks, in the shapes of `goal_routes.goal`, `starters` and `registry.listing`
    "/goal": {"values": {"goal.stage": "graduate", "goal.study_status": "finished", "goal.study_end": "",
                         "goal.kinds": ["graduate_job"], "goal.roles": ["museum assistant"], "goal.places": ["Leeds"],
                         "goal.pattern": "any", "goal.min_pay": 22000},
              "rows": [["Roles", "Museum assistant"], ["Places", "Leeds"]], "version": 1, "mode": "steady"},
    "/goal/starters": [{"id": "heritage", "name": "Museums, archives and heritage",
                        "roles": ["museum assistant", "archive assistant"], "interests": ["collections"], "avoid": []}],
    "/sources": {"sources": [
        {"id": "adzuna", "name": "Adzuna", "kind": "aggregator", "kind_label": "Job search site", "area": "",
         "on": True, "last": "2026-10-23T07:00:00Z", "seen": 120, "kept": 9, "ok": True, "error": None,
         "missed": None, "added_by": "builtin", "removable": False, "fix": None},
        {"id": "reed", "name": "Reed", "kind": "aggregator", "kind_label": "Job search site", "area": "", "on": True,
         "last": None, "seen": 0, "kept": 0, "ok": False, "error": None,
         "missed": "Reed has no key, so Hal skips it.", "added_by": "builtin", "removable": False,
         "fix": {"label": "Get a free key", "route": "settings-sites", "secret": "sites.reed_key",
                 "steps": ["Make a free account.", "Copy the key into Hal."], "url": "https://example.org/key"}}],
        "missed": ["Reed has no key, so Hal skips it."], "times": ["07:00", "13:00"]},
    # Your record
    "/record": {"exists": True, "folder_words": "~/Documents/Employment Record", "can_open": True, "groups": [],
                "last_read": None, "files": 0, "notes": 0, "private_folders": ["Private"]},
    # My CV; the parse report is the real server's answer before any CV is added
    "/cv/parse-report": {"version_id": None, "n": None, "found": "", "rows": [], "unsure": 0,
                         "original_preview_url": None, "original": None, "file": None, "from_cv": {}},
    "/cv": {"route": "improve", "route_words": "Help me improve it", "send": "hal_copy",
            "employers_get": "Employers get: Hal's copy of your CV, version 1.",
            "employers_get_sub": "Every change in it was accepted by you.",
            "type": {"id": "humanities_grad", "name": "Humanities and social science graduate"},
            "look": {"id": "classic", "name": "Classic"}, "version": {"id": "v1-demo", "n": 1, "what": "Made up",
                                                                       "when": "2026-10-20T09:00:00Z"},
            "baseline": None, "glance": {"scanner": None, "type_pass": None, "type_total": None, "pending": 0},
            "not_found": [], "name": "Robin Example", "contact": "Leeds"},
    "/cv/versions": [{"id": "v1-demo", "n": 1, "when": "2026-10-20T09:00:00Z", "what": "A made-up CV", "by": "hal",
                      "origin": "generated", "current": True, "baseline": False, "binned": None}],
    "/cv/document": {"schema": 2, "headline": "History graduate", "contact_snapshot": {}, "closing": "",
                     "locale": "en-GB", "extras": {}, "version_id": "v1-demo", "n": 1,
                     "sections": [{"id": "summary", "kind": "summary", "title": "Profile", "text": "A made-up profile.",
                                   "entries": [], "also": "", "visible": True, "locked": False, "max_entries": None}]},
    "/cv/layout": {"sections": [{"id": "summary", "kind": "summary", "title": "Profile", "show": True, "lock": False,
                                 "max": None}], "locked_lines": []},
    # My CV, Type and look and Checks
    "/cv/types": {
        "suggested": {"id": "general_grad", "name": "Graduate, any subject", "why": "You are a graduate."},
        "current": {"id": "humanities_grad", "name": "Humanities and social science graduate", "builtin": True},
        "writing": {"pages": 1, "tone": "plain", "format": "bullets", "results": "where_have",
                    "never_use": ["passionate"]},
        "spelling": "en-GB",
        "types": [{"id": "humanities_grad", "name": "Humanities and social science graduate",
                   "fits": "Recent graduates in history, politics, English, languages and similar",
                   "order": "Profile, Education, Volunteering or experience, Skills", "pages": "Up to 2 pages",
                   "note": "Shows the dissertation and strong modules.", "builtin": True, "tips": [],
                   "based_on": ""}]},
    "/cv/checks": {
        "version_id": "v1-demo",
        "scanner": {"score": 50, "parts": [
            {"id": "readable", "name": "Text can be read", "got": 30, "max": 30, "note": "", "fix_id": None},
            {"id": "headings", "name": "Standard headings", "got": 20, "max": 20, "note": "", "fix_id": None}]},
        "type": {"type_id": "humanities_grad", "name": "Humanities and social science graduate", "passed": 1,
                 "total": 2, "items": [
                     {"id": "profile_evidence", "name": "Profile shows evidence, not qualities", "ok": True,
                      "weight": 1.0, "note": "", "suggestion_id": None},
                     {"id": "result_bullets", "name": "Bullets show a result where there is one", "ok": False,
                      "weight": 2.0, "note": "One bullet has no result.", "suggestion_id": None}]},
        "recruiter": None, "job": None, "jobs": []},
    # My CV, the look gallery
    "/cv/looks": [{"id": "classic", "name": "Classic", "safe": True, "note": "One column, plain headings",
                   "chosen": True},
                  {"id": "modern", "name": "Modern", "safe": True, "note": "One column, coloured headings",
                   "chosen": False},
                  {"id": "compact", "name": "Compact", "safe": True, "note": "Fits more on each page", "chosen": False},
                  {"id": "two", "name": "Two columns", "safe": False, "note": "For printing and networking",
                   "chosen": False}],
    # My CV, Suggestions and Adjust for each job
    "/cv/suggestions": {"state": "ready", "reviewed": 0, "total": 1, "kinds": ["Dates"], "done": [], "message": "",
                        "pending": [{"id": "s-demo", "kind": "Dates", "where": "Education, BA (Hons) History",
                                     "target": "s:education/e:ed-1/start", "title": "Write dates the same way",
                                     "why": "Month names read the same to people and to job-site scanners.",
                                     "trigger": "Check for your CV type: \"Dates in one format\"",
                                     "trigger_type": "check", "evidence": [], "before": "09/2023",
                                     "afters": ["Sep 2023"], "alt": 0, "edited": None, "status": "pending",
                                     "reason": None, "op": "replace", "can_accept_all": True, "ai": False,
                                     "result_version_id": None}]},
    "/cv/adjust": {"level": "little", "setting": "little", "pick_first": False, "forced_no": ""},
}
# Made-up replies for POST routes a page calls as it draws (the goal step asks what Hal understood).
MADE_UP_POSTS = {
    "/api/v2/goal/quiz": {"understood": {"stage": "graduate", "study_status": "finished", "study_end": "",
                                         "kinds": ["graduate_job"], "roles": ["museum assistant"], "avoid": [],
                                         "interests": [], "places": ["Leeds"], "pattern": "any", "min_pay": 22000},
                          "rows": [["Roles", "Museum assistant"], ["Places", "Leeds"]]},
}
# The page a CV preview iframe shows (the real server sends the printed page; every version and look get this).
MADE_UP_PREVIEW = (b'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>CV</title></head>'
                   b"<body><main><h1>Robin Example</h1><p>A made-up profile.</p></main></body></html>")
PREVIEW_POLICY = "default-src 'none'; style-src 'unsafe-inline'"


@dataclass
class FakeState:
    """What the fake server answers; tests change it between page loads."""

    session: dict | None = field(default_factory=made_up_session)
    status: dict = field(default_factory=made_up_status)
    home: dict = field(default_factory=lambda: json.loads(json.dumps(MADE_UP_HOME)))
    search: list = field(default_factory=lambda: list(MADE_UP_SEARCH))
    pages: dict = field(default_factory=lambda: json.loads(json.dumps(MADE_UP_PAGES)))
    posts: list = field(default_factory=list)      # (path, body) of every POST, PUT and DELETE received
    missing: list = field(default_factory=list)    # every address answered 404, so a failing test can name it
    stopping: threading.Event = field(default_factory=threading.Event)


class Handler(BaseHTTPRequestHandler):
    """Serves the app's files and the shell's API routes from the server's FakeState."""

    server: FakeServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # the test output stays readable
        return

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        if status == 404:
            self.server.state.missing.append(self.path)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def _error(self, status: int, code: str, message: str) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    def _static(self, rel: str) -> None:
        path = (APP_DIR / rel).resolve()
        if APP_DIR not in path.parents or not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = MIME.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        extra = {}
        if path.name == "index.html":
            extra["Content-Security-Policy"] = csp_header(path.read_text(encoding="utf-8"))
        self._send(200, path.read_bytes(), ctype, extra)

    def do_GET(self) -> None:
        """Serves files and the GET routes."""
        url = urlparse(self.path)
        if url.path == "/":
            self._static("index.html")
        elif url.path.startswith("/app/"):
            self._static(url.path[len("/app/"):])
        elif url.path.startswith("/api/v2/"):
            self._api_get(url.path[len("/api/v2"):], parse_qs(url.query))
        else:
            self._send(404, b"not found", "text/plain")

    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        st = self.server.state
        if st.session is None:
            self._error(401, "signed_out", "Open Hal Employment from its shortcut.")
        elif path == "/session":
            self._json(st.session)
        elif path == "/status":
            self._json(st.status)
        elif path == "/home":
            self._json(st.home)
        elif unquote(path) in st.pages:
            self._json(st.pages[unquote(path)])
        elif path.startswith("/cv/versions/") and path.endswith("/preview.html"):
            self._send(200, MADE_UP_PREVIEW, "text/html; charset=utf-8", {"Content-Security-Policy": PREVIEW_POLICY})
        elif path == "/search":
            q = (query.get("q") or [""])[0].lower()
            self._json([r for r in st.search if q in r["label"].lower()])
        elif path == "/events":
            self._events()
        else:
            self._error(404, "not_found", "Hal has nothing at that address yet.")

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            end = time.monotonic() + SSE_MAX_S
            while not self.server.state.stopping.is_set() and time.monotonic() < end:
                self.server.state.stopping.wait(SSE_PING_S)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            return

    def _change(self) -> None:
        if self.headers.get("X-Hal") != "1":
            self._error(403, "forbidden", "Changes need the X-Hal header.")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        self.server.state.posts.append((self.path, body))
        if self.server.state.session is None:
            self._error(401, "signed_out", "Open Hal Employment from its shortcut.")
        elif self.command == "POST" and self.path.split("?")[0] in MADE_UP_POSTS:
            self._json(MADE_UP_POSTS[self.path.split("?")[0]])
        else:
            self._json({"ok": True})

    do_POST = do_PUT = do_DELETE = _change


class FakeServer(ThreadingHTTPServer):
    """The fake server on 127.0.0.1 and a free port; use it as a context manager."""

    daemon_threads = True

    def __init__(self, state: FakeState | None = None) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state or FakeState()
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The server's base address, without a trailing slash."""
        return f"http://127.0.0.1:{self.server_address[1]}"

    def __enter__(self) -> FakeServer:
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.state.stopping.set()
        self.shutdown()
        self.server_close()
        if self._thread:
            self._thread.join(timeout=5)

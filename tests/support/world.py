"""A made-up world for tests: a temporary home with one invented applicant, jobs, applications and emails.

`World` points every legacy module at its own folder for as long as it is open: the database, config, inbox,
vaults, packs and private answers. The owner's data, his private answers file and the network are never
reached, so command-line goldens and integration tests can run anywhere. Timestamps are set relative to the
moment the world opens, because the commands filter on "the last day" and "ten days without a reply".

Everything here is invented: the person, the employers and the email addresses. Place names are real UK
cities so that scoring by place has something to read.
"""

from __future__ import annotations

import contextlib
import email.message
import email.utils
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from jobhunt import config, cv, db, gmail, llm, pack, record, replenish, reputation, tasks, vault

PERSON = {
    "first_name": "Robin", "last_name": "Testwell", "full_name": "Robin Testwell",
    "email": "robin.testwell@example.org", "phone": "07700 900123", "city": "Leeds",
}
INBOX_ADDRESS = "robin.jobs@example.org"
# The legacy defaults of config.toml's queue settings, so the goldens do not depend on that file.
QUEUE_SIZE = 10
QUEUE_MIN_SCORE = 45
CONFIG = {"alert_score": 70, "queue_size": QUEUE_SIZE, "queue_min_score": QUEUE_MIN_SCORE,
          "applicant": {"city": PERSON["city"]}}

GRAD = ("We are hiring a graduate software engineer to join our platform team. You will write Python and "
        "TypeScript, review code and learn from senior engineers. A degree in computer science or a related "
        "subject (2:1 or above) is welcome; no commercial experience is needed. Salary 32,000 to 36,000 GBP. "
        "Hybrid, two days a week in the office.")
SENIOR = ("Senior staff engineer with 8+ years of commercial experience leading distributed systems teams. "
          "You will own architecture across the organisation.")
INTERN = ("A paid 12-week graduate internship in software engineering for recent graduates. You will build "
          "internal tools in Python. Graduates are welcome to apply. Paid 2,200 GBP a month.")
PLACEMENT = ("Industrial placement year for current students only: you must be returning to university to "
             "complete your degree in 2027. Software development in C#.")
DATA = ("Junior data analyst. SQL, Python and dashboards for our operations team. Entry level, training "
        "provided. Fully remote within the UK.")
DEFENCE = ("Graduate software engineer working on defence systems. Candidates must hold active SC clearance. "
           "C++ and embedded Linux.")
BOND = ("Graduate developer programme with full training. Leavers within two years must repay the cost of "
        "training (training bond). Placed with our clients.")
SALES = "Graduate sales executive: cold calling and business development, uncapped commission."

# (key, source, company, title, location, description, salary, status, hours since first seen)
JOB_ROWS = [
    ("fernhill-grad", "greenhouse", "Fernhill Analytics", "Graduate Software Engineer", "London", GRAD,
     "32,000 - 36,000 GBP", "new", 3),
    ("tidewater-grad", "lever", "Tidewater Systems", "Junior Software Developer", "Leeds", GRAD, None, "new", 5),
    # under 24 hours: at 30 the digest golden changed with the time of day (the digest counts since yesterday)
    ("brackenridge-data", "hn", "Brackenridge Labs", "Junior Data Analyst", "Remote (UK)", DATA, None, "new", 20),
    ("quillon-intern", "greenhouse", "Quillon Health", "Graduate Software Engineering Internship", "London",
     INTERN, "2,200 GBP a month", "new", 4),
    ("marrowby-placement", "lever", "Marrowby Retail", "Software Placement Year 2027", "London", PLACEMENT,
     None, "new", 6),
    ("ostrava-senior", "greenhouse", "Ostrava Cloud", "Senior Staff Engineer", "London", SENIOR, None, "new", 7),
    ("halberd-defence", "lever", "Halberd Dynamics", "Graduate Software Engineer", "London", DEFENCE, None,
     "new", 8),
    ("kestrel-bond", "workable", "Kestrel Academy", "Graduate Developer Programme", "London", BOND, None,
     "new", 9),
    ("pennant-sales", "workable", "Pennant Media", "Graduate Sales Executive", "London", SALES, None, "new", 10),
    ("larchmont-grad", "greenhouse", "Larchmont Energy", "Graduate Software Engineer", "Edinburgh", GRAD, None,
     "shortlisted", 72),
    ("sorrel-ready", "lever", "Sorrel Payments", "Software Engineer, Graduate", "London", GRAD, None, "ready", 96),
    ("wexford-ready", "greenhouse", "Wexford Maps", "Graduate Backend Engineer", "Cambridge", GRAD, None, "ready",
     120),
    ("ambleside-applied", "lever", "Ambleside Robotics", "Graduate Robotics Software Engineer", "London", GRAD,
     None, "applied", 400),
    ("cobalt-interview", "greenhouse", "Cobalt Ledger", "Graduate Engineer", "London", GRAD, None, "interview", 300),
    ("heron-assessment", "workable", "Heron Freight", "Junior Python Developer", "London", GRAD, None,
     "assessment", 200),
    ("dunmore-rejected", "lever", "Dunmore Books", "Graduate Developer", "Manchester", GRAD, None, "rejected", 500),
    ("gilt-closed", "greenhouse", "Gilt Studios", "Junior Developer", "London", GRAD, None, "closed", 600),
]


def job_id(key: str) -> str:
    """The id of the made-up job with this key, as `<source>:<key>`."""
    row = next(r for r in JOB_ROWS if r[0] == key)
    return f"{row[1]}:{key}"


def jobs() -> list[dict]:
    """Every made-up job as the dict a source returns."""
    return [{"id": f"{src}:{key}", "source": src, "company": company, "title": title, "location": loc,
             "url": f"https://jobs.example.org/{key}", "description": text, "salary": salary,
             "posted_at": None, "board_size": 12}
            for key, src, company, title, loc, text, salary, _status, _hours in JOB_ROWS]


def iso(at: datetime) -> str:
    """`at` in the legacy timestamp form, `2026-10-22T08:15:30+00:00`."""
    return at.astimezone(UTC).isoformat(timespec="seconds")


def seed(con, now: datetime) -> None:
    """Fills an empty legacy database with the made-up jobs, history, emails, to-dos and an application."""
    from jobhunt.score import score

    rows = {r[0]: r for r in JOB_ROWS}
    for job in jobs():
        key = job["id"].split(":", 1)[1]
        status, hours = rows[key][7], rows[key][8]
        seen = iso(now - timedelta(hours=hours))
        s, why = score(job)
        con.execute(
            """INSERT INTO jobs (id, source, company, title, location, url, description, salary, posted_at,
                                 first_seen, last_seen, active, board_size, score, score_notes, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["source"], job["company"], job["title"], job["location"], job["url"],
             job["description"], job["salary"], seen[:10], seen, iso(now), int(status != "closed"),
             job["board_size"], s, why, status))
        if status != "new":
            con.execute("INSERT INTO events (job_id, at, kind, detail) VALUES (?, ?, 'status', ?)",
                        (job["id"], seen, f"{status}: from the made-up world"))
    con.execute("INSERT INTO events (job_id, at, kind, detail) VALUES (?, ?, 'note', ?)",
                (job_id("cobalt-interview"), iso(now - timedelta(hours=20)), "Asked about the team's on-call rota"))
    con.execute("INSERT INTO events (job_id, at, kind, detail) VALUES (?, ?, 'status', ?)",
                (job_id("cobalt-interview"), iso(now - timedelta(hours=10)), "interview: email: Interview invitation"))
    emails = [
        ("1001", "2001", now - timedelta(days=2), "Cobalt Ledger Talent <talent@cobalt.example>",
         "Interview invitation: Graduate Engineer", "interview", "Cobalt Ledger", "Graduate Engineer",
         job_id("cobalt-interview")),
        ("1002", "2002", now - timedelta(days=4), "Heron Freight <jobs@heron.example>",
         "Your online assessment", "assessment", "Heron Freight", "Junior Python Developer",
         job_id("heron-assessment")),
        ("1003", "2003", now - timedelta(days=6), "Dunmore Books <careers@dunmore.example>",
         "Update on your application", "rejected", "Dunmore Books", "Graduate Developer",
         job_id("dunmore-rejected")),
        ("1004", "2004", now - timedelta(days=1), "Sam Example <sam@recruit.example>",
         "Quick question about your application", "human", "Ambleside Robotics", None, None),
        ("1005", "2005", now - timedelta(days=3), "Job alerts <alerts@board.example>",
         "12 new jobs for you", "alert", None, None, None),
    ]
    for msgid, thrid, at, sender, subject, kind, company, role, jid in emails:
        con.execute("""INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, role, job_id,
                                           snippet) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (msgid, thrid, iso(at), sender, INBOX_ADDRESS, subject, kind, company, role, jid,
                     f"{subject}. A made-up message."))
    db.add_todo(con, "interview", "Book the interview with Cobalt Ledger", job_id=job_id("cobalt-interview"),
                priority=1, due=(now + timedelta(days=3)).date().isoformat(), source="email:1001")
    db.add_todo(con, "task", "Update the portfolio page", priority=5, source="world:task")
    con.execute("UPDATE todos SET created = ?", (iso(now - timedelta(days=1)),))
    con.execute("INSERT INTO applications (job_id, stage, docs, updated) VALUES (?, 'documents', ?, ?)",
                (job_id("sorrel-ready"), json.dumps({"letter": "A made-up letter.", "answers": []}),
                 iso(now - timedelta(hours=2))))
    con.commit()


class FakeImap:
    """Stands in for the job inbox: answers the IMAP calls `gmail.sync` makes with made-up messages."""

    def __init__(self, messages: list[email.message.EmailMessage]):
        self.messages = messages
        self.literal = None

    def uid(self, command: str, *args):
        """Every message matches every search; fetches return the messages with Gmail's ids."""
        if command == "SEARCH":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]
        uids = [int(u) for u in args[0].split(b",")]
        if "BODY.PEEK" not in args[1]:
            return "OK", [f"{u} (X-GM-MSGID {9000 + u} UID {u})".encode() for u in uids]
        return "OK", [(f"{u} (X-GM-MSGID {9000 + u} X-GM-THRID {8000 + u} UID {u} BODY[] {{1}}".encode(),
                       self.messages[u - 1].as_bytes()) for u in uids]

    def logout(self):
        """Nothing to close."""


def inbox_messages(now: datetime) -> list[email.message.EmailMessage]:
    """Three new emails for `jobhunt mail`: an interview invitation, a rejection and a job alert."""
    out = []
    for hours, sender, subject, body in [
        (30, "Wexford Maps Recruitment <recruitment@wexford.example>", "Interview invitation - Graduate Backend "
         "Engineer", "Thank you for applying for the Graduate Backend Engineer role. We would like to invite you "
         "to an interview. Please book a slot."),
        (20, "Larchmont Energy <careers@larchmont.example>", "Your application for Graduate Software Engineer",
         "Unfortunately we will not be progressing your application for the Graduate Software Engineer role."),
        (10, "Job alerts <alerts@board.example>", "8 new jobs for you", "New graduate jobs near Leeds."),
    ]:
        m = email.message.EmailMessage()
        m["From"], m["To"], m["Subject"] = sender, INBOX_ADDRESS, subject
        m["Date"] = email.utils.format_datetime(now - timedelta(hours=hours))
        m.set_content(body)
        out.append(m)
    return out


ANALYSIS = {"summary": "Builds internal tools for a made-up company.", "must_have": ["Python"],
            "nice_to_have": ["SQL"], "contacts": [], "questions": ["Why do you want this role?"],
            "employer": None, "eligibility": []}


# The first line of the screen prompt (`ai/prompts/screen.txt`), the part the goal does not fill in.
SCREEN_OPENING = "Decide whether this person could realistically be hired for this job."


def fake_chat_json(system: str, user: str, **kw) -> dict:
    """Canned answers for the pack's prompts; any other prompt finds the model away."""
    canned = {pack.ANALYSE: ANALYSIS,
              pack.FIT: {"matches": [{"requirement": "Python", "evidence": "a made-up project", "strength": "strong"}],
                         "gaps": ["SQL"], "eligible": True, "eligibility_notes": "No conditions."},
              pack.ANSWERS: {"answers": [{"question": "Why do you want this role?", "answer": "A made-up answer."}]},
              pack.VERIFY: {"unsupported": []}}
    if system in canned:
        return json.loads(json.dumps(canned[system]))
    if system.startswith(SCREEN_OPENING):
        return {"eligible": True, "reason": "A made-up reason: the role fits the goal."}
    raise llm.LLMUnavailable("the made-up world has no model for this prompt")


def fake_chat(system: str, user: str, **kw) -> str:
    """Canned prose for the pack's brief, letter and message."""
    if system in (pack.BRIEF, pack.LETTER, pack.MESSAGE):
        return "A short made-up paragraph."
    raise llm.LLMUnavailable("the made-up world has no model for this prompt")


class World:
    """A temporary home holding the made-up database, with every legacy path pointed inside it.

    Use as a context manager. `root` is the folder; `con` an open connection; `now` the moment it opened.
    `HAL_EMPLOYMENT_HOME` points at `root` while it is open, so the new path functions resolve inside it too.
    """

    def __init__(self, *, seeded: bool = True, imap: bool = True):
        self.seeded = seeded
        self.imap = imap
        self.now = datetime.now(UTC).replace(microsecond=0)
        self._stack = contextlib.ExitStack()

    def __enter__(self) -> World:
        stack = self._stack
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-world-"))).resolve()
        self.db_path = self.root / "data" / "jobs.db"
        connect = db.connect

        def world_connect(path: Path = self.db_path):
            return connect(path)

        patches = [
            mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_HOME": str(self.root)}),
            mock.patch.dict(config.CONFIG, CONFIG, clear=True),
            mock.patch.object(db, "DB_PATH", self.db_path),
            mock.patch.object(db, "connect", world_connect),
            mock.patch.object(config, "BOARDS_PATH", self.root / "boards.toml"),
            mock.patch.object(config, "PRIVATE_PATH", self.root / "data" / "no-private.toml"),
            mock.patch.object(config, "private", dict),
            mock.patch.object(tasks, "ADDRESS", INBOX_ADDRESS),
            mock.patch.object(gmail, "ADDRESS", INBOX_ADDRESS),
            mock.patch.object(gmail, "PASSWORD_FILE", self.root / "config" / "no-gmail-password"),
            mock.patch.object(vault, "VAULT", self.root / "no-hal-vault"),
            mock.patch.object(vault, "NOTE", self.root / "no-hal-vault" / "Pipeline.md"),
            mock.patch.object(record, "VAULT", self.root / "no-record"),
            mock.patch.object(pack, "PACKS", self.root / "packs"),
            mock.patch.object(pack, "VAULT", self.root / "no-hal-vault"),
            mock.patch.object(pack, "APPLICATIONS", self.root / "no-hal-vault" / "Applications"),
            mock.patch.object(replenish, "TARGET", QUEUE_SIZE),
            mock.patch.object(replenish, "MIN_SCORE", float(QUEUE_MIN_SCORE)),
            mock.patch.object(llm, "available", lambda: True),
            mock.patch.object(llm, "chat", fake_chat),
            mock.patch.object(llm, "chat_json", fake_chat_json),
            mock.patch.object(cv, "facts_text", lambda: "Robin Testwell. BSc Made-up Studies. Python projects."),
            mock.patch.object(cv, "tailor", lambda text: ({}, None)),
            mock.patch.object(cv, "render", self._render_cv),
            mock.patch.object(record, "context", lambda queries, per_query=3, limit=6000: ""),
            mock.patch.object(reputation, "check", lambda company: ([], "")),
        ]
        if self.imap:
            patches.append(mock.patch.object(gmail, "connect", lambda: FakeImap(inbox_messages(self.now))))
        for p in patches:
            stack.enter_context(p)
        self.con = db.connect()
        stack.callback(self.con.close)
        if self.seeded:
            seed(self.con, self.now)
        return self

    def __exit__(self, *exc) -> None:
        self._stack.close()

    @staticmethod
    def _render_cv(variant: dict, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf = out_dir / "CV.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return pdf

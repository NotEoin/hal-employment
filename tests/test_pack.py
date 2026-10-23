"""Application packs shaped by the CV type: the letter, its checks, the checklist, the requests.

Each test runs in the made-up world as one of the made-up people of `support.people`, with their CV type
(`support.stubs.cv_types`), and a recording fake AI that answers each document prompt the way a model would
be asked to. The fake's letter repeats what the prompt told it to mention, so a test can follow the CV
type from the settings to the finished letter. The helpers here are shared by `test_type_leaks.py`,
`test_prep.py` and `test_contacts.py`.
"""
from __future__ import annotations

import contextlib
import json
import re
import unittest
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest import mock

from support import people
from support.stubs import cv_types
from support.world import World

from jobhunt import cv, db, documents, gpu, llm, pack, record
from jobhunt.core import settings

# One made-up advert per person, in their own field, so the advert itself never holds another career's words.
ADVERTS = {
    "humanities_grad": ("Collections Assistant", "Riverside Heritage Trust",
                        "Help catalogue the Trust's collections and answer enquiries from visitors. You will "
                        "write short reports and research object histories. Selection includes a written "
                        "exercise and an assessment centre. Contact Ada Penhallow, Head of Collections."),
    "technical_grad": ("Graduate Developer", "Brightwater Tools",
                       "Build internal tools in Python with a small team. Selection includes a technical test "
                       "and a video interview."),
    "career_changer": ("Project Coordinator", "Harbourside Housing",
                       "Keep projects on time, run meetings and track budgets. Selection includes a "
                       "presentation and competency questions."),
}
FACTS = {
    "humanities_grad": "Marnie Quillfeather. BA History and Imaginary Studies. Dissertation on river trade "
                       "in an invented port town. Volunteer guide at a made-up heritage centre, 2024 to 2026.",
    "technical_grad": "Tobias Wrenfield. BSc Software Studies. Built a made-up timetable planner in Python. "
                      "Part-time help desk work, 2024 to 2026.",
    "career_changer": "Priya Ashdown-Vell. Shift supervisor at a made-up warehouse, 2019 to 2026: planned rotas "
                      "for 30 staff and ran weekly meetings.",
}
RECORD = "[Volunteering/guide.md > Role]\nA made-up note about guiding visitors."
OPENINGS = {   # the first words of each document prompt, to tell the fake which one it was asked
    "analyse": "You analyse a job advert", "brief": "Summarise what this company does",
    "fit": "Match a candidate's evidence", "letter": "Write a cover letter", "message": "Write a short message",
    "answers": "Answer these application questions", "verify": "Check drafts written",
    "prep": "Prepare ", "contacts_pick": "You choose who",
}
WAIT_S = 10.0
POLL_S = 0.05


def prompt_id(system: str) -> str:
    """Which document prompt `system` is, by its opening words."""
    for pid, opening in OPENINGS.items():
        if system.startswith(opening):
            return pid
    raise AssertionError(f"an unexpected prompt: {system[:60]!r}")


@dataclass
class FakeWriter:
    """A fake AI for the document prompts that keeps every request and reply."""

    letter: str = ""                                   # a fixed letter instead of the echoing one
    log: list[dict] = field(default_factory=list)

    def reply(self, system: str, user: str) -> object:
        pid = prompt_id(system)
        if pid == "analyse":
            return {"summary": "Made-up work for a made-up employer.", "must_have": ["Writing reports"],
                    "nice_to_have": ["Spreadsheets"], "contacts": [], "questions": ["Why this role?"],
                    "employer": None, "eligibility": []}
        if pid == "fit":
            return {"matches": [{"requirement": "Writing reports", "evidence": "a made-up piece of work",
                                 "strength": "strong"}], "gaps": ["Spreadsheets"], "eligible": True,
                    "eligibility_notes": "No conditions."}
        if pid == "letter":
            if self.letter:
                return self.letter
            mention = re.search(r"Mention\s+(.*?)\.", system, re.S)
            return f"I would bring {' '.join(mention.group(1).split()) if mention else 'my evidence'} to the role."
        if pid == "answers":
            return {"answers": [{"question": "Why this role?", "answer": "It matches the work I have done."}]}
        if pid == "verify":
            return {"unsupported": []}
        if pid == "contacts_pick":
            return {"picks": [{"n": 1, "why": "Leads the team that is hiring.", "sure": True}]}
        if pid == "prep":
            return "## What they will test\n\nThe written work the advert names."
        return "A short made-up paragraph."

    def chat(self, system: str, user: str, **kw) -> str:
        out = self.reply(system, user)
        text = out if isinstance(out, str) else json.dumps(out)
        self.log.append({"id": prompt_id(system), "system": system, "user": user, "reply": text})
        return text

    def chat_json(self, system: str, user: str, **kw) -> dict:
        out = self.reply(system, user)
        self.log.append({"id": prompt_id(system), "system": system, "user": user, "reply": json.dumps(out)})
        return json.loads(json.dumps(out))

    def asked(self, pid: str) -> list[dict]:
        """Every request made with prompt `pid`."""
        return [e for e in self.log if e["id"] == pid]


def add_job(con, key: str, *, status: str = "shortlisted", eligibility: dict | None = None) -> dict:
    """Adds the made-up advert for person `key` to the world and returns its row."""
    title, company, text = ADVERTS[key]
    job_id = f"test:{key}"
    con.execute("""INSERT INTO jobs (id, source, company, title, location, url, description, first_seen,
                   last_seen, status, eligibility) VALUES (?, 'test', ?, ?, 'Made-up Town', ?, ?, ?, ?, ?, ?)""",
                (job_id, company, title, f"https://jobs.example.org/{key}", text, db.now(), db.now(), status,
                 json.dumps(eligibility) if eligibility else None))
    con.commit()
    return dict(con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


@contextlib.contextmanager
def as_applicant(person: people.Person, writer: FakeWriter | None = None) -> Iterator[SimpleNamespace]:
    """A made-up world where `person`, with their CV type and their own made-up CV, has the fake writer."""
    writer = writer or FakeWriter()
    with World() as w, people.as_person(person), cv_types.using(person.key), contextlib.ExitStack() as stack:
        for p in (mock.patch.object(llm, "chat", writer.chat), mock.patch.object(llm, "chat_json", writer.chat_json),
                  mock.patch.object(cv, "facts_text", lambda: FACTS[person.key]),
                  mock.patch.object(record, "context", lambda queries, per_query=3, limit=6000: RECORD)):
            stack.enter_context(p)
        yield SimpleNamespace(world=w, con=w.con, writer=writer, person=person)


def section(md: str, heading: str) -> str:
    """One `## heading` section of a pack."""
    found = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return found.group(1) if found else ""


class Letter(unittest.TestCase):
    def test_the_letter_follows_the_types_evidence_and_your_tone_and_length(self):
        with as_applicant(people.HUMANITIES) as a:
            settings.set("cvdocs.letter_tone", "formal", by="you")
            settings.set("cvdocs.letter_words", 150, by="you")
            settings.set("cvdocs.signoff", "Yours sincerely", by="you")
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
            shown = pack.read_letter_notes(job)
        system = a.writer.asked("letter")[0]["system"]
        self.assertIn("About 150 words", system)
        self.assertIn("The tone is formal", system)
        self.assertIn("Mention\nthe dissertation and the most relevant volunteering or work", system)
        self.assertIn("dissertation, strong modules, volunteering or part-time work", system)
        self.assertIn('"passionate"', system, "the never-use list reaches the letter")
        letter_md = section(md, "Cover letter")
        self.assertIn("the dissertation and the most relevant volunteering or work", letter_md)
        self.assertIn("Yours sincerely,\nMarnie Quillfeather", letter_md)
        notes = section(md, "Letter notes")
        self.assertIn("- Tone: Formal (your setting)", notes)
        self.assertRegex(notes, r"- Length: \d+ words \(you prefer about 150\)")
        self.assertIn("- Evidence: Volunteering/guide.md > Role", notes, "the record passage used is the evidence")
        self.assertIn(pack.CHECKED_CLEAN, notes)
        self.assertEqual(shown["tone"], "Formal (your setting)")
        self.assertEqual(shown["checked"], pack.CHECKED_CLEAN)

    def test_your_own_rewritten_answers_shape_the_voice_but_never_a_private_one(self):
        with as_applicant(people.HUMANITIES) as a:
            for label, answer in (("Why do you want to work in heritage?", "Old maps made me curious early on."),
                                  ("Do you consider yourself to have a disability?", "A made-up private line.")):
                a.con.execute("INSERT INTO answers2 (question, label, answer, scope, source, updated) "
                              "VALUES (?, ?, ?, 'never', 'you', ?)", (label, label, json.dumps(answer), db.now()))
            a.con.commit()
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
            settings.set("cvdocs.voice", False, by="you")
            off = pack.prompt("letter")
        system = a.writer.asked("letter")[0]["system"]
        self.assertIn("Old maps made me curious early on.", system)
        self.assertNotIn("A made-up private line.", json.dumps(a.writer.log))
        self.assertNotIn("Old maps", off, "Match my own writing switched off leaves the examples out")

    def test_a_letter_drafted_at_form_time_is_checked_like_any_other(self):
        writer = FakeWriter(letter="My volunteering raised 4,700 pounds for a made-up trust.")
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad", status="applying")
            # the made-up world's AI is never "free" (`gpu.busy`); the fake writer stands in for it here
            with mock.patch.object(documents, "render", lambda job, docs: {}), \
                    mock.patch.object(gpu, "busy", lambda: None):
                text = documents.draft_letter(a.con, job)
            docs = documents.load(a.con, job)
        self.assertEqual(text, writer.letter)
        asked = [e["id"] for e in writer.log]
        for pid in ("analyse", "fit", "letter", "verify"):
            self.assertIn(pid, asked, f"the form-time letter takes the pack's path ({pid})")
        self.assertIn(RECORD, writer.asked("letter")[0]["user"], "the record is searched for evidence")
        self.assertEqual(docs["letter"], writer.letter)
        self.assertIn("number not in the CV or advert: 4,700", docs["letter_flags"])
        self.assertIn("1 thing to check", docs["letter_notes"]["checked"])


class Checklist(unittest.TestCase):
    def test_the_checklist_never_says_to_open_a_command_line(self):
        for person in people.PEOPLE:
            with self.subTest(person=person.key), as_applicant(person) as a:
                job = add_job(a.con, person.key)
                md = pack.build(a.con, job["id"], use_web=False).read_text()
            checklist = section(md, "Before submitting")
            self.assertTrue(checklist.strip())
            for word in ("python", "jobhunt", "`", "command", "terminal"):
                self.assertNotIn(word, checklist.lower())
            self.assertNotIn("python3 -m", md)

    def test_a_students_only_advert_is_flagged_from_the_jobs_eligibility(self):
        verdict = {"requires": "current_students", "eligible": False,
                   "why": "For current students only. You finished your course in 2026."}
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", eligibility=verdict)
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertIn(f"- ELIGIBILITY: {verdict['why']}", section(md, "Check before sending"))


if __name__ == "__main__":
    unittest.main()

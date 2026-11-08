"""Application packs shaped by the CV type: the letter, its checks, the checklist, the requests.

Each test runs in the made-up world as one of the made-up people of `support.people`, with their CV type
(`support.people.cv_type`, through the real CV store), and a recording fake AI that answers each document
prompt the way a model would be asked to. The fake's letter repeats what the prompt told it to mention, so
a test can follow the CV type from the settings to the finished letter. The helpers here are shared by
`test_type_leaks.py`, `test_prep.py` and `test_contacts.py`.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sqlite3
import time
import unittest
from collections.abc import Iterator
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from support import people
from support.actions import finished
from support.world import World

from jobhunt import cv, db, documents, gpu, letter, llm, pack, record
from jobhunt.core import runner, settings
from jobhunt.writing import cites, flow, motive

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
    "prep": "Prepare ", "contacts_pick": "You choose who", "plan": "Choose the evidence",
    "sentence_check": "Check one sentence", "sentence_fix": "Rewrite one sentence",
    "pairing": "Find the applicant's own reason", "smooth": "Rewrite one paragraph",
    "add": "Write one more paragraph", "attribute": "Say which evidence items",
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
    plan: list = field(default_factory=list)           # the evidence plan's picks; none takes the unplanned path
    pairing: dict = field(default_factory=lambda: {"employer_fact": "", "item": "", "reason": ""})
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
        if pid == "plan":
            return {"evidence": self.plan}
        if pid == "sentence_check":
            return {"supported": True, "unsupported": [], "context_ok": True, "role_ok": True}
        if pid == "sentence_fix":
            return {"sentence": ""}
        if pid == "pairing":
            return self.pairing
        if pid in ("smooth", "add"):
            return {"paragraph": ""}
        if pid == "attribute":
            return {"sentences": [{"n": int(n), "ids": [], "kind": "courtesy"}
                                  for n in re.findall(r"^(\d+)\. ", user.split("\nSentences:\n", 1)[-1], re.M)]}
        if pid == "contacts_pick":
            return {"picks": [{"n": 1, "why": "Leads the team that is hiring.", "sure": True}]}
        if pid == "prep":
            return "## What they will test\n\nThe written work the advert names."
        return "A short made-up paragraph."

    def chat(self, system: str, user: str, **kw) -> str:
        out = self.reply(system, user)
        text = out if isinstance(out, str) else json.dumps(out)
        self.log.append({"id": prompt_id(system), "system": system, "user": user, "reply": text,
                         "temperature": kw.get("temperature")})
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
def as_applicant(person: people.Person, writer: FakeWriter | None = None,
                 length_floor: float = 0.0) -> Iterator[SimpleNamespace]:
    """A made-up world where `person`, with their CV type and their own made-up CV, has the fake writer. The
    made-up letters are a sentence or two, so no letter is short (`flow.LENGTH_FLOOR`) unless a test gives
    `length_floor`."""
    writer = writer or FakeWriter()
    with World() as w, people.as_person(person), people.cv_type(person.key), contextlib.ExitStack() as stack:
        for p in (mock.patch.object(llm, "chat", writer.chat), mock.patch.object(llm, "chat_json", writer.chat_json),
                  mock.patch.object(cv, "facts_text", lambda: FACTS[person.key]),
                  mock.patch.object(record, "context", lambda queries, per_query=3, limit=6000: RECORD),
                  mock.patch.object(flow, "LENGTH_FLOOR", length_floor)):
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
        # Claude wrote 193 to 198 words for "About 250 words, in three short paragraphs" (2026-10-23)
        self.assertIn("not much fewer", system)
        self.assertNotIn("short paragraphs", system)
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

    def test_the_letter_names_the_evidence_its_plan_picked(self):
        writer = FakeWriter(plan=[{"source": "S2", "quote": "Volunteer guide at a made-up heritage centre"},
                                  {"source": "S1", "quote": "a quote no source holds"}])
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("cvdocs.letter_tone", "formal", by="you")
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        asked = writer.asked("plan")
        self.assertEqual(len(asked), 1, "one plan per letter")
        self.assertIn("Requirements:\n- Writing reports\n- Spreadsheets\n", asked[0]["user"])
        self.assertIn("S1 (CV): " + FACTS["humanities_grad"], asked[0]["user"])
        self.assertIn("(Volunteering/guide.md > Role): A made-up note about guiding visitors.", asked[0]["user"])
        self.assertEqual([i.text for i in written["plan"]], [])
        self.assertEqual(written["notes"]["evidence"], "Volunteering/guide.md > Role",
                         "with no pick that holds, the record passages are named as before")

    def test_the_letters_evidence_line_names_the_picks_that_quote_a_source(self):
        writer = FakeWriter(plan=[{"source": "S1", "quote": "Dissertation on river trade in an invented port town"}])
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("cvdocs.letter_tone", "formal", by="you")
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual([(i.id, i.source) for i in written["plan"]], [("E1", "CV")])
        self.assertEqual(written["notes"]["evidence"], "Dissertation on river trade in an invented port town")

    def test_the_application_page_shows_what_the_letter_used_and_what_was_checked(self):
        # the page's letter Tone, Evidence and Checked were always empty
        from jobhunt.apply import queries

        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", status="applying")
            pack.build(a.con, job["id"], use_web=False)
            letter = queries.application(a.con, job["id"])["docs"]["letter"]
            shown = pack.read_letter_notes(job)
        self.assertEqual((letter["tone"], letter["evidence"], letter["checked"]),
                         (shown["tone"], shown["evidence"], shown["checked"]))
        self.assertEqual(letter["checked"], pack.CHECKED_CLEAN)

    def test_apply_with_hal_notes_the_record_passages_the_letter_used(self):
        # a regression: docs.prepare built the pack without its action, so Activity "Used" never named them
        from jobhunt.apply import actions

        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            done = finished(actions.start("docs.prepare", job["id"]), WAIT_S)
            runner.flush()
            row = a.con.execute("SELECT used FROM activity WHERE action_id = ?", (done["id"],)).fetchone()
        self.assertEqual(done["state"], "done", done.get("error"))
        self.assertIn({"kind": "record", "label": "Volunteering/guide.md > Role", "ref": ""},
                      json.loads(row["used"] or "[]"))

    def test_apply_with_hal_makes_the_cover_letter_pdf_before_approval(self):
        # testers found no cover letter in the application folder: only approving or editing made its PDF
        from jobhunt import documents
        from jobhunt.apply import actions

        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            done = finished(actions.start("docs.prepare", job["id"]), WAIT_S)
            made = documents.files(job, a.con)
        self.assertEqual(done["state"], "done", done.get("error"))
        self.assertEqual(set(made), {"cv", "letter"})

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


    def test_a_letter_that_breaks_a_high_rule_is_revised_once_and_what_is_left_is_listed(self):
        writer = FakeWriter(letter="I am passionate about the made-up collections and the visitors.")
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        letters = writer.asked("letter")
        self.assertEqual(len(letters), 2, "one revision, never a loop")
        self.assertIn("Your first draft:\n" + writer.letter, letters[1]["user"])
        self.assertIn('"passionate"', letters[1]["user"])
        self.assertTrue(written["revised"])
        self.assertIn("a word that reads as machine-written or stock: passionate", written["flags"])
        self.assertTrue(written["notes"]["checked"].startswith(pack.REVISED))

    def test_a_revised_letter_in_plain_words_is_clean(self):
        replies = iter(["I am passionate about the made-up collections.",
                        "I would bring my dissertation on river trade to the collections."])
        writer = FakeWriter()
        writer.reply = lambda system, user, original=writer.reply: (
            next(replies) if prompt_id(system) == "letter" else original(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(written["text"], "I would bring my dissertation on river trade to the collections.")
        self.assertEqual(written["flags"], [])
        self.assertEqual(written["notes"]["checked"], pack.REVISED + pack.CHECKED_CLEAN)

    def test_a_plain_first_draft_is_never_revised(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(len(a.writer.asked("letter")), 1)
        self.assertFalse(written["revised"])

    def test_answers_that_break_a_high_rule_are_revised_once_together(self):
        replies = iter([{"answers": [{"question": "Why this role?", "answer": "I am a passionate team player."}]},
                        {"answers": [{"question": "Why this role?", "answer": "It matches the work I have done."}]}])
        writer = FakeWriter()
        writer.reply = lambda system, user, original=writer.reply: (
            next(replies) if prompt_id(system) == "answers" else original(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        asked = writer.asked("answers")
        self.assertEqual(len(asked), 2)
        self.assertIn("Your first answers:", asked[1]["user"])
        self.assertIn("It matches the work I have done.", section(md, "Form answers"))
        self.assertNotIn("passionate", section(md, "Check before sending"))

    def test_every_draft_is_written_in_your_spelling_without_another_ai_call(self):
        us_words = {"letter": "I organized the catalog of a made-up archive by color.",
                    "message": "I analyzed the made-up visitor behavior.",
                    "answers": {"answers": [{"question": "Why this role?", "answer": "I traveled to the center."}]}}
        writer = FakeWriter()
        writer.reply = lambda system, user, original=writer.reply: us_words.get(prompt_id(system)) or original(
            system, user)
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertIn("I organised the catalogue of a made-up archive by colour.", section(md, "Cover letter"))
        self.assertIn("I analysed the made-up visitor behaviour.", section(md, "Message to the hiring manager"))
        self.assertIn("I travelled to the centre.", section(md, "Form answers"))
        self.assertNotIn("other side of the Atlantic", section(md, "Check before sending"))
        self.assertEqual(len(writer.asked("letter")), 1, "a spelling is fixed in place, never by a revision")
        self.assertIn("write in UK English, with UK spelling", " ".join(writer.asked("letter")[0]["system"].split()))

    def test_a_us_spelling_setting_turns_uk_spelling_in_a_draft_into_us(self):
        writer = FakeWriter(letter="I organised the catalogue of a made-up archive.")
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("cvdocs.spelling", "en-US", by="you")
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(written["text"], "I organized the catalog of a made-up archive.")
        self.assertIn("write in US English, with US spelling", " ".join(writer.asked("letter")[0]["system"].split()))

    def test_with_no_employment_record_no_request_mentions_one(self):
        # "Employment record: none" in the fit, letter and verify requests read to Claude as
        # a record that lacked a job on the CV; prep already leaves it out
        writer = FakeWriter()
        with as_applicant(people.HUMANITIES, writer) as a, mock.patch.object(record, "context", lambda *q, **k: ""):
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
        for pid in ("fit", "letter", "verify"):
            self.assertTrue(writer.asked(pid), pid)
            self.assertNotIn("Employment record", writer.asked(pid)[0]["user"], pid)


CLAIM = "ran the whole made-up archive alone"   # a claim no fact of the made-up applicant supports


class ClaimWriter(FakeWriter):
    """A fake writer whose first drafts make `CLAIM`, and whose rewrites drop it unless `stubborn`."""

    def __init__(self, stubborn: bool = False):
        super().__init__()
        self.stubborn = stubborn

    def reply(self, system: str, user: str) -> object:
        pid = prompt_id(system)
        rewrite = "not in their facts" in user
        said = f"I {CLAIM}." if self.stubborn or not rewrite else "I guided visitors at a made-up heritage centre."
        if pid in ("letter", "message"):
            return said
        if pid == "answers":
            return {"answers": [{"question": "Why this role?", "answer": said}]}
        if pid == "verify":
            drafts = user.split("\n\nDrafts:\n", 1)[1]
            return {"unsupported": [CLAIM] if CLAIM in drafts else []}
        return super().reply(system, user)


class Claims(unittest.TestCase):
    def test_the_check_reads_the_advert_and_brief_as_the_employers_own_words(self):
        # most "Check before sending" items in real packs were the employer's own facts restated (2026-11-06)
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            pack.write_letter(a.con, job, about="A made-up trust that looks after river boats.")
        asked = a.writer.asked("verify")[0]["user"]
        employer = asked.split("<<<EMPLOYER\n", 1)[1].split("\nEMPLOYER>>>", 1)[0]
        self.assertIn("never flag a sentence for restating them", asked)
        self.assertIn("Help catalogue the Trust's collections", employer)
        self.assertIn("A made-up trust that looks after river boats.", employer)
        self.assertIn(FACTS["humanities_grad"], asked.split("EMPLOYER>>>", 1)[1])
        self.assertIn(f"Employment record ({record.OWN_DOCUMENTS})", asked)
        self.assertNotIn("Evidence map", asked, "the fit map is the AI's own words, not a fact")

    def test_a_letter_with_an_unsupported_claim_is_rewritten_once_without_it(self):
        writer = ClaimWriter()
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        letters = writer.asked("letter")
        self.assertEqual(len(letters), 2, "one rewrite, never a loop")
        self.assertIn(f"- {CLAIM}", letters[1]["user"])
        self.assertNotIn(CLAIM, written["text"])
        self.assertEqual(written["flags"], [], "only what still fails is listed")
        self.assertEqual(len(writer.asked("verify")), 2, "the rewrite is checked again")
        self.assertEqual(written["notes"]["checked"], pack.SOFTENED + pack.CHECKED_CLEAN)

    def test_a_claim_the_rewrite_keeps_is_listed_for_you(self):
        writer = ClaimWriter(stubborn=True)
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(len(writer.asked("letter")), 2)
        self.assertEqual(written["flags"], [f"unsupported claim: {CLAIM}"])

    def test_a_claim_your_cv_states_is_never_flagged_or_rewritten(self):
        # a sentence from the person's own CV was listed as an unsupported claim (2026-11-07)
        said = "I was a volunteer guide at a made-up heritage centre."
        writer = FakeWriter(letter=said)
        plain = writer.reply
        writer.reply = lambda system, user: ({"unsupported": ["volunteer guide at a made-up heritage centre"]}
                                             if prompt_id(system) == "verify" else plain(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(len(writer.asked("letter")), 1)
        self.assertEqual((written["text"], written["flags"]), (said, []))

    def test_the_message_and_answers_are_each_rewritten_once_without_the_claim(self):
        writer = ClaimWriter()
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertEqual((len(writer.asked("message")), len(writer.asked("answers"))), (2, 2))
        self.assertNotIn(CLAIM, md)
        self.assertNotIn("unsupported claim", section(md, "Check before sending"))


PICKS = [{"source": "S1", "quote": "Dissertation on river trade in an invented port town"},
         {"source": "S2", "quote": "A made-up note about guiding visitors"}]
CITED = "My dissertation was on river trade in an invented port town [E1]. I would like to talk about the role [M]."


class MarkedWriter(FakeWriter):
    """A fake writer that cites the evidence plan as the prompts ask; `first` is each letter's first draft, or
    the drafts in turn when it is a list."""

    def __init__(self, first: str | list[str] = CITED):
        super().__init__(plan=PICKS)
        self.first = first

    def reply(self, system: str, user: str) -> object:
        pid = prompt_id(system)
        if pid == "letter":
            if "Your draft:" in user:
                return CITED
            return self.first.pop(0) if isinstance(self.first, list) else self.first
        if pid == "message":
            return "Hello,\n\nI guided visitors at a made-up heritage centre [E2]. What does the team catalogue [M]?"
        if pid == "answers":
            return {"answers": [{"question": "Why this role?", "answer": "Guiding visitors taught me to answer "
                                                                     "questions clearly [E2]."}]}
        return super().reply(system, user)


class Cited(unittest.TestCase):
    def test_every_draft_cites_the_plan_and_is_shown_without_its_markers(self):
        writer = MarkedWriter()
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertEqual(len(writer.asked("plan")), 1, "one plan for the letter, the message and the answers")
        for pid in ("letter", "message", "answers"):
            self.assertIn('E1: "Dissertation on river trade in an invented port town" (from the CV)',
                          writer.asked(pid)[0]["user"], pid)
            self.assertIn("When the request gives an evidence plan", " ".join(writer.asked(pid)[0]["system"].split()),
                          pid)
        self.assertIn("My dissertation was on river trade in an invented port town. I would like to talk about the "
                      "role.", section(md, "Cover letter"))
        self.assertIn("I guided visitors at a made-up heritage centre. What does the team catalogue?",
                      section(md, "Message to the hiring manager"))
        self.assertIn("Guiding visitors taught me to answer questions clearly.", section(md, "Form answers"))
        self.assertEqual(writer.asked("verify"), [], "a marked draft is checked sentence by sentence")
        self.assertNotRegex(md, r"\[(E\d|M)", "no marker reaches the pack")

    def test_a_sentence_about_you_that_cites_no_evidence_is_rewritten_once_then_taken_out(self):
        # a local model added "to ensure patient data remained local and secure" to fit an advert (2026-11-07)
        writer = MarkedWriter(first=f"I kept every visitor record local and secure. {CITED}")
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        fixes = writer.asked("sentence_fix")
        letters = writer.asked("letter")
        self.assertEqual(len(letters), 2, "the first needed a rewrite, so a second is drafted; neither is rewritten")
        self.assertNotIn("Your draft:", letters[1]["user"], "the second is drafted afresh")
        self.assertEqual(len(fixes), 2, "one sentence rewrite in each draft")
        self.assertIn(f"Sentence: I kept every visitor record local and secure.\nWhat is wrong: {cites.NO_EVIDENCE}",
                      fixes[0]["user"])
        self.assertEqual(written["text"], cites.strip(CITED), "the rewrite found nothing true to say, so it is cut")
        self.assertEqual(written["flags"], [])
        self.assertTrue(written["notes"]["checked"].startswith(pack.SOFTENED))

    def test_check_before_sending_lists_only_sentences_still_failing_never_free_form_flags(self):
        # one verify call over every draft listed about 20 false alarms for 10 real problems in five packs
        # (2026-11-07); a marked draft is checked sentence by sentence, against its own evidence only
        added = "I catalogued visitor feedback for the centre's new exhibition [E2]."
        writer = MarkedWriter(first=f"{CITED}\n\n{added}")
        plain = writer.reply

        def reply(system: str, user: str) -> object:
            if prompt_id(system) == "verify":
                return {"unsupported": ["Dissertation on river trade", "a made-up heritage centre", "the role"]}
            if prompt_id(system) == "sentence_check" and "exhibition" in user:
                return {"supported": False, "unsupported": ["for the centre's new exhibition", "visitor"],
                        "context_ok": True, "role_ok": True}
            return plain(system, user)

        writer.reply = reply
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(writer.asked("verify"), [])
        self.assertEqual(written["flags"], ["unsupported claim: I catalogued visitor feedback for the centre's new "
                                            "exhibition. (its evidence does not say: \"for the centre's new "
                                            "exhibition\")"])
        checked = writer.asked("sentence_check")
        self.assertEqual(len(checked), 2, "one call in each of the two drafts; restating its evidence needs none")
        self.assertNotIn("Dissertation", checked[0]["user"], "a sentence sees only the evidence it cites")

    def test_a_message_saying_what_the_letter_said_is_rewritten_from_other_evidence(self):
        # packs reused one CV line in the letter, the message and both answers (2026-11-07)
        writer = MarkedWriter()
        plain = writer.reply
        said = "My dissertation was on river trade in an invented port town [E1]."

        def reply(system: str, user: str) -> object:
            pid = prompt_id(system)
            if pid == "message":
                return f"Hello,\n\n{said} What does the team catalogue [M]?"
            if pid == "sentence_fix" and "already said as" in user:
                return {"sentence": "I guided visitors at a made-up heritage centre [E2]."}
            return plain(system, user)

        writer.reply = reply
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        fix = writer.asked("sentence_fix")[0]["user"]
        self.assertIn('already said as "My dissertation was on river trade in an invented port town."', fix)
        self.assertNotIn("E1:", fix)
        self.assertIn("I guided visitors at a made-up heritage centre. What does the team catalogue?",
                      section(md, "Message to the hiring manager"))
        self.assertNotIn(pack.REPEAT_FLAG, md)

    def test_a_letter_short_of_your_length_gets_one_paragraph_more_and_is_listed_if_still_short(self):
        # letters of 105 to 216 words against a preferred 250 had no "Check before sending" at all (2026-11-07)
        writer = MarkedWriter()
        with as_applicant(people.HUMANITIES, writer, length_floor=flow.LENGTH_FLOOR) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        asked = writer.asked("add")
        self.assertEqual(len(asked), 1, "one paragraph more, asked for once")
        self.assertIn('Unused evidence:\nE2: "A made-up note about guiding visitors"', asked[0]["user"])
        n = len(cites.strip(CITED).split())
        self.assertIn(f"- {pack.SHORT_FLAG.format(n=n, words=250)}", section(md, "Check before sending"))

    def test_check_before_sending_is_always_there_and_says_when_nothing_needs_you(self):
        # some packs had no "Check before sending" at all, so nothing flagged a broken letter (2026-11-07)
        with as_applicant(people.HUMANITIES, MarkedWriter()) as a:
            job = add_job(a.con, "humanities_grad")
            path = pack.build(a.con, job["id"], use_web=False)
            md = path.read_text()
            self.assertEqual(section(md, "Check before sending").strip(), pack.NOTHING_TO_CHECK)
            self.assertEqual(documents.summary(job)["flags"], [])
            with mock.patch.object(pack.reputation, "check", lambda company, advert: (["Possible match: X"], "")):
                pack.add_company_check(path, job["company"], job)
            self.assertEqual(section(path.read_text(), "Check before sending").strip(), "- Possible match: X")

    def test_a_paragraph_pointing_back_at_nothing_is_listed(self):
        letter = (f"{CITED}\n\nThis exhibition taught me to answer questions from visitors [E2].")
        with as_applicant(people.HUMANITIES, MarkedWriter(first=letter)) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(written["flags"], [f"{pack.DANGLING_FLAG}: This exhibition taught me to answer questions "
                                            "from visitors."])

    def test_saved_drafts_have_straight_quotes_and_no_stray_leading_space(self):
        # curly apostrophes and a stray leading space reached graded packs (2026-11-07)
        curly = " My dissertation\u2019s subject was river trade in an invented port town [E1]. I would like to " \
                "talk about the role [M]."
        writer = MarkedWriter(first=curly)
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertTrue(written["text"].startswith("My dissertation's subject"), written["text"])

    def test_of_two_letters_the_one_with_fewer_sentences_still_failing_is_kept(self):
        writer = MarkedWriter(first=["I led every tour of the heritage centre [E7].", CITED])
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(len(writer.asked("letter")), 2)
        self.assertEqual((written["text"], written["flags"]), (cites.strip(CITED), []))

    def test_of_two_letters_as_good_as_each_other_the_first_is_kept(self):
        writer = MarkedWriter(first=["I led every tour of the heritage centre [E7].", "I ran the shop [E8]."])
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(written["text"], "I led every tour of the heritage centre.")

    def test_a_pack_with_a_letter_needing_no_rewrite_makes_these_calls(self):
        # the count a local model at about 200 tokens a second answers in a few minutes (2026-11-07); a letter
        # needing a rewrite adds one letter, and each sentence checked adds one call
        writer = MarkedWriter()
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
        counts = {pid: sum(e["id"] == pid for e in writer.log) for pid in dict.fromkeys(e["id"] for e in writer.log)}
        self.assertEqual(counts, {"analyse": 1, "brief": 1, "fit": 1, "plan": 1, "pairing": 1, "letter": 1,
                                  "message": 1, "sentence_check": 2, "answers": 1})

    def test_the_letter_and_message_are_drafted_cooler_than_other_prose(self):
        writer = MarkedWriter()
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
        self.assertEqual({e["id"]: e["temperature"] for e in writer.log if e["id"] in ("letter", "message", "brief")},
                         {"letter": pack.DRAFT_TEMPERATURE, "message": pack.DRAFT_TEMPERATURE, "brief": None})
        self.assertLess(pack.DRAFT_TEMPERATURE, 0.7)

    def test_the_letters_reason_for_the_employer_comes_from_a_pairing_that_holds(self):
        pairing = {"employer_fact": "answer enquiries from visitors", "item": "E2",
                   "reason": "guiding visitors is the same work"}
        writer = MarkedWriter()
        writer.pairing = pairing
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job, about="A made-up trust that looks after river boats.")
        asked = writer.asked("pairing")[0]["user"]
        self.assertIn("Advert:\nCollections Assistant", asked)
        self.assertIn("Company brief:\nA made-up trust that looks after river boats.", asked)
        self.assertIn('E2: "A made-up note about guiding visitors"', asked)
        self.assertIn('Motivation pairing (write why this employer from this alone): the '
                      'employer\'s "answer enquiries from visitors" and the applicant\'s E2',
                      writer.asked("letter")[0]["user"])
        self.assertEqual((written["pairing"].item.id, written["pairing"].fact),
                         ("E2", "answer enquiries from visitors"))

    def test_with_no_honest_pairing_the_letter_is_told_to_give_no_reason(self):
        writer = MarkedWriter()
        writer.pairing = {"employer_fact": "a culture of innovation", "item": "E1", "reason": "it is exciting"}
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertIsNone(written["pairing"], "a fact the advert never says is no pairing")
        self.assertIn(motive.NONE_SAID, writer.asked("letter")[0]["user"])

    def test_a_sentence_citing_evidence_the_plan_does_not_hold_is_listed_when_it_stays(self):
        stubborn = "I led every tour of the heritage centre [E7]."
        writer = MarkedWriter(first=stubborn)
        writer.reply = lambda system, user, original=writer.reply: (
            stubborn if prompt_id(system) == "letter" else original(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(written["text"], "I led every tour of the heritage centre.")
        self.assertEqual(written["flags"], ["unsupported claim: I led every tour of the heritage centre. (cites E7, "
                                            "which the evidence plan does not hold)"])


FREE_LETTER = ("My dissertation on river trade in an invented port town taught me to read old ledgers closely. "
               "I would like to talk about the role.")
FREE_MESSAGE = ("Hello,\n\nI am a history graduate applying for the Collections Assistant role. I guided visitors "
                "at a made-up heritage centre. What does the team catalogue?")
FREE_ANSWER = "Guiding visitors at a made-up heritage centre taught me to answer questions clearly."


class FreeWriter(FakeWriter):
    """A fake writer that writes freely, as the prompts now ask, and attributes each sentence by `cites`: the
    ids of the first key a sentence holds; a sentence holding none is a courtesy. `attributed` False makes the
    attribution reply hold nothing."""

    def __init__(self, cites_by: dict[str, list[str]] | None = None, attributed: bool = True):
        super().__init__(plan=PICKS)
        self.cites_by = cites_by if cites_by is not None else {"dissertation": ["E1"], "guided visitors": ["E2"],
                                                                "Guiding visitors": ["E2"]}
        self.attributed = attributed

    def reply(self, system: str, user: str) -> object:
        pid = prompt_id(system)
        if pid == "letter":
            return CITED if cites.MARK_NOTE in user else FREE_LETTER
        if pid == "message":
            return FREE_MESSAGE
        if pid == "answers":
            return {"answers": [{"question": "Why this role?", "answer": FREE_ANSWER}]}
        if pid == "attribute":
            if not self.attributed:
                return {}
            said = re.findall(r"^(\d+)\. (.*)$", user.split("\nSentences:\n", 1)[1], re.M)
            return {"sentences": [{"n": int(n), "ids": next((v for k, v in self.cites_by.items() if k in s), []),
                                   "kind": "fact" if any(k in s for k in self.cites_by) else "courtesy"}
                                  for n, s in said]}
        return super().reply(system, user)


class Free(unittest.TestCase):
    def test_drafts_are_written_freely_then_each_sentence_is_tied_to_its_evidence_and_checked(self):
        # marking every sentence while writing made choppy one-fact sentences, held at 2 of 5 (2026-11-08)
        writer = FreeWriter()
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        for pid in ("letter", "message", "answers"):
            system = " ".join(writer.asked(pid)[0]["system"].split())
            self.assertNotIn("[E2]", system, pid)
            self.assertIn("When the request gives an evidence plan, its items are the facts to use", system, pid)
            self.assertIn('E1: "Dissertation on river trade in an invented port town"', writer.asked(pid)[0]["user"])
        self.assertIn("name each project, job or module the first time", " ".join(writer.asked("letter")[0]["system"]
                                                                                    .split()))
        attributed = writer.asked("attribute")
        self.assertEqual(len(attributed), 3, "one attribution each for the letter, the message and the answers")
        self.assertIn("1. My dissertation on river trade", attributed[0]["user"])
        checked = [e["user"] for e in writer.asked("sentence_check")]
        self.assertTrue(checked)
        self.assertTrue(all(("E1:" in u) != ("E2:" in u) for u in checked), "each sees only its own evidence")
        self.assertIn(FREE_LETTER, section(md, "Cover letter"))
        self.assertIn(FREE_MESSAGE.split("\n\n", 1)[1], section(md, "Message to the hiring manager"))
        self.assertIn(FREE_ANSWER, section(md, "Form answers"))
        self.assertEqual(writer.asked("verify"), [])
        self.assertNotRegex(md, r"\[(E\d|M)")

    def test_a_sentence_about_you_the_attribution_ties_to_nothing_is_rewritten(self):
        writer = FreeWriter(cites_by={"guided visitors": ["E2"], "Guiding visitors": ["E2"]})
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            pack.write_letter(a.con, job)
        fixes = writer.asked("sentence_fix")
        self.assertTrue(fixes)
        self.assertIn(f"What is wrong: {cites.NO_EVIDENCE}", fixes[0]["user"])

    def test_when_attribution_fails_the_writer_marks_its_own_draft_once(self):
        writer = FreeWriter(attributed=False)
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        letters = writer.asked("letter")
        self.assertEqual(len(letters), 2)
        self.assertIn(f"Your draft:\n{FREE_LETTER}\n\n{cites.MARK_NOTE}", letters[1]["user"])
        self.assertEqual(written["text"], cites.strip(CITED))
        self.assertEqual(writer.asked("verify"), [], "the marked draft is checked sentence by sentence")


class Today(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict("os.environ", {"HAL_EMPLOYMENT_FAKE_NOW": "2026-11-07T09:00:00Z"}))

    def test_every_writing_request_says_today_and_where_you_are(self):
        with as_applicant(people.HUMANITIES) as a:
            settings.set("goal.study_end", "2026-06", by="you")
            settings.set("you.education.degree", "BA", by="you")
            settings.set("you.education.subject", "History", by="you")
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
        line = "Today: 2026-11-07\nStage: Finished BA History in 2026-06; available now."
        for pid in ("fit", "letter", "message", "answers", "verify"):
            self.assertIn(line, a.writer.asked(pid)[0]["user"], pid)

    def test_a_letter_that_speaks_of_finishing_an_ended_course_is_rewritten_once(self):
        # "available to start in 2026 upon completing my degree", in November 2026 (2026-11-06)
        writer = FakeWriter(letter="I can start soon upon completing my degree in History.")
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("goal.study_end", "2026-06", by="you")
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        letters = writer.asked("letter")
        self.assertEqual(len(letters), 2)
        self.assertIn("upon completing my degree in History. (the course ended in 2026-06)", letters[1]["user"])
        self.assertEqual(written["flags"], ["unsupported claim: I can start soon upon completing my degree in "
                                            "History. (the course ended in 2026-06)"],
                         "the fixed fake letter keeps it, so it is listed")


class FitWriter(FakeWriter):
    """A fake writer whose fit map finds no evidence of the tools a card lists."""

    def reply(self, system: str, user: str) -> object:
        if prompt_id(system) == "fit":
            return {"matches": [{"requirement": "Docker", "evidence": "No evidence of Docker", "strength": "partial"},
                                {"requirement": "Writing reports", "evidence": "a made-up report",
                                 "strength": "strong"}],
                    "gaps": ["Experience with AWS", "Spreadsheets"], "eligible": True, "eligibility_notes": ""}
        return super().reply(system, user)


class Fit(unittest.TestCase):
    def cards(self, con) -> None:
        """A checked project card listing AWS and Docker, and one nobody checked listing Kafka."""
        from jobhunt.profile import store

        ev = {"source_rel": "Sources/Code/Tide gauge.md", "quote": "", "field": "", "doc_type": "repository"}
        store.confirm(con, store.add_found(con, "project", {"name": "Tide gauge", "summary": "Logs made-up tides.",
                                                            "tools": ["AWS", "Docker"]}, ev))
        store.add_found(con, "project", {"name": "Queue toy", "tools": ["Kafka"]}, {**ev, "source_rel": "q.md"})

    def test_the_fit_map_sees_the_tools_of_every_checked_card(self):
        with as_applicant(people.TECHNICAL) as a:
            self.cards(a.con)
            job = add_job(a.con, "technical_grad")
            pack.build(a.con, job["id"], use_web=False)
        asked = a.writer.asked("fit")[0]["user"]
        self.assertIn("- Project (personal project): Tide gauge. Tools: AWS, Docker. Logs made-up tides.", asked)
        self.assertNotIn("Kafka", asked, "a card nobody checked never reaches the fit map")

    def test_a_requirement_naming_a_cards_tool_is_never_a_gap(self):
        # the fit table said "No evidence of AWS" although a project card lists AWS (2026-11-06)
        with as_applicant(people.TECHNICAL, FitWriter()) as a:
            self.cards(a.con)
            job = add_job(a.con, "technical_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        fit = section(md, "Fit")
        self.assertIn("| Experience with AWS | project (personal project): Tide gauge (tools: AWS) | partial |", fit)
        self.assertIn("| Docker | project (personal project): Tide gauge (tools: Docker) | partial |", fit)
        self.assertIn("| Writing reports | a made-up report | strong |", fit)
        self.assertIn("Gaps to address or accept: Spreadsheets\n", fit)


class ToolsWriter(FakeWriter):
    """A fake writer whose fit map gives two projects tools neither card lists, as a real one did (2026-11-07)."""

    def reply(self, system: str, user: str) -> object:
        if prompt_id(system) == "fit":
            return {"matches": [
                {"requirement": "Python data libraries",
                 "evidence": "Tide gauge and Ferry planner use Pandas, Scikit-learn and Matplotlib.",
                 "strength": "strong"},
                {"requirement": "Data cleaning", "evidence": "Tide gauge cleans sensor logs with Lua and Pandas.",
                 "strength": "strong"},
                {"requirement": "Writing reports", "evidence": "a made-up report", "strength": "strong"}],
                "gaps": ["Spreadsheets"], "eligible": True, "eligibility_notes": ""}
        return super().reply(system, user)


class FitTools(unittest.TestCase):
    def test_a_tool_a_fit_row_gives_to_a_project_that_never_used_it_is_taken_out(self):
        # a fit row said two projects used Pandas, Scikit-learn and Matplotlib; no source had either using them
        # (2026-11-07)
        from jobhunt.profile import store

        with as_applicant(people.TECHNICAL, ToolsWriter()) as a:
            ev = {"source_rel": "Sources/Code/Tide gauge.md", "quote": "", "field": "", "doc_type": "repository"}
            store.confirm(a.con, store.add_found(a.con, "project", {"name": "Tide gauge", "tools": ["Lua"]}, ev))
            store.confirm(a.con, store.add_found(a.con, "project", {"name": "Ferry planner", "tools": ["Go"]},
                                                 {**ev, "source_rel": "Sources/Code/Ferry planner.md"}))
            for skill in ("Pandas", "Scikit-learn", "Matplotlib"):
                store.confirm(a.con, store.add_found(a.con, "skill", {"name": skill}, {**ev, "source_rel": "cv.md"}))
            job = add_job(a.con, "technical_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        fit = section(md, "Fit")
        self.assertNotIn("| Python data libraries |", fit)
        self.assertIn("| Data cleaning | Tide gauge cleans sensor logs with Lua. | partial |", fit)
        self.assertIn("| Writing reports | a made-up report | strong |", fit)
        self.assertIn("Gaps to address or accept: Spreadsheets; Python data libraries\n", fit)


class TableWriter(FakeWriter):
    """A fake writer whose fit map leaves out a must-have, rates a row with no evidence partial, and calls
    office days in the person's town partial, as a real model replied (2026-11-07); asked again for the
    missing must-have, it answers for it."""

    MUST = ("Writing reports", "Familiar with statistical methodologies",
            "Made-up Town office three days a week")

    def reply(self, system: str, user: str) -> object:
        pid = prompt_id(system)
        if pid == "analyse":
            return {**super().reply(system, user), "must_have": list(self.MUST), "nice_to_have": []}
        if pid == "fit" and "- Writing reports" not in user:
            return {"matches": [{"requirement": "Familiar with statistical methodologies",
                                 "evidence": "record: a made-up survey of visitor numbers", "strength": "partial"}],
                    "gaps": [], "eligible": True, "eligibility_notes": ""}
        if pid == "fit":
            return {"matches": [{"requirement": "Writing reports", "evidence": "No evidence in the CV",
                                 "strength": "partial"},
                                {"requirement": "Made-up Town office three days a week",
                                 "evidence": "no evidence of refusal", "strength": "partial"}],
                    "gaps": [], "eligible": True, "eligibility_notes": ""}
        return super().reply(system, user)


class Table(unittest.TestCase):
    def test_every_must_have_gets_a_row_and_a_missing_one_is_asked_for_once(self):
        # "familiar with statistical methodologies" was missing from a real fit table (2026-11-07)
        with as_applicant(people.HUMANITIES, TableWriter()) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        asks = a.writer.asked("fit")
        self.assertEqual(len(asks), 2, "one more ask, never a loop")
        self.assertIn("Requirements:\n- Familiar with statistical methodologies\n\n", asks[1]["user"])
        self.assertIn("| Familiar with statistical methodologies | record: a made-up survey of visitor numbers "
                      "| partial |", section(md, "Fit"))

    def test_a_row_with_no_evidence_is_a_gap_and_office_days_where_you_live_are_met(self):
        # "London office three days a week" was partial, "no evidence of refusal", for a person in London, and a
        # row with no evidence was rated partial (2026-11-07)
        with as_applicant(people.HUMANITIES, TableWriter()) as a:
            settings.set("you.city", "Made-up Town", by="you")
            settings.set("you.relocate", False, by="you")
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        fit = section(md, "Fit")
        self.assertIn("| Made-up Town office three days a week | You live in Made-up Town | strong |", fit)
        self.assertNotIn("| Writing reports |", fit)
        self.assertIn("Gaps to address or accept: Writing reports\n", fit)

    def test_a_must_have_still_missing_after_the_second_ask_is_a_gap(self):
        fit = pack.complete_fit({"matches": [], "gaps": []}, ["Familiar with statistical methodologies"],
                                lambda missing: {"matches": [], "gaps": []})
        self.assertEqual(fit["gaps"], ["Familiar with statistical methodologies (no evidence found)"])


def replying(pid: str, *replies: str, writer: FakeWriter | None = None) -> FakeWriter:
    """A fake writer whose `pid` replies are `replies` in turn, the last one repeated."""
    writer = writer or FakeWriter()
    plain, left = writer.reply, list(replies)
    writer.reply = lambda system, user: ((left.pop(0) if len(left) > 1 else left[0]) if prompt_id(system) == pid
                                         else plain(system, user))
    return writer


class Drafts(unittest.TestCase):
    def test_a_message_that_greets_the_applicant_is_rewritten_to_greet_the_recipient(self):
        # a message opened "Hi <the applicant's first name>," (2026-11-07)
        writer = replying("message", "Hi Marnie,\n\nI applied for the Collections Assistant role. Who leads it?",
                          "Hello,\n\nI applied for the Collections Assistant role. Who leads it?")
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        asked = writer.asked("message")
        self.assertEqual(len(asked), 2)
        self.assertIn('starting with "Hello,"', asked[1]["user"])
        message = section(md, "Message to the hiring manager")
        self.assertIn("\n\nHello,\n\nI applied for the Collections Assistant role.", message)
        self.assertNotIn("Marnie", message)

    def test_a_greeting_to_the_applicant_that_survives_the_rewrite_is_replaced(self):
        writer = replying("message", "Hi Marnie Quillfeather,\n\nI applied for the role. Who leads the team?",
                          writer=reading(contacts=[{"name": "Ada Penhallow"}]))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        message = section(md, "Message to the hiring manager")
        self.assertIn("Dear Ada Penhallow,\n\nI applied for the role.", message)
        self.assertNotIn("Hi Marnie", message)

    def test_a_letter_cut_off_mid_word_is_asked_for_again_with_room_to_finish(self):
        # a letter ended mid-word, "distingu", when the reply ran out of room (2026-11-07)
        writer = replying("letter", "I catalogued letters at a made-up museum and I would bring the distingu",
                          "I catalogued letters at a made-up museum.")
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertIn(pack.CUT_NOTE, writer.asked("letter")[1]["user"])
        self.assertEqual(written["text"], "I catalogued letters at a made-up museum.")
        self.assertEqual(written["flags"], [])

    def test_a_letter_cut_off_twice_keeps_its_last_full_sentence_and_says_so(self):
        writer = replying("letter", "I catalogued letters at a made-up museum. I would bring the distingu")
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        self.assertEqual(len(writer.asked("letter")), 2, "one more ask, never a loop")
        self.assertEqual(written["text"], "I catalogued letters at a made-up museum.")
        self.assertEqual(written["flags"], [pack.CUT_FLAG.format(what="cover letter")])

    def test_a_degree_the_advert_requires_above_yours_goes_first_and_is_never_softened(self):
        # an advert requiring a Master's or PhD was drafted for a BSc holder with no warning (2026-11-07)
        writer = reading(must_have=["A Master's degree or PhD in History", "Writing reports"])
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("you.education.degree", "BA", by="you")
            job = add_job(a.con, "humanities_grad")
            a.con.execute("UPDATE jobs SET description = description || ' You must hold a Master''s degree in "
                          "History.' WHERE id = ?", (job["id"],))
            a.con.commit()
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        first = section(md, "Check before sending").strip().splitlines()[0]
        self.assertEqual(first, "- ELIGIBILITY: the advert requires a Master's degree (\"A Master's degree or PhD in "
                                "History\"); your highest is a bachelor's degree")

    def test_a_degree_the_advert_only_also_considers_is_never_an_eligibility_line(self):
        # "ELIGIBILITY: requires a Master's" for an advert that would "also consider" Master's holders
        # (2026-11-07)
        writer = reading(must_have=["A Master's degree in History", "Writing reports"],
                         eligibility=["Master's holders also considered"])
        plain = writer.reply
        writer.reply = lambda system, user: ({**plain(system, user), "eligible": False,
                                              "eligibility_notes": "Requires a Master's degree"}
                                             if prompt_id(system) == "fit" else plain(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("you.education.degree", "BA", by="you")
            job = add_job(a.con, "humanities_grad")
            a.con.execute("UPDATE jobs SET description = description || ' We will also consider those with a "
                          "Master''s in History.' WHERE id = ?", (job["id"],))
            a.con.commit()
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertNotIn("ELIGIBILITY", md)

    def test_an_offer_to_relocate_to_your_own_town_never_reaches_a_draft(self):
        # "happy to relocate to your London office" for a person living in London (2026-11-07)
        offer = "I catalogued letters, and I am happy to relocate to your Made-up Town office."
        writer = replying("message", f"Hello,\n\n{offer} Who leads the team?", writer=FakeWriter(letter=offer))
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("you.city", "Made-up Town", by="you")
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertNotIn("relocate", md)
        self.assertIn("I catalogued letters.", section(md, "Cover letter"))
        self.assertIn("I catalogued letters. Who leads the team?", section(md, "Message to the hiring manager"))

    def test_office_days_in_the_town_you_live_in_are_never_a_gap(self):
        # a fit map called office attendance unmet for a person living in that city (2026-11-07)
        writer = FakeWriter()
        plain = writer.reply
        writer.reply = lambda system, user: ({**plain(system, user), "gaps": [
            "Office attendance in Made-up Town three days a week", "Spreadsheets"]}
            if prompt_id(system) == "fit" else plain(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            settings.set("you.city", "Made-up Town", by="you")
            settings.set("you.relocate", False, by="you")
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertIn("Gaps to address or accept: Spreadsheets\n", section(md, "Fit"))

    def test_a_tool_given_to_a_project_that_never_used_it_is_a_claim(self):
        # a letter said a Lua project "required C++ and multithreading" (2026-11-07)
        from jobhunt.profile import store

        writer = FakeWriter(letter="My Tide gauge project required C++ and multithreading.")
        with as_applicant(people.HUMANITIES, writer) as a:
            ev = {"source_rel": "Sources/Code/Tide gauge.md", "quote": "", "field": "", "doc_type": "repository"}
            store.confirm(a.con, store.add_found(a.con, "project", {"name": "Tide gauge", "tools": ["Lua"]}, ev))
            store.confirm(a.con, store.add_found(a.con, "skill", {"name": "C++"}, {**ev, "source_rel": "cv.md"}))
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        claim = "My Tide gauge project required C++ and multithreading. (C++ is not among Tide gauge's tools)"
        self.assertIn(f"- {claim}", writer.asked("letter")[1]["user"])
        self.assertEqual(written["flags"], [f"unsupported claim: {claim}"])
        self.assertIn("- Project (personal project): Tide gauge. Tools: Lua", writer.asked("verify")[0]["user"])

    def test_a_personal_project_placed_in_a_degree_is_rewritten_once_then_listed(self):
        # "My first-class Computer Science degree includes building a local RAG assistant", a personal project
        # (2026-11-07)
        from jobhunt.profile import store

        writer = FakeWriter(letter="My History degree included building Tide gauge.")
        with as_applicant(people.HUMANITIES, writer) as a:
            ev = {"source_rel": "Sources/Code/Tide gauge.md", "quote": "", "field": "", "doc_type": "repository"}
            store.confirm(a.con, store.add_found(a.con, "project", {"name": "Tide gauge", "tools": ["Lua"]}, ev))
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        claim = ("My History degree included building Tide gauge. (Tide gauge is a personal project, not part of "
                 "a degree)")
        self.assertEqual(len(writer.asked("letter")), 2, "one rewrite, never a loop")
        self.assertIn(f"- {claim}", writer.asked("letter")[1]["user"])
        self.assertEqual(written["flags"], [f"unsupported claim: {claim}"], "the fixed fake letter keeps it")

    def test_what_a_module_covered_with_no_source_is_rewritten_once_then_listed(self):
        # "the Foundations of Data Science module, which covered core statistical methods", while its notes
        # covered databases, SQL and language processing (2026-11-07)
        from jobhunt.profile import store

        said = "The Foundations of Imaginary Data module covered core statistical methods."
        writer = FakeWriter(letter=said)
        with as_applicant(people.HUMANITIES, writer) as a:
            ev = {"source_rel": "Sources/Education/Transcript.md", "quote": "", "field": "", "doc_type": "transcript"}
            store.confirm(a.con, store.add_found(a.con, "education", {
                "qualification": "BA", "subject": "History", "institution": "Wexley University",
                "modules": [{"name": "Foundations of Imaginary Data", "mark": "70"}]}, ev))
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        claim = f"{said} (nothing in your facts says what Foundations of Imaginary Data covered)"
        self.assertEqual(len(writer.asked("letter")), 2, "one rewrite, never a loop")
        self.assertIn(f"- {claim}", writer.asked("letter")[1]["user"])
        self.assertEqual(written["flags"], [f"unsupported claim: {claim}"], "the fixed fake letter keeps it")
        self.assertIn("A module's name alone says nothing of its content", pack.prompt("verify"))

    def test_the_message_and_answers_keep_to_each_projects_context(self):
        from jobhunt.profile import contexts, store

        writer = replying("message", "Hello,\n\nFerry planner was my own personal project. Who leads the team?",
                          "Hello,\n\nFerry planner was a team project for my degree. Who leads the team?")
        with as_applicant(people.HUMANITIES, writer) as a:
            ev = {"source_rel": "Sources/Code/Ferry planner.md", "quote": "", "field": "", "doc_type": "repository"}
            ferry = store.add_found(a.con, "project", {"name": "Ferry planner", "tools": ["Lua"]}, ev)
            store.confirm(a.con, ferry)
            contexts.set_context(a.con, ferry, "team")
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertEqual(len(writer.asked("message")), 2)
        self.assertIn("Ferry planner is a university team project", writer.asked("message")[1]["user"])
        self.assertIn("a team project for my degree", section(md, "Message to the hiring manager"))
        self.assertNotIn("Ferry planner", section(md, "Check before sending"))


ECHO = "I want to help catalogue the Trust's collections and answer enquiries from visitors."
OWN_REASON = "My dissertation on river trade made me curious about how a port's objects reach a museum."


class Echoes(unittest.TestCase):
    def test_a_letter_that_restates_the_advert_is_rewritten_once_to_give_your_own_reason(self):
        # "drawn to the challenge of maintaining low default rates" restated the advert as a reason (2026-11-07)
        writer = replying("letter", ECHO, OWN_REASON)
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            written = pack.write_letter(a.con, job)
        letters = writer.asked("letter")
        self.assertEqual(len(letters), 2, "one rewrite, never a loop")
        self.assertIn(f"- {ECHO}", letters[1]["user"])
        self.assertIn("tie one specific thing about the applicant", letters[1]["user"])
        self.assertEqual((written["text"], written["flags"]), (OWN_REASON, []))

    def test_an_echo_the_rewrite_keeps_is_listed_and_a_why_answer_is_checked_too(self):
        writer = replying("letter", ECHO)
        plain = writer.reply
        writer.reply = lambda system, user: ({"answers": [
            {"question": "Why do you want to work here?", "answer": ECHO},
            {"question": "Why are you a good fit for this role?", "answer": ECHO}]}
            if prompt_id(system) == "answers" else plain(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertEqual(len(writer.asked("answers")), 3, "the answers are rewritten once together for the echo, "
                                                          "and once more as the second repeats the first")
        listed = section(md, "Check before sending")
        self.assertEqual(listed.count(f"- {pack.ECHO_FLAG}: {ECHO}"), 2,
                         "the letter's and the why-here answer's, never the fit answer's")

    def test_a_later_answer_that_repeats_an_earlier_one_is_rewritten_once_and_gaps_are_listed_once(self):
        # two form answers repeated the same pipeline details, and a gap was listed twice (2026-11-07)
        same = "I guided visitors round a made-up heritage centre and wrote short reports on each tour group."
        own = "My dissertation on river trade taught me to read old shipping records closely."
        writer = FakeWriter()
        plain, asked = writer.reply, []

        def reply(system: str, user: str) -> object:
            pid = prompt_id(system)
            if pid == "answers":
                asked.append(user)
                second = own if "repeat what an earlier answer already says" in user else same
                return {"answers": [{"question": "Why this role?", "answer": same},
                                    {"question": "Why are you a good fit for this role?", "answer": second}]}
            if pid == "fit":
                return {**plain(system, user), "gaps": ["Spreadsheets", "spreadsheets (no evidence found)"]}
            return plain(system, user)

        writer.reply = reply
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertEqual(len(asked), 2, "one rewrite, never a loop")
        self.assertIn(f'- In the answer to "Why are you a good fit for this role?": {same}', asked[1])
        self.assertIn(own, section(md, "Form answers"))
        self.assertNotIn(pack.REPEAT_FLAG, md)
        self.assertIn("Gaps to address or accept: Spreadsheets\n", section(md, "Fit"))

    def test_the_prompts_ask_for_your_own_reason_up_front(self):
        with as_applicant(people.HUMANITIES):
            for pid in ("letter", "answers"):
                self.assertIn("tie one specific thing about the applicant", pack.prompt(pid))


class To(unittest.TestCase):
    """The message's "To:" line names a hiring contact, never the advert's address for questions about applying."""

    ADVERT = ("Build internal tools with Ada Penhallow, Head of Platform. For questions about the application "
              "process, email talent@brightwater.example.org or call Sam Reedly in our recruitment team.")

    def to(self, contacts: list) -> str:
        job = {"company": "Brightwater Tools", "title": "Graduate Developer", "description": self.ADVERT}
        return pack.message_to(job, {"contacts": contacts}, {"hiring_titles": ["engineering manager"]})

    def test_an_address_or_person_for_questions_about_applying_is_never_the_recipient(self):
        # a pack's To: was the advert's address for questions about the application process (2026-11-07)
        hint = f"{pack.FIND_PREFIX} on LinkedIn (search \"Brightwater Tools engineering manager\")"
        self.assertEqual(self.to([{"email": "talent@brightwater.example.org",
                                   "role": "questions about the application process"}]), hint)
        self.assertEqual(self.to(["careers@brightwater.example.org"]), hint)
        self.assertEqual(self.to([{"name": "Sam Reedly", "role": ""}]), hint, "the advert gives him for applying")

    def test_a_named_hiring_contact_is_the_recipient_without_a_mailbox(self):
        found = self.to([{"name": "Sam Reedly", "role": "Recruiter"},
                         {"name": "Ada Penhallow", "role": "Head of Platform",
                          "email": "jobs@brightwater.example.org"}])
        self.assertEqual(found, "Ada Penhallow, Head of Platform")


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

    def test_an_advert_with_no_conditions_gives_no_eligibility_warning(self):
        # Claude's fit map for a made-up advert with no conditions said "eligible": false because there was
        # "nothing to match" (2026-10-23), and the pack warned the person they might not be eligible
        writer = FakeWriter()
        fit = writer.reply
        unsure = {"eligible": False, "eligibility_notes": "The conditions are empty, so nothing can be judged."}
        writer.reply = lambda system, user: ({**fit(system, user), **unsure} if prompt_id(system) == "fit"
                                             else fit(system, user))
        with as_applicant(people.CHANGER, writer) as a:
            job = add_job(a.con, "career_changer")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertNotIn("ELIGIBILITY", md)
        analyse = writer.reply
        writer.reply = lambda system, user: ({**analyse(system, user), "eligibility": ["Current students only"]}
                                             if prompt_id(system) == "analyse" else analyse(system, user))
        with as_applicant(people.CHANGER, writer) as a:
            job = add_job(a.con, "career_changer")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertIn(f"- ELIGIBILITY: {unsure['eligibility_notes']}", section(md, "Check before sending"))

    def test_a_students_only_advert_is_flagged_from_the_jobs_eligibility(self):
        verdict = {"requires": "current_students", "eligible": False,
                   "why": "For current students only. You finished your course in 2026."}
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", eligibility=verdict)
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertIn(f"- ELIGIBILITY: {verdict['why']}", section(md, "Check before sending"))


def reading(**changes) -> FakeWriter:
    """A fake writer whose reading of the advert has `changes`, as a real model replied (2026-10-23)."""
    writer = FakeWriter()
    plain = writer.reply
    writer.reply = lambda system, user: ({**plain(system, user), **changes} if prompt_id(system) == "analyse"
                                         else plain(system, user))
    return writer


class Advert(unittest.TestCase):
    NOTE = "Competency questions (the advert mentions them but doesn't list the actual questions)"

    def test_a_note_about_questions_is_never_answered_as_a_question(self):
        # Claude's reading of an advert that only said "competency questions" listed this note as a question,
        # and the pack wrote an answer to it (2026-10-23)
        writer = reading(questions=[self.NOTE])
        with as_applicant(people.CHANGER, writer) as a:
            job = add_job(a.con, "career_changer")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertNotIn("Competency questions", writer.asked("answers")[0]["user"])
        self.assertNotIn("Competency questions", md)

    def test_a_question_the_advert_writes_out_is_answered(self):
        advert = 'Please answer: "Tell us about a time you kept a project on budget." Then attach your CV.'
        self.assertEqual(pack.written_out(["Tell us about a time you kept a project on budget.", self.NOTE],
                                          advert),
                         ["Tell us about a time you kept a project on budget."])

    def test_an_advert_with_no_stated_requirements_is_matched_on_its_duties(self):
        # Claude read all three made-up adverts as having no must_have or nice_to_have, so every pack's fit
        # table was empty and the letter's Evidence line said "None named" (2026-10-23)
        writer = reading(must_have=[], nice_to_have=[], summary="Keep projects on time and track budgets.")
        with as_applicant(people.CHANGER, writer) as a:
            job = add_job(a.con, "career_changer")
            pack.build(a.con, job["id"], use_web=False)
        self.assertIn("Requirements:\n- Keep projects on time and track budgets.", writer.asked("fit")[0]["user"])

    def test_the_letter_greets_the_person_the_advert_names(self):
        # the letter said "Dear Hiring Team" while the message beside it greeted the named contact (2026-10-23)
        writer = reading(contacts=["Ada Penhallow, Head of Collections"])
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertTrue(section(md, "Cover letter").lstrip().startswith("Dear Ada Penhallow,\n"))
        self.assertEqual(pack.salutation({"contacts": ["jobs@example.org"]}), "Dear Hiring Team,")
        self.assertEqual(pack.salutation({"contacts": [{"name": "Sam Example", "role": "Lead"}]}),
                         "Dear Sam Example,")

    def test_the_models_notes_about_a_message_never_reach_the_message(self):
        # Claude's message (made-up career changer, 2026-10-23) began with Markdown bold and ended with a note
        # about itself, and its "75" was then flagged as an invented number
        reply = ("**Subject:** Application: Project Coordinator\n\nDear Hiring Manager,\n\nI planned a refit "
                 "that opened two days early.\n\nKind regards,\nPriya Ashdown-Vell\n\n(About 75 words. The "
                 "advert names no individual, so I used \"Hiring Manager\".)")
        writer = FakeWriter()
        plain = writer.reply
        writer.reply = lambda system, user: reply if prompt_id(system) == "message" else plain(system, user)
        with as_applicant(people.CHANGER, writer) as a:
            job = add_job(a.con, "career_changer")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        message = section(md, "Message to the hiring manager")
        self.assertIn("Subject: Application: Project Coordinator", message)
        self.assertIn("Kind regards,\nPriya Ashdown-Vell", message)
        for leak in ("**", "About 75 words", "names no individual"):
            self.assertNotIn(leak, message)
        self.assertNotIn("number not in the CV or advert: 75", md)

    def test_the_evidence_line_lists_the_fit_maps_sentences_cleanly(self):
        # with no record, Claude's fit-map evidence (full sentences) was joined into "spreadsheet., Student"
        # (2026-10-23)
        writer = FakeWriter()
        plain = writer.reply
        matches = [{"requirement": "Writing reports", "evidence": "Catalogued 300 letters into a spreadsheet.",
                    "strength": "strong"},
                   {"requirement": "Visitors", "evidence": "Guided 40 open-day tours.", "strength": "strong"}]
        writer.reply = lambda system, user: ({**plain(system, user), "matches": matches}
                                             if prompt_id(system) == "fit" else plain(system, user))
        with as_applicant(people.HUMANITIES, writer) as a, mock.patch.object(record, "context", lambda *q, **k: ""):
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
            shown = pack.read_letter_notes(job)
        self.assertEqual(shown["evidence"], "Catalogued 300 letters into a spreadsheet; Guided 40 open-day tours")

    def test_the_message_never_gives_the_named_person_a_title_the_advert_does_not(self):
        # Claude greeted the advert's "Ada Penhallow, Head of Collections" as "Dear Ms Penhallow," (2026-10-23)
        writer = reading(contacts=["Ada Penhallow, Head of Collections"])
        plain = writer.reply
        writer.reply = lambda system, user: ("Dear Ms Penhallow,\n\nI am applying.\n\nKind regards,\nMarnie"
                                             if prompt_id(system) == "message" else plain(system, user))
        with as_applicant(people.HUMANITIES, writer) as a:
            job = add_job(a.con, "humanities_grad")
            md = pack.build(a.con, job["id"], use_web=False).read_text()
        self.assertIn("\nDear Ada Penhallow,\n\nI am applying.", section(md, "Message to the hiring manager"))
        self.assertNotIn("Ms Penhallow", md)
        given = "Contact Dr Penhallow, Head of Collections."
        self.assertEqual(pack.untitled("Dear Dr Penhallow,\n\nHello.", {"contacts": ["Ada Penhallow"]}, given),
                         "Dear Dr Penhallow,\n\nHello.", "a title the advert gives is kept")


class CommandLine(unittest.TestCase):
    def test_letter_and_check_read_the_numbered_application_folder(self):
        """Regression: both read the old shared packs folder after packs moved into application folders.

        Also: both left their database open, so Windows could not remove the home afterwards (CI, 2026-10-23).
        """
        from jobhunt.cli import docs as cli_docs

        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            pack_md = pack.build(a.con, job["id"], use_web=False)
            made, opened = [], []
            connect = db.connect
            with mock.patch.object(letter, "render", lambda body, out, *a, **k: made.append((body, out)) or out), \
                    mock.patch.object(pack, "add_company_check", lambda md, company, job=None: made.append(md)), \
                    mock.patch.object(db, "connect", lambda *a, **k: opened.append(connect(*a, **k)) or opened[-1]):
                out = io.StringIO()
                with redirect_stdout(out):
                    cli_docs.cmd_letter(SimpleNamespace(id=job["id"], to=None, subject=None))
                    cli_docs.cmd_check(SimpleNamespace(id=job["id"], ready=False))
            self.assertTrue(opened)
            for con in opened:
                with self.assertRaises(sqlite3.ProgrammingError):   # closed
                    con.execute("SELECT 1")
        self.assertEqual(made[0][1].parent, pack_md.parent)
        self.assertIn("Marnie Quillfeather", made[0][0])
        self.assertEqual(made[1], pack_md)
        self.assertIn("checked Riverside Heritage Trust", out.getvalue())


CIVIL_SERVICE = ("Executive Officer", "Department for Made-up Affairs",
                 "An Executive Officer post in the Civil Service. You will be assessed against the Success "
                 "Profiles behaviours Working Together and Communicating and Influencing. Write a personal "
                 "statement of up to 750 words.")
TECH_WORDS = ("key skills", "code", "python", "technical", "software", "tools", "non-technical")


def add_advert(con, job_id: str, advert: tuple[str, str, str]) -> dict:
    """Adds one made-up advert and returns its row."""
    title, company, text = advert
    con.execute("""INSERT INTO jobs (id, source, company, title, location, url, description, first_seen,
                   last_seen, status) VALUES (?, 'test', ?, ?, 'Made-up Town', ?, ?, ?, ?, 'shortlisted')""",
                (job_id, company, title, f"https://jobs.example.org/{job_id}", text, db.now(), db.now()))
    con.commit()
    return dict(con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def conventions(request: str) -> str:
    """The conventions section of a request."""
    found = re.search(r"Conventions for this kind of job \((.*?)\):\n((?:- .*\n?)+)", request)
    return found.group(0) if found else ""


class Fields(unittest.TestCase):
    """Each letter and answer is written to the conventions of its kind of job, never another field's."""

    def test_a_humanities_letter_gets_humanities_conventions_and_no_technical_advice(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            pack.build(a.con, job["id"], use_web=False)
        block = conventions(a.writer.asked("letter")[0]["user"])
        self.assertIn("(Humanities, heritage and the written word)", block)
        self.assertIn("research project", block)
        for word in TECH_WORDS:
            self.assertNotIn(word, block.lower())

    def test_a_civil_service_form_gets_star_and_the_behaviours(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_advert(a.con, "test:civil", CIVIL_SERVICE)
            pack.build(a.con, job["id"], use_web=False)
        answers = conventions(a.writer.asked("answers")[0]["user"])
        self.assertIn("(Civil Service and public sector)", answers)
        self.assertIn("STAR", answers)
        self.assertIn("behaviour", answers)
        self.assertIn("transferable skill", answers, "the humanities background adds its own advice")
        letter = conventions(a.writer.asked("letter")[0]["user"])
        self.assertIn("essential criterion", letter)

    def test_a_technical_letter_gets_the_technical_conventions(self):
        with as_applicant(people.TECHNICAL) as a:
            job = add_job(a.con, "technical_grad")
            pack.build(a.con, job["id"], use_web=False)
        self.assertIn("(Software, data and IT)", conventions(a.writer.asked("letter")[0]["user"]))

    def test_a_job_hal_cannot_place_gets_general_advice(self):
        with as_applicant(people.CHANGER) as a:
            job = add_job(a.con, "career_changer")
            pack.build(a.con, job["id"], use_web=False)
        self.assertIn("(General)", conventions(a.writer.asked("letter")[0]["user"]))


class Requests(unittest.TestCase):
    """The writing routes start their actions; the actions keep the person's words and check the letter."""

    def setUp(self):
        from support.api import HalServer

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        # The server first: it points the folders at its own home, where the pack must then be written.
        self.hal = stack.enter_context(HalServer())
        self.a = stack.enter_context(as_applicant(people.HUMANITIES))
        stack.enter_context(mock.patch.object(documents, "render", lambda job, docs: {}))
        self.job = add_job(self.a.con, "humanities_grad", status="applying")
        pack.build(self.a.con, self.job["id"], use_web=False)
        self.c = self.hal.signed_in()

    def wait(self, action_id: str) -> dict:
        end = time.monotonic() + WAIT_S
        while time.monotonic() < end:
            runner.flush()
            row = self.a.con.execute("SELECT state, error FROM actions WHERE id = ?", (action_id,)).fetchone()
            if row is not None and row["state"] not in ("queued", "running"):
                return dict(row)
            time.sleep(POLL_S)
        raise AssertionError("the action did not finish")

    def url(self, rest: str) -> str:
        return f"/api/v2/applications/{quote(self.job['id'], safe='')}{rest}"

    def test_asking_for_changes_rewrites_the_letter_message_and_answers_in_your_words(self):
        r = self.c.post(self.url("/documents/change"), {"words": "Mention my guiding work"})
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(self.wait(r.json()["action_id"])["state"], "done")
        asked = self.a.writer.log
        for pid in ("letter", "message", "answers"):
            self.assertTrue(any("Mention my guiding work" in e["user"] for e in asked if e["id"] == pid), pid)
        why = self.a.con.execute("SELECT why, used FROM activity WHERE action_id = ?",
                                 (r.json()["action_id"],)).fetchone()
        self.assertEqual(why["why"], "You asked: Mention my guiding work")
        self.assertIn("Volunteering/guide.md > Role", why["used"])
        docs = documents.load(self.a.con, self.job)
        self.assertIn("letter_notes", docs)

    def test_a_changed_message_and_answers_get_the_packs_checks(self):
        # docs.change saved the AI's message and answers with no claim, greeting or project check (2026-11-07)
        plain = self.a.writer.reply

        def reply(system: str, user: str) -> object:
            pid, rewrite = prompt_id(system), "not in their facts" in user or "Greet the person" in user
            if pid == "message":
                return "Hello,\n\nI guided visitors. Who leads the team?" if rewrite else \
                    f"Hi Marnie,\n\nI {CLAIM}. Who leads the team?"
            if pid == "answers":
                return {"answers": [{"question": "Why this role?", "answer": "I guided visitors." if rewrite
                                     else f"I {CLAIM}."}]}
            if pid == "verify":
                return {"unsupported": [CLAIM] if CLAIM in user.split("\n\nDrafts:\n", 1)[1] else []}
            return plain(system, user)

        self.a.writer.reply = reply
        r = self.c.post(self.url("/documents/change"), {"words": "Mention my guiding work"})
        self.assertEqual(self.wait(r.json()["action_id"])["state"], "done")
        docs = documents.load(self.a.con, self.job)
        self.assertEqual(docs["message"], "Hello,\n\nI guided visitors. Who leads the team?")
        self.assertNotIn(CLAIM, json.dumps(docs["answers"]))
        changed = [e for e in self.a.writer.log if "Mention my guiding work" in e["user"]]
        self.assertEqual([e["id"] for e in changed].count("message"), 2, "one rewrite, never a loop")
        self.assertIn("Today: ", changed[-1]["user"], "the change reads the same facts as the pack")

    def test_a_change_you_ask_for_comes_back_in_your_spelling(self):
        # the changed message and answers were saved as the AI wrote them, never checked for spelling
        plain = self.a.writer.reply
        us = {"message": "I organized the made-up catalog.",
              "answers": {"answers": [{"question": "Why this role?", "answer": "I analyzed the center's records."}]}}
        self.a.writer.reply = lambda system, user: us.get(prompt_id(system)) or plain(system, user)
        r = self.c.post(self.url("/documents/change"), {"words": "Mention my catalogue work"})
        self.assertEqual(self.wait(r.json()["action_id"])["state"], "done")
        docs = documents.load(self.a.con, self.job)
        self.assertEqual(docs["message"], "I organised the made-up catalogue.")
        self.assertEqual([a["answer"] for a in docs["answers"] if a["question"] == "Why this role?"],
                         ["I analysed the centre's records."])

    def test_saving_your_edits_keeps_them_to_learn_your_writing_from(self):
        r = self.c.request("PUT", self.url("/documents"), {"letter": "My own made-up letter, in my own words."})
        self.assertEqual(r.status, 200, r.text)
        rows = self.a.con.execute("SELECT kind, final FROM writing_edits").fetchall()
        self.assertEqual([tuple(x) for x in rows], [("letter", "My own made-up letter, in my own words.")])

    def test_critique_as_a_recruiter_starts_a_read_that_changes_nothing(self):
        before = documents.load(self.a.con, self.job)
        r = self.c.post(self.url("/documents/critique"), {})
        self.assertEqual(r.status, 200, r.text)
        action_id = r.json()["action_id"]
        self.assertEqual(self.wait(action_id)["state"], "done")
        result = self.c.get(f"/api/v2/actions/{action_id}").json()["result"]
        self.assertEqual(set(result), {"field", "screen", "requirements", "documents", "top_fixes", "ai", "note"})
        self.assertEqual([d["document"] for d in result["documents"]], ["cv", "letter"])
        self.assertEqual(documents.load(self.a.con, self.job), before)

    def test_asking_for_changes_with_no_words_is_refused(self):
        r = self.c.post(self.url("/documents/change"), {"words": "  "})
        self.assertEqual(r.status, 400)
        self.assertEqual(r.json()["error"]["field"], "words")

    def test_rewriting_the_letter_changes_only_the_letter(self):
        before = documents.load(self.a.con, self.job)
        r = self.c.post(self.url("/documents/letter/rewrite"), {"words": "Shorter, please"})
        self.assertEqual(self.wait(r.json()["action_id"])["state"], "done")
        after = documents.load(self.a.con, self.job)
        self.assertEqual(after["message"], before["message"])
        self.assertEqual([e["id"] for e in self.a.writer.log if "Shorter, please" in e["user"]], ["letter"])


if __name__ == "__main__":
    unittest.main()

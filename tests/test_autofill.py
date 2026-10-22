"""Answering form questions: facts from the profile, private answers, salaries and codes.

Moved by class from tests/test_apply.py. Private answers come from the private answers store (
`settings.private`, stood in for by `support.stubs.settings_private`), never from the profile, so the
health tests save them there. `PrivatePaths` holds the rules for private questions: a private question
never reaches the AI, is never guessed and is never remembered. The applicant is made up.
"""
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import legacy
from support.fake_ai import fake_ai
from support.legacy import PROFILE
from support.stubs import settings_private

from jobhunt import autofill, db, gmail, salary, tasks
from jobhunt.core import paths

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class Answers(unittest.TestCase):
    def setUp(self):
        self.saved = autofill.profile
        autofill.profile = lambda: dict(PROFILE)
        self.private({})
        self.job = {"id": "test:1", "source": "adzuna", "company": "Practice Ltd", "title": "Junior Engineer",
                    "salary": "", "description": "", "location": "London"}

    def tearDown(self):
        autofill.profile = self.saved

    def private(self, answers: dict) -> None:
        """The applicant's saved private answers for the rest of the test."""
        stack = contextlib.ExitStack()
        stack.enter_context(settings_private.saved(answers))
        self.addCleanup(stack.close)

    def propose(self, **field):
        f = {"hid": "h0", "kind": "text", "type": "text", "label": "", "required": True, **field}
        return autofill.propose(self.job, [f], {}, {})[0]

    def test_a_socio_economic_question_is_not_answered_with_the_university(self):
        f = self.propose(kind="combobox", label="Were you eligible for free school meals? Choose up to 1",
                         options=["No", "Prefer not to say", "Yes"])
        self.assertEqual((f["value"], f["source"]), ("Prefer not to say", "profile: diversity"))

    def test_a_question_that_only_mentions_a_fact_is_left_to_the_model(self):
        f = self.propose(kind="textarea", label="Please detail any additional training courses you have completed. "
                                                "This may be self-led online or in school or college.")
        self.assertIsNone(f["value"])

    def test_a_listed_fact_is_chosen_from_its_list_however_long_the_question(self):
        f = self.propose(kind="combobox", options=["Other University", "University"],
                         label="Which university are you currently attending or did you attend for your "
                               "undergraduate studies? Type the name to search the list provided below.")
        self.assertEqual(f["value"], "University")

    def test_a_number_box_is_not_given_a_sentence(self):
        self.assertIsNone(self.propose(type="number", label="Salary expectations")["value"])
        self.assertEqual(self.propose(type="number", label="Notice period (in weeks)")["value"], "0")

    def test_a_salary_question_gets_a_researched_figure_with_its_reasoning(self):
        """No "open to discussion": a range in words, a number in a number box, and the band holding it in a list."""
        autofill.profile = lambda: {**PROFILE, "salary": ""}
        saved = salary.expect
        salary.expect = lambda job, con=None: {"figure": 38000, "text": "£36,000 to £40,000",
                                               "basis": "ITJobsWatch median for graduate software engineer in London: £38,000"}
        self.addCleanup(setattr, salary, "expect", saved)
        words = self.propose(label="What are your salary expectations?")
        self.assertEqual((words["value"], words["source"]), ("£36,000 to £40,000", "profile: salary"))
        self.assertIn("ITJobsWatch median", words["note"])
        self.assertEqual(self.propose(type="number", label="Salary expectations")["value"], "38000")
        listed = self.propose(kind="select", label="Expected salary", options=["£25,000 - £34,999", "£35,000 - £44,999"])
        self.assertEqual(listed["value"], "£35,000 - £44,999")

    def health(self):
        self.private({"disability": "Yes", "disability_type": "Dyslexia", "guaranteed_interview": "Yes",
                      "adjustments": "Yes. I would like 25% extra time on timed tests."})

    def test_a_disclosed_disability_is_given_and_other_identity_questions_are_declined(self):
        """The applicant discloses a disability only: gender and the like stay "Prefer not to say"."""
        self.health()
        f = self.propose(kind="radio", label="Do you consider yourself to have a disability?",
                         options=["Yes", "No", "Prefer not to say"])
        self.assertEqual((f["value"], f["source"]), ("Yes", "private: disability"))
        self.assertEqual(self.propose(kind="radio", label="What is your gender?",
                                      options=["Man", "Woman", "Prefer not to say"])["value"], "Prefer not to say")
        kinds = self.propose(kind="checkgroup", label="Which of the following best describes your disability?",
                             options=["Physical impairment", "Neurodivergent condition (e.g. autism, dyslexia)", "Mental health"])
        self.assertEqual(kinds["value"], ["Neurodivergent condition (e.g. autism, dyslexia)"])
        described = self.propose(kind="textarea", label="Please describe the nature of your disability")
        self.assertEqual(described["value"], "Dyslexia")

    def test_caring_for_someone_with_a_disability_is_not_the_applicants_disability(self):
        """Nomura, 2026-10-15: "look after someone ... caused by disability" was answered "Yes, 1-19 hours a week"."""
        self.health()
        carer = ["No", "Yes, 1-19 hours a week", "Yes, 20-49 hours a week", "Yes, 50 hours or more a week", "Prefer not to say"]
        for label in ("Do you look after or care for someone with long term physical or mental ill health caused by "
                      "disability or age?", "Does anyone in your household have a disability?",
                      "Do you have caring responsibilities for a disabled family member?"):
            f = self.propose(kind="combobox", label=label, options=carer)
            self.assertEqual(autofill.private_question(f), "carer", label)
            self.assertEqual((f["value"], f["source"], f["note"]), (None, "you", "Answer this yourself"), label)

    def test_a_caring_question_gets_the_applicants_answer(self):
        """The applicant cares for no one, and says so; whether they were in care themselves is another question."""
        self.private({"carer": "No"})
        hours = ["Yes, 1-19 hours a week", "Yes, 20-49 hours a week", "No, I do not provide care", "Prefer not to say"]
        nomura = self.propose(kind="combobox", label="Do you look after or care for someone with long term physical or "
                                                     "mental ill health caused by disability or age?", options=hours)
        self.assertEqual((nomura["value"], nomura["source"]), ("No, I do not provide care", "private: carer"))
        self.assertEqual(self.propose(kind="radio", label="Do you have any caring responsibilities?",
                                      options=["Yes", "No", "Prefer not to say"])["value"], "No")
        self.assertEqual(self.propose(label="Are you a carer?")["value"], "No")
        self.assertEqual(self.propose(kind="radio", label="Have you ever been in care, or are you a care leaver?",
                                      options=["Yes", "No", "Prefer not to say"])["value"], "Prefer not to say")

    def test_disability_questions_in_their_many_wordings(self):
        """Each wording gets the answer the applicant chose, or is left for them where only they can judge."""
        self.health()
        yes_no, mine = ["Yes", "No", "Prefer not to say"], "Yes. I would like 25% extra time on timed tests."
        cases = [
            ("radio", "Do you have a disability? If yes, please give details below.", yes_no, "", "Yes"),
            ("radio", "Does your disability affect your ability to carry out the role?", yes_no, "", None),
            ("radio", "Is your day-to-day activity limited because of a health problem or disability?",
             ["Yes, limited a lot", "Yes, limited a little", "No"], "", None),
            ("radio", "Are you registered disabled?", yes_no, "", None),
            ("textarea", "Please tell us about any disability-related support you need", [], "", mine),
            ("radio", "Do you have any access requirements for the interview?", ["Yes", "No"], "", "Yes"),
            ("text", "Preferred pronouns", [], "We are a Disability Confident employer and offer a guaranteed interview",
             "Prefer not to say"),
            ("checkgroup", "Please select the category that best describes your impairment",
             ["Physical", "Learning disability, difficulty or difference (such as dyslexia, ADHD)", "Prefer not to say"], "",
             ["Learning disability, difficulty or difference (such as dyslexia, ADHD)"]),
            ("radio", "Would you like to hear about our disability network?", ["Yes", "No"], "", None),
        ]
        for kind, label, options, context, expected in cases:
            with self.subTest(label):
                f = self.propose(kind=kind, label=label, options=options, context=context)
                self.assertEqual(f["value"], expected)

    def test_a_question_about_a_formal_diagnosis_is_left_for_the_applicant(self):
        self.health()
        f = self.propose(kind="radio", label="Do you have a formal diagnosis of your disability?", options=["Yes", "No"])
        self.assertIsNone(f["value"])
        self.assertIn("diagnosis", f["note"])

    def test_a_guaranteed_interview_is_taken_and_adjustments_are_asked_for(self):
        self.health()
        self.assertIs(self.propose(kind="checkbox", label="I wish to apply under the Disability Confident guaranteed "
                                                          "interview scheme", required=False)["value"], True)
        self.assertEqual(self.propose(kind="radio", label="Do you require any reasonable adjustments?",
                                      options=["Yes", "No"])["value"], "Yes")
        details = self.propose(kind="textarea", label="If yes, please give details",
                               context="Reasonable adjustments for the assessment process")
        self.assertEqual((details["value"], details["source"]), ("Yes. I would like 25% extra time on timed tests.",
                                                                 "private: adjustments"))

    def test_private_answers_never_reach_the_model_that_writes(self):
        """Letters and essays are written from facts that hold no health answer."""
        self.health()
        self.assertNotIn("Dyslexia", autofill._facts(self.job, {}, None))
        self.assertNotIn("extra time", autofill._facts(self.job, {}, None))

    def test_an_optional_sign_up_is_left_alone_and_consent_is_given(self):
        self.assertTrue(self.propose(kind="checkbox", label="Send me the newsletter", required=False)["skip"])
        self.assertIs(self.propose(kind="checkbox", label="I agree to the privacy policy")["value"], True)

    def test_the_source_is_matched_to_the_nearest_kind_the_list_offers(self):
        f = self.propose(kind="select", label="How did you hear about us?",
                         options=["Company website", "Job board", "A friend"])
        self.assertEqual(f["value"], "Job board")

    def test_a_referee_is_not_given_the_applicants_details(self):
        self.assertIsNone(self.propose(label="Email", section="Referee 1")["value"])
        self.assertEqual(self.propose(label="Email", section="About you")["value"], "ada@example.org")

    def test_a_course_start_date_is_not_the_applicants_availability(self):
        self.assertIsNone(self.propose(label="Start date", section="Education")["value"])
        self.assertEqual(self.propose(label="Earliest start date", section="Availability")["value"], "Immediately")

    def test_a_referees_name_is_remembered_as_the_referees(self):
        con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(con.close)
        autofill.remember(con, [{"hid": "h0", "kind": "text", "label": "Name", "section": "Referee 1",
                                 "value": "Grace Hopper", "source": "you"}])
        mine = self.propose(label="Name", section="Your details")
        self.assertEqual(mine["value"], "Ada Lovelace")
        f = {"hid": "h0", "kind": "text", "type": "text", "label": "Name", "section": "Referee 1", "required": True}
        self.assertEqual(autofill.propose(self.job, [f], {}, {}, con)[0]["value"], "Grace Hopper")

    def test_a_keyword_answer_the_review_rejects_is_answered_again(self):
        f = self.propose(label="Phone number of your current manager")
        self.assertEqual(f["source"], "profile: phone")
        replies = iter([{"wrong": [{"hid": "h0", "why": "asks for the manager's number"}]}, {"answers": []}])
        saved = autofill.llm.chat_json, autofill.gpu.busy
        autofill.llm.chat_json, autofill.gpu.busy = lambda *a, **k: next(replies), lambda: None
        try:
            autofill.ask_model(self.job, [f], {})
        finally:
            autofill.llm.chat_json, autofill.gpu.busy = saved
        self.assertIsNone(f["value"])
        self.assertIn("manager's number", f["note"])

    def test_a_number_ending_a_sentence_in_the_source_is_supported(self):
        """A source's "2023-2024." holds 2024, so a draft giving 2024 is not flagged."""
        from jobhunt import pack
        self.assertEqual(pack.unsupported_numbers("Editor, 2023 - 2024", "history column, 2023-2024. Commissioned"), [])

    def test_a_security_code_is_found_in_its_email(self):
        self.assertEqual(gmail.code_in("Security code for your application",
                                       "Copy and paste this code into the security code field: aB3dE5fG Then"), "aB3dE5fG")
        self.assertIsNone(gmail.code_in("Welcome", "Use the code below to continue with your application"))


PRIVATE_LABELS = ("Do you consider yourself to have a disability?", "Please describe the nature of your disability",
                  "Do you require any reasonable adjustments?", "Apply under the guaranteed interview scheme",
                  "Are you a carer?")
CANARY = "SYNTH-PRIVATE-7731 a quiet room for tests"
REAL_PROFILE = autofill.profile          # taken before any test replaces it


class PrivatePaths(unittest.TestCase):
    """Private questions stay out of every AI path, whatever their source."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-autofill-")))
        stack.enter_context(paths.set_override(tmp))
        stack.enter_context(mock.patch.object(autofill, "profile", lambda: dict(PROFILE)))
        stack.enter_context(mock.patch.object(autofill, "_facts", lambda job, docs, con: "Made-up CV facts."))
        stack.enter_context(mock.patch.object(autofill.record, "context", lambda questions: ""))
        self.stack = stack
        self.job = {"id": "test:1", "source": "adzuna", "company": "Practice Ltd", "title": "Junior Engineer",
                    "salary": "", "description": "", "location": "London"}

    def field(self, hid: str, label: str, **kw) -> dict:
        return {"hid": hid, "kind": "text", "type": "text", "label": label, "required": True, **kw}

    def sent(self, fake) -> str:
        return "\n".join(f"{r.system}\n{r.user}" for r in fake.log)

    def test_a_private_question_never_reaches_the_ai_whatever_its_source(self):
        self.stack.enter_context(settings_private.saved({"disability": "Yes", "adjustments": CANARY}))
        sources = ("profile: city", "your earlier answer", "documents: drafted answer", "hal", "you")
        fields = [self.field(f"p{i}", label, value="Yes", source=src)
                  for i, (label, src) in enumerate(zip(PRIVATE_LABELS, sources, strict=True))]
        fields += [self.field("p9", PRIVATE_LABELS[0], value=None, source="you"),
                   self.field("n1", "Town or city", value="London", source="profile: city"),
                   self.field("n2", "What do you enjoy about engineering?")]
        with fake_ai({"re:You check answers": '{"wrong": []}'}, default='{"answers": []}') as fake:
            autofill.ask_model(self.job, fields, {})
        sent = self.sent(fake)
        self.assertGreaterEqual(len(fake.log), 2)                 # the review and the fill both ran
        self.assertIn("Town or city", sent)
        self.assertIn("What do you enjoy about engineering?", sent)
        for label in PRIVATE_LABELS:
            self.assertNotIn(label, sent)
        self.assertNotIn(CANARY, sent)

    def test_a_person_whose_private_answer_is_prefer_not_to_say_still_gets_ai_form_filling(self):
        self.stack.enter_context(settings_private.saved({"disability": "Prefer not to say",
                                                         "disability_type": "Prefer not to say", "carer": "No"}))
        f = self.field("n2", "What do you enjoy about engineering? Yes, we ask everyone; prefer not to say is fine.")
        with fake_ai(default='{"answers": [{"hid": "n2", "value": "Careful work.", "sure": true}]}') as fake:
            answered, reason = autofill.ask_model(self.job, [f], {})
        self.assertEqual((answered, reason, f["value"]), (1, None, "Careful work."))
        self.assertEqual(len(fake.log), 1)

    def test_an_unanswered_private_question_is_left_to_you_not_guessed(self):
        self.stack.enter_context(settings_private.saved({}))
        fields = [self.field(f"p{i}", label) for i, label in enumerate(PRIVATE_LABELS)]
        autofill.propose(self.job, fields, {}, {})
        self.assertEqual({(f["value"], f["source"], f["note"]) for f in fields},
                         {(None, "you", "Answer this yourself")})
        with fake_ai(default='{"answers": []}') as fake:
            self.assertEqual(autofill.ask_model(self.job, fields, {}), (0, None))
        self.assertEqual(fake.log, [])

    def test_what_you_type_into_a_private_question_is_never_remembered(self):
        con = db.connect()
        self.addCleanup(con.close)
        typed = [self.field(f"p{i}", label, value=CANARY, source="you") for i, label in enumerate(PRIVATE_LABELS)]
        typed.append(self.field("n1", "Which programming language do you use most?", value="Python", source="you"))
        self.assertEqual(autofill.remember(con, typed, "Practice Ltd"), 1)
        kept = [tuple(r) for r in con.execute("SELECT label, answer FROM answers")]
        self.assertEqual(kept, [("Which programming language do you use most?", '"Python"')])

    def test_the_carer_answer_is_never_in_the_profile_or_the_review(self):
        self.stack.enter_context(settings_private.saved({"carer": "No"}))
        self.stack.enter_context(mock.patch.dict(autofill.CONFIG, {"applicant": {"carer": "No", "notice": "A month"}}))
        self.stack.enter_context(mock.patch.object(autofill, "contact",
                                                   lambda: ("Robin Example", "robin@example.org", "07000 000001")))
        self.stack.enter_context(mock.patch.object(autofill.cv, "load", lambda: {"contact": {
            "linkedin": "linkedin.example/robin", "github": "github.example/robin", "site": "robin.example"}}))
        prof = REAL_PROFILE()
        self.assertNotIn("carer", prof)
        self.assertEqual(prof["notice"], "A month")
        f = self.field("c1", "Do you have any caring responsibilities?", kind="radio", options=["Yes", "No"])
        autofill.propose(self.job, [f], {}, {})
        self.assertEqual((f["value"], f["source"]), ("No", "private: carer"))
        with fake_ai(default='{"wrong": [], "answers": []}') as fake:
            autofill.ask_model(self.job, [f, self.field("n1", "Town or city", value="London", source="profile: city")],
                               {})
        self.assertIn("Town or city", self.sent(fake))
        self.assertNotIn("caring", self.sent(fake))

    def test_the_test_invitation_todo_says_nothing_about_private_answers(self):
        con = db.connect()
        self.addCleanup(con.close)
        con.execute("""INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, role, job_id, snippet)
                       VALUES ('m1', '1', ?, 'talent@acme.example', '', 'Your online assessment', 'assessment', 'Acme',
                               'Engineer', NULL, '')""", (db.now(),))
        todos = []
        for answers in ({}, {"adjustments": CANARY, "disability": "Yes"}):
            con.execute("DELETE FROM todos")
            with settings_private.saved(answers):
                tasks.sync(con)
            rows = con.execute("SELECT title, detail FROM todos ORDER BY priority, id")
            todos.append([(r["title"], r["detail"]) for r in rows])
        self.assertEqual(todos[0], todos[1])
        self.assertEqual([t for t, _ in todos[0]], [tasks.CHECK_TITLE, "Complete the Acme assessment"])
        words = " ".join(f"{t} {d}" for t, d in todos[0]).lower()
        for hint in ("private", "extra time", "adjust", "disab", "toml", CANARY.lower()):
            self.assertNotIn(hint, words)


if __name__ == "__main__":
    unittest.main()

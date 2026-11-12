"""Answering form questions: facts from the profile, private answers, salaries and codes.

Moved by class from tests/test_apply.py. Private answers come from the private answers store
(`settings.private`, saved by `support.private_answers`), never from the profile, so the health tests save
them there. `PrivatePaths` holds the rules for private answers: a private question
never reaches the AI, is never guessed and is never remembered. The applicant is made up.
"""
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import legacy, people, private_answers
from support.fake_ai import fake_ai
from support.legacy import PROFILE

from jobhunt import autofill, db, gmail, salary, tasks
from jobhunt.core import paths, settings
from jobhunt.sites import notes as site_notes

# Obviously synthetic private answers, so no fixture could be read as anyone's real health.
SYNTH_TYPE = "Synthetic condition B"
SYNTH_ADJUST = "SYNTHETIC adjustment: a made-up quiet room for tests"

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
        stack.enter_context(private_answers.saved(answers))
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
        autofill.profile = lambda: {**PROFILE, "university": "Southbrook University"}
        f = self.propose(kind="combobox", options=["Northgate University", "Southbrook University"],
                         label="Which university are you currently attending or did you attend for your "
                               "undergraduate studies? Type the name to search the list provided below.")
        self.assertEqual(f["value"], "Southbrook University")

    def test_a_number_box_is_not_given_a_sentence(self):
        self.assertIsNone(self.propose(type="number", label="Salary expectations")["value"])
        self.assertEqual(self.propose(type="number", label="Notice period (in weeks)")["value"], "0")

    def test_a_salary_question_gets_a_researched_figure_with_its_reasoning(self):
        """No "open to discussion": a range in words, a number in a number box, and the band holding it in a list."""
        autofill.profile = lambda: {**PROFILE, "salary": ""}
        saved = salary.expect
        salary.expect = lambda job, con=None: {"figure": 38000, "text": "\u00a336,000 to \u00a340,000",
                                               "basis": "ITJobsWatch median for graduate software engineer "
                                                        "in London: \u00a338,000"}
        self.addCleanup(setattr, salary, "expect", saved)
        words = self.propose(label="What are your salary expectations?")
        self.assertEqual((words["value"], words["source"]), ("\u00a336,000 to \u00a340,000", "profile: salary"))
        self.assertIn("ITJobsWatch median", words["note"])
        self.assertEqual(self.propose(type="number", label="Salary expectations")["value"], "38000")
        bands = ["\u00a325,000 - \u00a334,999", "\u00a335,000 - \u00a344,999"]
        listed = self.propose(kind="select", label="Expected salary", options=bands)
        self.assertEqual(listed["value"], "\u00a335,000 - \u00a344,999")

    def health(self):
        self.private({"disability": "Yes", "disability_type": SYNTH_TYPE, "guaranteed_interview": "Yes",
                      "adjustments": SYNTH_ADJUST})

    def test_a_disclosed_disability_is_given_and_other_identity_questions_are_declined(self):
        """The applicant discloses a disability only: gender and the like stay "Prefer not to say"."""
        self.health()
        f = self.propose(kind="radio", label="Do you consider yourself to have a disability?",
                         options=["Yes", "No", "Prefer not to say"])
        self.assertEqual((f["value"], f["source"]), ("Yes", "private: disability"))
        self.assertEqual(self.propose(kind="radio", label="What is your gender?",
                                      options=["Man", "Woman", "Prefer not to say"])["value"], "Prefer not to say")
        kinds = self.propose(kind="checkgroup", label="Which of the following best describes your disability?",
                             options=["Made-up category A", f"{SYNTH_TYPE} (an invented example)",
                                      "Made-up category C"])
        self.assertEqual(kinds["value"], [f"{SYNTH_TYPE} (an invented example)"])
        described = self.propose(kind="textarea", label="Please describe the nature of your disability")
        self.assertEqual(described["value"], SYNTH_TYPE)

    def test_a_disability_type_no_option_names_is_left_for_the_applicant(self):
        """The saved type is chosen only when the list names it; no ranking guesses a near option."""
        self.health()
        f = self.propose(kind="radio", label="Which category best describes your disability?",
                         options=["Made-up category A", "Made-up category C"])
        self.assertEqual((f["value"], f["source"]), (None, "you"))

    def test_caring_for_someone_with_a_disability_is_not_the_applicants_disability(self):
        """A form seen 2026-10-15: "look after someone ... caused by disability" got "Yes, 1-19 hours a week"."""
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
        listed = self.propose(kind="combobox", label="Do you look after or care for someone with long term physical or "
                                                     "mental ill health caused by disability or age?", options=hours)
        self.assertEqual((listed["value"], listed["source"]), ("No, I do not provide care", "private: carer"))
        self.assertEqual(self.propose(kind="radio", label="Do you have any caring responsibilities?",
                                      options=["Yes", "No", "Prefer not to say"])["value"], "No")
        self.assertEqual(self.propose(label="Are you a carer?")["value"], "No")
        self.assertEqual(self.propose(kind="radio", label="Have you ever been in care, or are you a care leaver?",
                                      options=["Yes", "No", "Prefer not to say"])["value"], "Prefer not to say")

    def test_disability_questions_in_their_many_wordings(self):
        """Each wording gets the answer the applicant chose, or is left for them where only they can judge."""
        self.health()
        yes_no, mine = ["Yes", "No", "Prefer not to say"], SYNTH_ADJUST
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
             ["Made-up category A", f"{SYNTH_TYPE} or similar", "Prefer not to say"], "", [f"{SYNTH_TYPE} or similar"]),
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
        self.assertEqual((details["value"], details["source"]), (SYNTH_ADJUST, "private: adjustments"))

    def test_private_answers_never_reach_the_model_that_writes(self):
        """Letters and essays are written from facts that hold no health answer."""
        self.health()
        self.assertNotIn(SYNTH_TYPE, autofill._facts(self.job, {}, None))
        self.assertNotIn(SYNTH_ADJUST, autofill._facts(self.job, {}, None))

    def test_an_optional_sign_up_is_left_alone_and_consent_is_given(self):
        self.assertTrue(self.propose(kind="checkbox", label="Send me the newsletter", required=False)["skip"])
        self.assertIs(self.propose(kind="checkbox", label="I agree to the privacy policy")["value"], True)

    def test_the_cv_goes_in_the_first_upload_named_for_no_other_document(self):
        """Regression (testers, 2026-11-04): an upload named "cover" made Hal think the CV's was named too, so
        a form with "cover-letter-input", "certificate-input" and an unnamed "Eingabefeld" got no CV."""
        uploads = [{"hid": f"h{i}", "kind": "file", "type": "file", "label": label, "required": False, "context": ""}
                   for i, label in enumerate(("cover-letter-input", "certificate-input", "Eingabefeld"))]
        files = {"cv": "/made/up/cv.pdf", "letter": "/made/up/letter.pdf"}
        out = autofill.propose(self.job, uploads, {}, files)
        self.assertEqual([f["value"] for f in out], ["/made/up/letter.pdf", None, "/made/up/cv.pdf"])

    def test_a_dialling_code_is_answered_from_the_country_not_the_phone_number(self):
        """Regression (testers, 2026-11-04): a required "Country code" list was left empty on Totaljobs."""
        autofill.profile = lambda: {**PROFILE, "phone_code": autofill._dial_code("07700 900000", "United Kingdom")}
        f = self.propose(kind="select", label="Country code", options=["+1", "+353", "+44", "+441"])
        self.assertEqual((f["value"], f["source"]), ("+44", "profile: phone_code"))
        named = self.propose(kind="select", label="Phone country code",
                             options=["Ireland (+353)", "United Kingdom (+44)", "Guernsey (+441)"])
        self.assertEqual(named["value"], "United Kingdom (+44)")
        self.assertEqual(autofill._dial_code("+353 87 123 4567", "United Kingdom"), "+353")

    def test_the_right_to_work_asked_with_sponsorship_is_never_answered_from_sponsorship(self):
        """Regression (testers, 2026-11-05): "Are you legally entitled to work in the United Kingdom? Will you
        require sponsorship now or anytime in the future?" was answered "No" from the sponsorship answer."""
        both = self.propose(kind="radio", options=["Yes", "No"],
                            label="Are you legally entitled to work in the United Kingdom? Will you require "
                                  "sponsorship now or anytime in the future?")
        self.assertEqual((both["value"], both["note"], both["why"]),
                         (None, autofill.YOURSELF, autofill.TWO_IN_ONE_WHY))
        self.assertTrue(autofill.two_in_one(both))
        first = self.propose(kind="radio", options=["Yes", "No"],
                             label="Are you authorised to work in the UK without requiring sponsorship?")
        self.assertEqual((first["value"], first["source"]), ("Yes", "profile: right_to_work"))
        sponsor = self.propose(kind="radio", options=["Yes", "No"],
                               label="Will you now or in the future require sponsorship to work in the UK?")
        self.assertEqual((sponsor["value"], sponsor["source"]), ("No", "profile: sponsorship"))

    def test_optional_sign_up_boxes_alone_are_not_an_application(self):
        news = {"kind": "checkbox", "label": "I would like to receive email communication from the site"}
        self.assertTrue(autofill.sign_ups_only([news, {**news, "label": "Send me job alerts"}]))
        self.assertFalse(autofill.sign_ups_only([news, {"kind": "text", "label": "Email"}]))
        self.assertFalse(autofill.sign_ups_only([{**news, "required": True}]))
        self.assertFalse(autofill.sign_ups_only([]))

    def test_questions_only_the_applicant_can_answer_are_left_for_them(self):
        """Regression (testers, 2026-11-05): the expected salary went into "What is your current salary?", and the
        model said "Yes" to "Have you interviewed with Jane Street before?"."""
        autofill.profile = lambda: {**PROFILE, "salary": "30000"}
        for label in ("What is your current salary?", "What is your current (or most recent) salary?",
                      "Have you interviewed with Practice Ltd before?", "Have you ever applied to us before?"):
            f = self.propose(label=label)
            self.assertEqual((f["value"], f["note"]), (None, autofill.YOURSELF), label)
            self.assertIsNotNone(autofill.left_to_you(f, "Practice Ltd"), label)
        self.assertEqual(self.propose(label="What is your desired salary?")["source"], "profile: salary")
        self.assertIsNone(autofill.left_to_you({"label": "Have you worked with Python before?"}, "Practice Ltd"))

    def test_a_high_school_or_second_subject_is_not_given_the_university_degree(self):
        """Regression (testers, 2026-11-05): "What year did you graduate high school?" and "High School" were given
        the university, and "Secondary Major" the degree."""
        autofill.profile = lambda: {**PROFILE, "university": "Southbrook University", "degree": "BSc Made-up Studies"}
        for label in ("What year did you graduate high school?", "High School", "Secondary Major"):
            self.assertNotIn(self.propose(label=label)["source"], ("profile: university", "profile: degree",
                                                                   "profile: grad_year"), label)
        self.assertEqual(self.propose(label="University")["value"], "Southbrook University")

    def test_a_label_asking_for_first_and_last_name_gets_the_full_name(self):
        """Regression (testers, 2026-11-05): "Legal First and Last Name" was given only the surname."""
        autofill.profile = lambda: {**PROFILE, "first_name": "Robin", "last_name": "Testwell",
                                    "full_name": "Robin Testwell"}
        for label in ("Legal First and Last Name", "First & last name"):
            self.assertEqual(self.propose(label=label)["value"], "Robin Testwell", label)
        self.assertEqual(self.propose(label="Last Name")["value"], "Testwell")

    def test_an_optional_follow_up_to_a_different_answer_is_left_empty(self):
        """Regression (testers, 2026-11-04): after "No" to sponsorship, the model answered "If you answered yes to
        the above, confirm your visa" with "A"."""
        fields = [{"hid": "h0", "kind": "radio", "label": "Do you need sponsorship?", "options": ["Yes", "No"],
                   "required": True},
                  {"hid": "h1", "kind": "text", "label": "If  you answered yes to the above, confirm your visa",
                   "required": False},
                  {"hid": "h2", "kind": "radio", "label": "Do you hold a made-up licence?", "options": ["Yes", "No"],
                   "required": True},
                  {"hid": "h3", "kind": "text", "label": "If yes, please give details", "required": False},
                  {"hid": "h4", "kind": "text", "label": "If you selected other, please specify", "required": False}]
        out = [{**f, "value": v} for f, v in zip(fields, ["No", None, "Yes", None, None], strict=True)]
        autofill._skip_follow_ups(out)
        self.assertEqual([f.get("skip", False) for f in out], [False, True, False, False, False])
        self.assertEqual(out[1]["why"], autofill.FOLLOW_UP_WHY)

    def test_what_the_site_already_filled_is_kept_when_hal_has_no_answer(self):
        """Regression (testers, 2026-11-04): a code the site had set to "+44" was counted as a required
        question Hal could not answer."""
        fields = [{"hid": "h0", "kind": "select", "label": "Made-up list", "required": True, "value": None,
                   "current": "Option B", "note": ""},
                  {"hid": "h1", "kind": "radio", "label": "A private question", "required": True, "value": None,
                   "current": "No", "note": "answer this yourself"},
                  {"hid": "h2", "kind": "checkbox", "label": "Send me news", "value": None, "current": False,
                   "note": ""}]
        kept = autofill.keep_site_answers(fields)
        self.assertEqual([(f["value"], f.get("source")) for f in kept],
                         [("Option B", "site: already filled"), (None, None), (None, None)])

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

    def test_a_written_answer_is_asked_for_and_kept_in_your_spelling(self):
        f = {"hid": "h0", "kind": "textarea", "type": "textarea", "label": "Describe a project you are proud of",
             "required": True}
        asked = []

        def model(system, user, **kw):
            asked.append(system)
            return {"answers": [{"hid": "h0", "value": "I organized the color catalog of a made-up archive.",
                                 "sure": True}]}
        saved = autofill.llm.chat_json, autofill.gpu.busy
        autofill.llm.chat_json, autofill.gpu.busy = model, lambda: None
        try:
            autofill.ask_model(self.job, [f], {})
        finally:
            autofill.llm.chat_json, autofill.gpu.busy = saved
        self.assertEqual(f["value"], "I organised the colour catalogue of a made-up archive.")
        self.assertIn("in UK English, with UK spelling", " ".join(asked[0].split()))

    def fill_with(self, answer: str, unsupported: list[str], docs: dict | None = None) -> tuple[dict, list]:
        """A written answer filled by a stand-in model whose check finds `unsupported`; the field and the asks."""
        f = {"hid": "h0", "kind": "textarea", "type": "textarea", "label": "Describe a project you are proud of",
             "required": True}
        asked = []

        def model(system, user, **kw):
            asked.append((system, user))
            if system.startswith("Check drafts"):
                return {"unsupported": unsupported}
            return {"answers": [{"hid": "h0", "value": answer, "sure": True}]}
        with mock.patch.object(autofill.llm, "chat_json", model), mock.patch.object(autofill.gpu, "busy", lambda: None):
            autofill.ask_model(self.job, [f], docs or {})
        return f, asked

    def test_a_written_answer_with_a_claim_your_facts_lack_is_marked_for_you(self):
        # autofill had no claim check at all (2026-11-06)
        f, asked = self.fill_with("I led a made-up archive team and catalogued the river boat records.",
                                  ["led a made-up archive team"])
        self.assertEqual(f["note"], "Hal is unsure; check this: not in your facts: led a made-up archive team")
        checks = [user for system, user in asked if system.startswith("Check drafts")]
        self.assertEqual(len(checks), 1, "one check for the whole form")
        self.assertIn("I led a made-up archive team", checks[0])

    def test_a_written_answer_with_a_number_no_fact_holds_is_marked_for_you(self):
        f, _ = self.fill_with("I catalogued 4,700 made-up river boat records for the archive.", [])
        self.assertEqual(f["note"], "Hal is unsure; check this: 4,700 is in none of your facts")

    def test_earlier_drafts_reach_the_form_filler_as_style_never_as_facts(self):
        # earlier drafts were fed back in as facts, so a claim one made up came back on the form (2026-11-06)
        docs = {"answers": [{"question": "Why us?", "answer": "I once ran a made-up lighthouse."}],
                "letter": "I bring years of made-up lighthouse work."}
        self.assertNotIn("lighthouse", autofill._facts(self.job, docs, None))
        _, asked = self.fill_with("I catalogued made-up river boat records for an archive.", [], docs)
        fill = next(user for system, user in asked if not system.startswith("Check drafts"))
        style = fill.split(autofill.STYLE_LABEL, 1)
        self.assertEqual(len(style), 2)
        self.assertNotIn("lighthouse", style[0])
        self.assertIn("I bring years of made-up lighthouse work.", style[1])
        self.assertIn("never take a fact from them", next(system for system, _ in asked
                                                          if not system.startswith("Check drafts")))

    def test_the_form_filler_knows_today_and_where_you_are(self):
        self.addCleanup(settings.set, "goal.study_end", settings.get("goal.study_end") or "", by="you")
        with mock.patch.dict("os.environ", {"HAL_EMPLOYMENT_FAKE_NOW": "2026-11-07T09:00:00Z"}):
            settings.set("goal.study_end", "2026-06", by="you")
            facts = autofill._facts(self.job, {}, None)
        self.assertTrue(facts.startswith("Today: 2026-11-07\nStage: Finished "), facts[:80])
        self.assertIn(" in 2026-06; ", facts.splitlines()[1])

    def test_a_salary_typed_for_one_job_is_never_offered_to_another(self):
        con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(con.close)
        other = {**self.job, "id": "test:2", "url": "https://jobs.example/2"}
        typed = {"hid": "h0", "kind": "text", "type": "text", "label": "What are your salary expectations?",
                 "value": "Made-up figure 31,500", "source": "you"}
        autofill.remember(con, [typed], "Practice Ltd", job=self.job)
        f = {"hid": "h0", "kind": "text", "type": "text", "label": "What are your salary expectations?",
             "required": True}
        self.assertEqual(autofill._recall(con, dict(f), self.job), "Made-up figure 31,500")
        self.assertIsNone(autofill._recall(con, dict(f), other))
        autofill.remember(con, [typed], "Practice Ltd")          # a form whose job is not known
        self.assertIsNone(autofill._recall(con, dict(f), other))
        self.assertEqual(con.execute("SELECT scope FROM answers2 WHERE scope_key = ''").fetchone()["scope"], "review")

    def test_a_short_why_us_answer_is_never_reused_word_for_word(self):
        con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(con.close)
        why = "Why do you want to work at Practice Ltd?"
        autofill.remember(con, [{"hid": "h0", "kind": "textarea", "label": why, "value": "Made-up short reason.",
                                 "source": "you"}], "Practice Ltd", job=self.job)
        nxt = {**self.job, "id": "test:3", "company": "Other Made-up Co"}
        f = {"hid": "h0", "kind": "textarea", "type": "text", "label": "Why do you want to work at Other Made-up Co?",
             "required": True}
        self.assertEqual(autofill.propose(nxt, [f], {}, {}, con)[0]["value"], None)
        self.assertEqual(con.execute("SELECT scope FROM answers2").fetchone()["scope"], "never")
        self.assertIn("Made-up short reason.", autofill._facts(nxt, {}, con))   # only as an example of their voice

    def test_a_box_for_an_emailed_code_is_left_for_the_code_step(self):
        """A code box is never filled from a saved answer, a profile fact or the AI."""
        con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(con.close)
        label = "Enter the security code we emailed you"
        autofill.remember(con, [{"hid": "h0", "kind": "text", "label": label, "value": "MADEUP12", "source": "you"}],
                          "Practice Ltd", job=self.job)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM answers2").fetchone()[0], 0)
        f = {"hid": "h0", "kind": "text", "type": "text", "label": label, "required": True}
        filled = autofill.propose(self.job, [f], {}, {}, con)[0]
        self.assertEqual((filled["value"], filled["skip"], filled["note"]), (None, True, autofill.CODE_NOTE))

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
        stack.enter_context(mock.patch.object(autofill, "_facts",
                                              lambda job, docs, con, notes=None: "Made-up CV facts."))
        stack.enter_context(mock.patch.object(autofill.record, "context", lambda questions: ""))
        self.stack = stack
        self.job = {"id": "test:1", "source": "adzuna", "company": "Practice Ltd", "title": "Junior Engineer",
                    "salary": "", "description": "", "location": "London"}

    def field(self, hid: str, label: str, **kw) -> dict:
        return {"hid": hid, "kind": "text", "type": "text", "label": label, "required": True, **kw}

    def sent(self, fake) -> str:
        return "\n".join(f"{r.system}\n{r.user}" for r in fake.log)

    def test_a_private_question_never_reaches_the_ai_whatever_its_source(self):
        self.stack.enter_context(private_answers.saved({"disability": "Yes", "adjustments": CANARY}))
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
        self.stack.enter_context(private_answers.saved({"disability": "Prefer not to say",
                                                         "disability_type": "Prefer not to say", "carer": "No"}))
        f = self.field("n2", "What do you enjoy about engineering? Yes, we ask everyone; prefer not to say is fine.")
        with fake_ai(default='{"answers": [{"hid": "n2", "value": "Careful work.", "sure": true}]}') as fake:
            answered, reason = autofill.ask_model(self.job, [f], {})
        self.assertEqual((answered, reason, f["value"]), (1, None, "Careful work."))
        self.assertEqual(len(fake.log), 1)

    def test_an_unanswered_private_question_is_left_to_you_not_guessed(self):
        self.stack.enter_context(private_answers.saved({}))
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
        kept = [tuple(r) for r in con.execute("SELECT label, answer FROM answers2")]
        self.assertEqual(kept, [("Which programming language do you use most?", '"Python"')])

    def test_the_carer_answer_is_never_in_the_profile_or_the_review(self):
        self.stack.enter_context(private_answers.saved({"carer": "No"}))
        self.stack.enter_context(people.as_person(people.CHANGER))
        settings.set("you.notice", "A month", by="setup")
        self.addCleanup(settings.reset, "you.notice", by="setup")
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

    def test_contact_details_come_from_your_profile(self):
        self.stack.enter_context(mock.patch.object(autofill, "profile", REAL_PROFILE))
        with people.as_person(people.TECHNICAL):
            prof = REAL_PROFILE()
            fields = [self.field("a", "Full name"), self.field("b", "Email address"), self.field("c", "Phone"),
                      self.field("d", "University"), self.field("e", "When could you start?")]
            autofill.propose(self.job, fields, {}, {})
        self.assertEqual([f["value"] for f in fields], ["Tobias Wrenfield", "toby.wrenfield@example.org",
                                                        "07700 900462", "Made-up Institute of Technology",
                                                        "Immediately"])
        self.assertEqual(prof["preferred_name"], "Toby")
        for key in autofill.PRIVATE_KEYS:
            self.assertNotIn(key, prof)

    def test_a_private_answer_canary_never_reaches_a_prompt(self):
        """G5 end to end: a form fill, its review, and the facts the AI is given, with every answer a canary."""
        self.stack.close()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-canary-")))
        self.stack.enter_context(paths.set_override(tmp))
        self.stack.enter_context(mock.patch.object(autofill.record, "context", lambda questions: ""))
        self.stack.enter_context(mock.patch.object(autofill.cv, "facts_text", lambda: "Made-up CV facts."))
        self.stack.enter_context(mock.patch.object(salary, "expect", lambda job, con=None: {
            "figure": 30000, "text": "Made-up 30,000", "basis": "made up"}))
        self.stack.enter_context(people.as_person(people.HUMANITIES))
        self.stack.enter_context(private_answers.saved({k: CANARY for k in autofill.PRIVATE_KEYS}))
        con = db.connect()
        self.addCleanup(con.close)
        fields = [self.field(f"p{i}", label) for i, label in enumerate(PRIVATE_LABELS)]
        fields += [self.field("n1", "Full name"), self.field("n2", "What draws you to this team?")]
        autofill.propose(self.job, fields, {}, {}, con)
        typed = [dict(f, value=CANARY, source="you") for f in fields if f["hid"].startswith("p")]
        autofill.remember(con, typed, "Practice Ltd", job=self.job)
        with fake_ai({"re:You check answers": '{"wrong": []}'}, default='{"answers": []}') as fake:
            autofill.ask_model(self.job, fields, {}, con)
            autofill.choose_control(self.job, {"title": "Jobs", "url": "https://jobs.example/1", "controls": [
                {"name": label} for label in PRIVATE_LABELS] + [{"name": "Apply now"}]})
        sent = self.sent(fake)
        self.assertIn("Marnie Quillfeather", sent)
        self.assertNotIn(CANARY, sent)
        for label in PRIVATE_LABELS:
            self.assertNotIn(label, sent)

    def test_the_test_invitation_todo_says_nothing_about_private_answers(self):
        con = db.connect()
        self.addCleanup(con.close)
        con.execute("""INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, role, job_id, snippet)
                       VALUES ('m1', '1', ?, 'talent@acme.example', '', 'Your online assessment', 'assessment', 'Acme',
                               'Engineer', NULL, '')""", (db.now(),))
        todos = []
        for answers in ({}, {"adjustments": CANARY, "disability": "Yes"}):
            con.execute("DELETE FROM todos")
            with private_answers.saved(answers):
                tasks.sync(con)
            rows = con.execute("SELECT title, detail FROM todos ORDER BY priority, id")
            todos.append([(r["title"], r["detail"]) for r in rows])
        self.assertEqual(todos[0], todos[1])
        self.assertEqual([t for t, _ in todos[0]], [tasks.CHECK_TITLE, "Complete the Acme assessment"])
        words = " ".join(f"{t} {d}" for t, d in todos[0]).lower()
        for hint in ("private", "extra time", "adjust", "disab", "toml", CANARY.lower()):
            self.assertNotIn(hint, words)


class WhyThisAnswer(unittest.TestCase):
    """Answers from notes about the job site, their order, and "Why this answer?"."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-why-")))
        stack.enter_context(paths.set_override(tmp))
        stack.enter_context(mock.patch.object(autofill, "profile", lambda: dict(PROFILE)))
        self.notes = site_notes
        self.con = db.connect()
        stack.callback(self.con.close)
        self.stack = stack
        self.job = {"id": "test:1", "source": "adzuna", "company": "Civic Made-up Ltd", "title": "Junior Researcher",
                    "salary": "", "description": "", "location": "Leeds",
                    "url": "https://civicmadeup.example/jobs/1"}

    def field(self, hid: str, label: str, **kw) -> dict:
        return {"hid": hid, "kind": "text", "type": "text", "label": label, "required": True, **kw}

    def note(self, label: str, value: str, site: str = "civicmadeup.example", kind: str = "field_answer") -> str:
        cond, act, text = {"label": label}, {"value": value}, f"Answer \"{label}\" with \"{value}\" here"
        if autofill.private_question({"label": label}) is None:
            return self.notes.add("site", site, kind, cond, act, text)
        # The store refuses a note for a private question; one already stored past it must still never apply.
        with self.assertRaises(site_notes.NoteError):
            self.notes.add("site", site, kind, cond, act, text)
        note_id = f"test-private-{self.con.execute('SELECT COUNT(*) FROM site_notes').fetchone()[0]}"
        self.con.execute("""INSERT INTO site_notes (id, scope_kind, scope_key, kind, cond_json, act_json, text, origin,
                                                    status, private, evidence, created, updated)
                            VALUES (?, 'site', ?, ?, ?, ?, ?, 'you', 'active', 1, '[]', ?, ?)""",
                         (note_id, site, kind, json.dumps(cond), json.dumps(act), text, db.now(), db.now()))
        self.con.commit()
        return note_id

    def saved(self, label: str, answer: str, scope: str = "everywhere", key: str = "") -> None:
        self.con.execute("""INSERT INTO answers2 (question, label, answer, scope, scope_key, updated)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                         (autofill._norm(label), label, db.json_text(answer), scope, key, db.now()))
        self.con.commit()

    def test_a_site_note_answer_is_used_only_on_that_site(self):
        self.note("how did you hear", "Company website")
        here = autofill.propose(self.job, [self.field("h", "How did you hear about this role?")], {}, {}, self.con)[0]
        elsewhere = {**self.job, "id": "test:2", "url": "https://othermadeup.example/jobs/2"}
        there = autofill.propose(elsewhere, [self.field("h", "How did you hear about this role?")], {}, {}, self.con)[0]
        self.assertEqual((here["value"], here["source"]), ("Company website", "note: field_answer"))
        self.assertEqual([n["text"] for n in here["notes_used"]],
                         ['Answer "how did you hear" with "Company website" here'])
        self.assertIn("civicmadeup.example", here["why"])
        # elsewhere the built-in note for jobs found on Adzuna answers, as HOW_HEARD did before notes
        self.assertEqual((there["value"], there["source"]), ("Adzuna", "note: field_answer"))
        self.assertEqual([n["origin"] for n in there["notes_used"]], ["builtin"])

    def test_answers_waiting_for_a_choice_are_never_used(self):
        self.saved("Do you hold a full driving licence?", "Made-up yes", scope="review")
        self.saved("What is your favourite archive?", "Made-up Record Office", scope="review")
        fields = [self.field("d", "Do you hold a full driving licence?"),
                  self.field("a", "What is your favourite archive?")]
        autofill.propose(self.job, fields, {}, {}, self.con)
        self.assertEqual([(f["value"], f["source"]) for f in fields], [(None, "you"), (None, "you")])
        self.assertNotIn("Made-up Record Office", autofill._facts(self.job, {}, self.con))
        self.assertEqual(self.con.execute("SELECT SUM(uses) FROM answers2").fetchone()[0], 0)

    def test_each_field_says_where_its_answer_came_from(self):
        self.stack.enter_context(private_answers.saved({"adjustments": "No adjustments needed"}))
        self.note("location", "Leeds")
        self.saved("Do you hold a full driving licence?", "Yes")
        fields = [self.field("n", "Full name"), self.field("l", "Location"),
                  self.field("d", "Do you hold a full driving licence?"),
                  self.field("c", "Upload your CV", kind="file", type="file"),
                  self.field("p", "Do you need any adjustments for the interview?"),
                  self.field("w", "Why do you want this job?", kind="textarea")]
        autofill.propose(self.job, fields, {}, {"cv": "Made-up CV.pdf"}, self.con)
        reply = {"answers": [{"hid": "w", "value": "Cataloguing the made-up parish collection taught me patience "
                                                    "with records.", "sure": True}]}
        cv_text = "Archive Volunteer, Made-up Record Office (2025): cataloguing the parish collection records"
        with mock.patch.object(autofill.cv, "facts_text", lambda: cv_text), \
                mock.patch.object(autofill.record, "context", lambda questions: ""), \
                fake_ai({"re:You check answers": '{"wrong": []}'}, default=json.dumps(reply)):
            autofill.ask_model(self.job, fields, {}, self.con)
        why = {f["hid"]: f["why"] for f in fields}
        self.assertEqual(why["n"], "From your profile (Settings, You): your name.")
        self.assertIn('because of your note for civicmadeup.example: "Answer \"location\"', why["l"])
        self.assertIn('You gave this answer before, to "Do you hold a full driving licence?"', why["d"])
        self.assertIn("every form", why["d"])
        self.assertEqual(why["c"], "Your CV for this job, as you approved it.")
        self.assertEqual(why["p"], autofill.PRIVATE_WHY)
        self.assertIn("Written for this job by Hal from your CV (\"Archive Volunteer, Made-up Record Office", why["w"])
        self.assertEqual(self.con.execute("SELECT uses FROM answers2").fetchone()[0], 1)

    def test_private_answers_say_only_that_they_are_private(self):
        self.stack.enter_context(private_answers.saved({"disability": "Yes", "adjustments": CANARY, "carer": "No"}))
        for label in PRIVATE_LABELS:
            self.note(label, CANARY)
        fields = [self.field(f"p{i}", label) for i, label in enumerate(PRIVATE_LABELS)]
        fields.append(self.field("q", "Do you consider yourself to have a long-term health condition?",
                                 kind="radio", options=["Yes, limited a lot", "Yes, limited a little", "No"]))
        autofill.propose(self.job, fields, {}, {}, self.con)
        for f in fields:
            self.assertIn(f["why"], (autofill.PRIVATE_WHY, autofill.LEFT_PRIVATE_WHY), f["label"])
            self.assertEqual(f["notes_used"], [])
        self.assertEqual([f["why"] for f in fields if f["source"].startswith("private")],
                         [autofill.PRIVATE_WHY] * 4)
        from jobhunt.apply import queries
        shown = queries._fields({"fields": fields})
        self.assertNotIn(CANARY, json.dumps(shown))
        self.assertEqual({x["why"] for x in shown if x["src"] == "private"}, {autofill.PRIVATE_WHY})

    def test_site_answers_come_after_private_and_carer_answers_and_before_saved_ones(self):
        self.stack.enter_context(private_answers.saved({"carer": "No"}))
        self.note("caring responsibilities", "Made-up note answer")
        self.note("notice period", "Two weeks")
        self.saved("What is your notice period?", "Made-up saved notice")
        self.saved("Do you hold a full driving licence?", "Yes")
        fields = [self.field("c", "Do you have any caring responsibilities?", kind="radio", options=["Yes", "No"]),
                  self.field("n", "What is your notice period?"),
                  self.field("d", "Do you hold a full driving licence?")]
        autofill.propose(self.job, fields, {}, {}, self.con)
        self.assertEqual([(f["value"], f["source"]) for f in fields],
                         [("No", "private: carer"), ("Two weeks", "note: field_answer"),
                          ("Yes", "your earlier answer")])

    def test_a_list_that_takes_several_answers_gets_every_answer(self):
        """Regression: a list answer was cut to its first item, so a multi-select got one language."""
        langs = {"hid": "l", "kind": "select", "label": "Languages", "multiple": True,
                 "options": ["English", "French", "Welsh"]}
        self.assertEqual(autofill._fit(langs, ["French", "Welsh"], []), ["French", "Welsh"])
        self.assertEqual(autofill._fit({**langs, "multiple": False}, ["French", "Welsh"], []), "French")

    def test_a_skip_note_leaves_the_field_alone_and_says_so(self):
        self.note("referral code", "", kind="field_skip")
        f = autofill.propose(self.job, [self.field("r", "Referral code")], {}, {}, self.con)[0]
        self.assertEqual((f["skip"], f["source"]), (True, "note: field_skip"))
        self.assertTrue(f["why"].startswith("Left as it is because of your note for civicmadeup.example"))

    def test_free_words_notes_reach_the_ai_but_never_one_about_a_private_question(self):
        self.notes.add("site", "civicmadeup.example", "note", {}, {}, "This site calls the CV a portfolio")
        self.notes.add("site", "civicmadeup.example", "note", {}, {}, "Say yes to the disability question")
        facts = autofill._facts(self.job, {}, self.con, notes=autofill.page_notes(self.job))
        self.assertIn("This site calls the CV a portfolio", facts)
        self.assertNotIn("disability", facts)


if __name__ == "__main__":
    unittest.main()

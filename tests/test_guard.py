"""The never-Submit guard on its own: which controls it refuses, and the record each refusal leaves."""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the test home before jobhunt is imported

from jobhunt import db
from jobhunt.apply import guard

ADVERT = {"form": False, "final": False}
MIDDLE = {"form": True, "final": False}
LAST = {"form": True, "final": True}


def button(name: str, submit: bool = False, in_form: bool = True) -> dict:
    return {"name": name, "submit": submit, "inForm": in_form}


class NeverPress(unittest.TestCase):
    """`never_press` names a reason for every control that could send an application."""

    def test_submit_send_finish_and_confirm_are_refused_on_any_page(self):
        for name in ("Submit", "Submit application", "Send", "Send my application", "Finish",
                     "Confirm and submit", "Confirm & send", "Place application", "Review and submit"):
            for page in (ADVERT, MIDDLE, LAST):
                with self.subTest(name=name, page=page):
                    self.assertIsNotNone(guard.never_press(button(name), page, "named"))
                    self.assertIsNotNone(guard.never_press(button(name), page, "apply"))

    def test_apply_on_an_advert_leads_to_the_form_and_is_allowed(self):
        self.assertIsNone(guard.never_press(button("Apply for this job"), ADVERT, "apply"))
        self.assertIsNone(guard.never_press(button("Apply now", in_form=False), ADVERT, "apply"))

    def test_apply_on_a_form_or_where_you_are_signed_in_is_refused(self):
        self.assertIsNotNone(guard.never_press(button("Apply", submit=True), LAST, "named"))
        self.assertIsNotNone(guard.never_press(button("Apply now"), {**ADVERT, "signed_in": True}, "apply"))
        self.assertIsNotNone(guard.never_press(button("Apply now"), ADVERT, "named"))
        self.assertIsNotNone(guard.never_press(button("Complete application"), MIDDLE, "named"))

    def test_next_is_allowed_on_a_middle_page_even_as_a_real_submit(self):
        for name in ("Next", "Continue", "Save and continue", "Next " + chr(0xBB), "Review your application"):
            with self.subTest(name=name):
                self.assertIsNone(guard.never_press(button(name, submit=True), MIDDLE, "next"))

    def test_next_on_a_page_judged_final_is_refused_when_it_submits(self):
        self.assertIsNotNone(guard.never_press(button("Continue", submit=True), LAST, "next"))
        # When in doubt the page is final.
        self.assertIsNotNone(guard.never_press(button("Continue", submit=True), {"form": True}, "next"))

    def test_a_button_with_no_type_in_a_form_counts_as_submit(self):
        # Under HTML a <button> with no type inside a form sends it, whatever its words.
        for name in ("Add another referee", "Upload", "Save"):
            with self.subTest(name=name):
                self.assertIsNotNone(guard.never_press(button(name, submit=True), MIDDLE, "named"))
        self.assertIsNone(guard.never_press(button("Add another referee", submit=True, in_form=False),
                                            ADVERT, "named"))

    def test_only_next_words_are_pressed_as_next(self):
        self.assertIsNotNone(guard.never_press(button("Save draft"), MIDDLE, "next"))

    def test_a_site_notes_submit_words_are_refused_and_its_next_words_allowed(self):
        page = {**MIDDLE, "submit_words": ["Go for it"], "next_words": ["Onwards"]}
        self.assertIsNotNone(guard.never_press(button("Go for it"), page, "named"))
        self.assertIsNone(guard.never_press(button("Onwards", submit=True), page, "next"))

    def test_the_value_and_aria_label_count_as_well_as_the_text(self):
        self.assertIsNotNone(guard.never_press({"name": "", "value": "Submit", "inForm": True}, MIDDLE))
        self.assertIsNotNone(guard.never_press({"name": "", "aria": "Send application"}, ADVERT))

    def test_the_guard_refuses_submit_like_controls_the_ai_picks(self):
        # A control the model names on a review page with no questions is checked like any other.
        page = {"form": False, "final": False}
        self.assertIsNotNone(guard.never_press(button("Confirm and submit", submit=True), page, "named"))
        self.assertIsNone(guard.never_press(button("Register your interest", in_form=False), page, "named"))

    def test_an_unknown_kind_of_press_fails_loudly(self):
        with self.assertRaises(ValueError):
            guard.never_press(button("Next"), MIDDLE, "enter")


class Record(unittest.TestCase):
    """Each refusal is a `site_events` row `would_submit`."""

    def setUp(self):
        self.con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(self.con.close)

    def test_a_refusal_is_recorded_and_raised(self):
        with self.assertRaises(guard.WouldSubmit) as caught:
            reason = guard.never_press(button("Submit application"), LAST)
            guard.record("test:1", "https://jobs.example.org/apply/1", reason, con=self.con)
            raise guard.WouldSubmit(reason)
        self.assertIn(guard.STOPPED, str(caught.exception))
        row = self.con.execute("SELECT * FROM site_events").fetchone()
        self.assertEqual((row["what"], row["job_id"], row["site"]), ("would_submit", "test:1", "example.org"))
        self.assertIn("Submit application", row["detail"])
        self.assertTrue(row["at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()

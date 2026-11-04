"""Scoring rules with reasons: internships, eligibility, unpaid roles and the kinds a person did not choose.

Every advert, employer and person here is made up.
"""

import unittest
from datetime import date
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.jobs import advert

from jobhunt.find import goal as goal_mod
from jobhunt.find import rules

TODAY = date(2026, 10, 22)
GRADUATE = {"goal.stage": "new_grad", "goal.study_status": "finished", "goal.study_end": "2026",
            "goal.kinds": ["graduate_job", "graduate_scheme", "internship", "fixed_term"]}
STUDENT = {"goal.stage": "student", "goal.study_status": "at_uni", "goal.study_end": "2028-06",
           "goal.kinds": ["internship", "placement", "insight", "graduate_scheme", "part_time", "fixed_term"]}
PAID_INTERN = ("A paid summer internship in our analytics team. Open to recent graduates and students. "
               "\u00a32,000 per month.")
SPRING = "A three-day spring insight week for first and second year undergraduates. Expenses are covered."
STUDENTS_ONLY = ("Our 12-month industrial placement is for current students only: you must be returning to "
                 "university to finish your degree afterwards.")


def make_goal(values: dict, **more) -> goal_mod.Goal:
    return goal_mod.from_values({**values, **more})


def run(job: dict, values: dict, preset: str = "humanities_grad", **more) -> rules.Scored:
    with mock.patch("jobhunt.core.clock.local_day", return_value=TODAY):
        return rules.score(job, make_goal(values, **more), rules.weights_from(preset))


class OutsideYourField(unittest.TestCase):
    """A humanities search hides specialist technical titles unless the person's own roles name them."""

    ML = "Join our research team building machine learning systems. Graduates welcome. \u00a335,000."

    def test_a_machine_learning_engineer_is_hidden_from_a_humanities_graduate(self):
        # testers saw "Machine Learning Engineer" in a humanities graduate's matches (2026-11-03)
        scored = run(advert("Graduate Machine Learning Engineer", self.ML), GRADUATE,
                     **{"goal.roles": ["museum assistant", "archivist"]})
        self.assertEqual(scored.hidden.rule, "off_field")
        self.assertEqual(scored.hidden.label, "Outside your field: machine learning")

    def test_a_humanities_graduate_with_no_roles_also_has_it_hidden(self):
        scored = run(advert("Junior Software Developer", self.ML), GRADUATE)
        self.assertEqual(scored.hidden.rule, "off_field")

    def test_a_role_the_person_wants_is_never_hidden_as_outside_their_field(self):
        scored = run(advert("Data Engineer (Graduate)", self.ML), GRADUATE, **{"goal.roles": ["data engineer"]})
        self.assertIsNone(scored.hidden)

    def test_humanities_roles_with_technical_sounding_words_stay(self):
        for title in ("Learning Developer", "Digital Archivist", "Curriculum Developer"):
            with self.subTest(title=title):
                self.assertIsNone(run(advert(title, "A graduate role in our museum."), GRADUATE).hidden)


class Internships(unittest.TestCase):
    """Internships are first-class when the goal includes them."""

    def test_a_paid_internship_is_shown_when_the_goal_includes_internships(self):
        scored = run(advert("Summer Analyst Internship", PAID_INTERN), GRADUATE)
        self.assertIsNone(scored.hidden)
        self.assertGreater(scored.score, 0)
        self.assertEqual(scored.kind.kind, "internship")
        self.assertIn("graduate internship", scored.notes)
        self.assertNotIn("kind_wanted", [r.rule for r in scored.reasons if r.points < 0])

    def test_an_internship_is_hidden_with_its_reason_when_the_goal_leaves_it_out(self):
        scored = run(advert("Summer Analyst Internship", PAID_INTERN), GRADUATE,
                     **{"goal.kinds": ["graduate_job"]})
        self.assertEqual(scored.hidden.rule, "kind_not_chosen:internship")
        self.assertEqual(scored.hidden.label, "Paid internships: not a kind of role you chose")
        self.assertEqual(scored.score, 0)

    def test_a_graduate_does_not_see_students_only_schemes_and_sees_why(self):
        values = {**GRADUATE, "goal.kinds": GRADUATE["goal.kinds"] + ["placement"]}
        scored = run(advert("Industrial Placement", STUDENTS_ONLY), values)
        self.assertEqual(scored.hidden.rule, "students_only")
        self.assertEqual(scored.hidden.label, "For current students only")
        self.assertEqual(scored.hidden.data["why"], "For current students only. You finished your course in 2026.")
        self.assertIn("current students", scored.hidden.quote)

    def test_a_current_student_sees_spring_weeks(self):
        scored = run(advert("Spring Insight Week 2027", SPRING), STUDENT, "internship_seeker")
        self.assertIsNone(scored.hidden)
        self.assertEqual(scored.kind.kind, "insight")
        graduate = run(advert("Spring Insight Week 2027", SPRING), GRADUATE)
        self.assertEqual(graduate.hidden.rule, "kind_not_chosen:insight")

    def test_students_only_years_follow_the_persons_own_graduation(self):
        text = "A summer internship for students graduating in 2028. Paid \u00a3420 per week."
        mine = run(advert("Software Internship", text), STUDENT, "internship_seeker")
        self.assertIsNone(mine.hidden)
        later = run(advert("Software Internship", text), STUDENT, "internship_seeker", **{"goal.study_end": "2027"})
        self.assertEqual(later.hidden.rule, "grad_year")
        self.assertEqual(later.hidden.label, "For people finishing in 2028")
        finished = run(advert("Software Internship", text), GRADUATE)
        self.assertEqual(finished.hidden.rule, "students_only")

    def test_unpaid_roles_are_hidden_unless_asked_for(self):
        job = advert("Research Internship", "An unpaid internship, expenses only, two days a week.")
        hidden = run(job, GRADUATE)
        self.assertEqual(hidden.hidden.rule, "unpaid")
        self.assertEqual(hidden.hidden.label, "Unpaid or expenses only")
        shown = run(job, GRADUATE, **{"goal.unpaid_ok": True})
        self.assertNotEqual((shown.hidden or rules.Reason("", "", 0, "", "")).rule, "unpaid")

    def test_fixed_term_costs_points_instead_of_hiding(self):
        text = "A 12-month contract as a graduate assistant, maternity cover."
        chose = run(advert("Graduate Assistant", text), GRADUATE)
        did_not = run(advert("Graduate Assistant", text), GRADUATE, **{"goal.kinds": ["graduate_job"]})
        self.assertIsNone(did_not.hidden)
        also = next(r for r in did_not.reasons if r.rule == "kind_also:fixed_term")
        self.assertEqual(also.label, "Fixed-term, which you did not choose")
        self.assertEqual(also.points, rules.weights_from("humanities_grad")["kind_also"])
        self.assertEqual(chose.score - did_not.score, -also.points)


class Reasons(unittest.TestCase):
    """Every verdict is kept; the first hide rule is the one shown."""

    def test_every_hide_rule_is_recorded_and_the_first_one_hides(self):
        job = advert("Senior Internship", "An unpaid role.", location="Ravensmoor")
        scored = run(job, GRADUATE, **{"goal.places": ["Northby"]})
        hides = [r.rule for r in scored.reasons if r.hide]
        self.assertEqual(hides[0], scored.hidden.rule)
        self.assertIn("too_far", hides)
        self.assertIn("unpaid", hides)

    def test_an_employer_you_avoid_is_hidden_whatever_its_suffix(self):
        scored = run(advert("Graduate Analyst", company="Brightwater Bets Ltd"), GRADUATE,
                     **{"goal.avoid_employers": ["# Gambling", "brightwater bets"]})
        self.assertEqual(scored.hidden.rule, "excluded_employer")

    def test_roles_and_interests_from_the_goal_give_points(self):
        job = advert("Museum Assistant", "Join our collections team caring for local history archives.")
        scored = run(job, GRADUATE, **{"goal.roles": ["museum assistant"], "goal.interests": ["local history"]})
        rules_seen = {r.rule: r for r in scored.reasons}
        self.assertEqual(rules_seen["role_title"].label, "\"Museum Assistant\" is a role you want")
        self.assertIn("local history", rules_seen["interest"].label)


if __name__ == "__main__":
    unittest.main()

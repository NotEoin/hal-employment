"""What an advert requires that the person plainly does not meet, and the place they plainly can work in.

The people and adverts are made up; no database or AI is read except where `person()` is tested.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import eligibility
from jobhunt.writing.eligibility import Person

GRADUATE = Person(level="bachelor", needs_sponsorship=True, years=0, places=("Made-up Town",))


class Blockers(unittest.TestCase):
    def test_a_higher_degree_more_years_and_no_sponsorship_are_blockers(self):
        found = eligibility.blockers(["PhD in Imaginary Studies", "3 years of experience in a made-up trade",
                                      "You must have the right to work in the UK; we cannot offer sponsorship"],
                                     GRADUATE)
        self.assertEqual([f.split(" (")[0] for f in found],
                         ["ELIGIBILITY: the advert requires a PhD", "ELIGIBILITY: the advert requires 3 years of "
                          "experience", "ELIGIBILITY: the advert requires the right to work in the UK without "
                          "sponsorship"])

    def test_what_is_not_plain_is_never_a_blocker(self):
        self.assertEqual(eligibility.blockers(["A BSc or MSc in a made-up subject", "An MSc or equivalent experience",
                                               "A PhD is desirable"], GRADUATE), [])
        self.assertEqual(eligibility.blockers(["A Master's degree"], Person()), [], "an unknown level says nothing")


class Place(unittest.TestCase):
    def test_office_days_where_you_live_or_will_move_are_settled(self):
        gap = "Office attendance in Made-up Town three days a week"
        self.assertTrue(eligibility.where_you_live_settles(gap, GRADUATE, ["Made-up Town"]))
        self.assertTrue(eligibility.where_you_live_settles(gap, Person(relocate=True), ["Elsewhere"]))
        self.assertFalse(eligibility.where_you_live_settles(gap, Person(places=("Other Town",)), ["Made-up Town"]))
        self.assertFalse(eligibility.where_you_live_settles("Spreadsheets", GRADUATE, ["Made-up Town"]))


if __name__ == "__main__":
    unittest.main()

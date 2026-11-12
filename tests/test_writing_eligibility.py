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


# A made-up graduate scheme's advert, paraphrasing the shape of a real one: a degree asked for, other
# graduates and Master's holders "also considered".
SCHEME = ("Coastal Code Graduate Scheme\n\nAbout you\n- You should have a degree in Computing or a related subject "
          "by the time you start.\n- We will also consider graduates in Maths or Physics, or those with a "
          "Master's, who can show strong coding skills.\n\nNice to have\n- A PhD in tide modelling.\n"
          "We'd love to hear from you if you have a Master's in Sea Science.")
SCHEME_READING = ["A degree in Computing or a related subject",
                  "Maths or Physics graduates, or a Master's, with strong coding skills", "A PhD in tide modelling"]


class Conditions(unittest.TestCase):
    def test_a_degree_the_advert_only_also_considers_is_never_a_blocker(self):
        # an advert that asked for a relevant degree and "also considers" Master's holders was flagged
        # "ELIGIBILITY: requires a Master's" (2026-11-07)
        self.assertEqual(eligibility.blockers(SCHEME_READING, GRADUATE, SCHEME), [])
        self.assertTrue(eligibility.unfounded_degree("Requires a Master's degree", SCHEME))

    def test_softening_words_are_never_a_condition(self):
        for said in ("Master's holders will also be considered", "A PhD is welcome", "A Master's would be nice to have",
                     "We'd love to hear from you if you have a PhD", "An MSc is desirable"):
            with self.subTest(said):
                self.assertEqual(eligibility.blockers([said], GRADUATE), [])
                self.assertEqual(eligibility.conditions(said + "."), [])

    def test_requirement_phrasing_or_an_essential_heading_makes_a_condition(self):
        advert = ("Tide Analyst\n\nA PhD in tide modelling is essential.\n\nEssential criteria\n"
                  "- At least 3 years of experience in a made-up trade\n\nAbout the team\n- An MSc in Sea Science")
        found = eligibility.blockers(["PhD in tide modelling", "3 years of experience in a made-up trade",
                                      "An MSc in Sea Science"], GRADUATE, advert)
        self.assertEqual([f.split(" (")[0] for f in found],
                         ["ELIGIBILITY: the advert requires a PhD",
                          "ELIGIBILITY: the advert requires 3 years of experience"])
        self.assertFalse(eligibility.unfounded_degree("Requires a PhD", advert))


class Place(unittest.TestCase):
    def test_office_days_where_you_live_or_will_move_are_settled(self):
        gap = "Office attendance in Made-up Town three days a week"
        self.assertTrue(eligibility.where_you_live_settles(gap, GRADUATE, ["Made-up Town"]))
        self.assertTrue(eligibility.where_you_live_settles(gap, Person(relocate=True), ["Elsewhere"]))
        self.assertFalse(eligibility.where_you_live_settles(gap, Person(places=("Other Town",)), ["Made-up Town"]))
        self.assertFalse(eligibility.where_you_live_settles("Spreadsheets", GRADUATE, ["Made-up Town"]))

    def test_the_reason_says_why_in_plain_words_even_when_the_advert_gives_no_town(self):
        # "London office three days a week" was partial, "no evidence of refusal", for a person in London
        # (2026-11-07); the advert's location said only "Hybrid"
        row = "Made-up Town office three days a week"
        self.assertEqual(eligibility.where_you_live_reason(row, GRADUATE, []), "You live in Made-up Town")
        self.assertEqual(eligibility.where_you_live_reason(row, Person(relocate=True), []),
                         "Your settings say you will relocate")
        self.assertEqual(eligibility.where_you_live_reason("In the office three days a week", Person(office=True), []),
                         "Your settings say you are happy to work in an office")
        self.assertEqual(eligibility.where_you_live_reason(row, Person(places=("Other Town",)), []), "")


if __name__ == "__main__":
    unittest.main()

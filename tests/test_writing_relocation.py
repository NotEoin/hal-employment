"""No offer to relocate to the town the person already lives in.

The people, towns and drafts are made up.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import relocation
from jobhunt.writing.eligibility import Person

AT_HOME = Person(places=("Made-up Town", "4 Quay Lane, Made-up Town"))


class OwnTown(unittest.TestCase):
    def test_an_offer_to_relocate_to_your_own_town_is_taken_out(self):
        # "happy to relocate to your London office" for a person living in London (2026-11-07)
        for said, left in (
                ("I am available to start immediately and happy to relocate to your Made-up Town office.",
                 "I am available to start immediately."),
                ("I can start now, and I am willing to move to Made-up Town, so the office suits me.",
                 "I can start now, so the office suits me."),
                ("I am happy to relocate to Made-up Town. I built a tide gauge.", "I built a tide gauge."),
                ("Dear Ada,\n\nI catalogued letters.\n\nI would relocate to made-up town.\n\nBest wishes,",
                 "Dear Ada,\n\nI catalogued letters.\n\nBest wishes,")):
            with self.subTest(said):
                self.assertEqual(relocation.without_own_move(said, AT_HOME), left)

    def test_a_move_elsewhere_a_past_move_or_no_town_is_left_alone(self):
        for said in ("I am happy to relocate to Other Town.", "I moved to Made-up Town in 2020.",
                     "I am happy to relocate if needed."):
            with self.subTest(said):
                self.assertEqual(relocation.without_own_move(said, AT_HOME), said)
        said = "I am happy to relocate to Made-up Town."
        self.assertEqual(relocation.without_own_move(said, Person()), said, "no town in the settings")


if __name__ == "__main__":
    unittest.main()

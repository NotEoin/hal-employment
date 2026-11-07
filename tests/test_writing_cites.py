"""Evidence markers: read with each sentence, taken out before anyone sees the draft, and checked."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import cites

unmarked = cites.strip     # the draft as the person sees it (a name ruff does not take for `str.strip`)

DRAFT = ("I wrote the statistics features of the Ferry planner with six classmates [E1]. Your timetable work is "
         "why I am applying [M].\n\nI logged river levels every ten minutes. [E2, E3] I would like to talk about "
         "the role [M].")


class Markers(unittest.TestCase):
    def test_the_person_sees_the_draft_without_markers_and_with_its_paragraphs(self):
        self.assertEqual(cites.strip(DRAFT),
                         "I wrote the statistics features of the Ferry planner with six classmates. Your timetable "
                         "work is why I am applying.\n\nI logged river levels every ten minutes. I would like to "
                         "talk about the role.")
        self.assertFalse(cites.has_markers(cites.strip(DRAFT)))
        self.assertTrue(cites.has_markers(DRAFT))

    def test_each_sentence_keeps_its_own_marker_even_after_its_full_stop(self):
        read = cites.cited(DRAFT)
        self.assertEqual([(c.ids, c.motive) for c in read],
                         [(("E1",), False), ((), True), (("E2", "E3"), False), ((), True)])
        self.assertEqual(read[2].text, "I logged river levels every ten minutes.")
        self.assertEqual(cites.normalise("It worked. [E4]"), "It worked [E4].")

    def test_a_marker_with_no_full_stop_after_it_still_ends_its_sentence(self):
        # a local model left the stop out ("on one consumer GPU [E1] This project shows"), and taking the markers
        # out joined the sentences: "I built Hal It runs offline" (2026-11-07)
        self.assertEqual(unmarked("I built Hal [E1] It runs offline [E2]"), "I built Hal. It runs offline.")
        self.assertEqual(unmarked("It runs on one consumer GPU [E1] This project shows care [E1]."),
                         "It runs on one consumer GPU. This project shows care.")
        self.assertEqual(unmarked("I used NumPy [E2] How does the team test its models [M]?"),
                         "I used NumPy. How does the team test its models?")
        self.assertEqual(unmarked('I logged levels [E2] "Tide gauge" ran for a year [E2]\n\nThanks [M]'),
                         'I logged levels. "Tide gauge" ran for a year.\n\nThanks.')
        self.assertEqual(unmarked("I used Python [E1] and SQL [E3]."), "I used Python and SQL.",
                         "a marker before a small word is inside its sentence")

    def test_each_sentence_after_a_marker_is_read_on_its_own(self):
        read = cites.cited("I built Hal [E1] It runs offline [E2] 3 people use it [E3]. then it rests [M].")
        self.assertEqual([(c.text, c.ids) for c in read],
                         [("I built Hal.", ("E1",)), ("It runs offline.", ("E2",)), ("3 people use it.", ("E3",)),
                          ("then it rests.", ())])

    def test_what_counts_as_about_you(self):
        about = ["I led the weekly tours.", "My team won the regional prize.", "We wrote 12 reports.",
                 "The archive holds 400 letters."]
        not_about = ["I am excited by your flood sensors.", "Your focus on rivers interested me.",
                     "I would like to hear about the team's work.", "I feel prepared for the interview."]
        self.assertEqual([s for s in about if not cites.about_person(s)], [])
        self.assertEqual([s for s in not_about if cites.about_person(s)], [])


if __name__ == "__main__":
    unittest.main()

"""What a number in a draft counts, against what the facts count: the drafts and facts are made up."""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import counts

COUNT_FACTS = ("Harbour bot: a scripted intruder was contained in all 9 break-in tests.\n- Logs: kept 30 days.\n"
               "Modules: Coastal Mapping and Tide Studies 74. Tests run on Linux and Windows.\n"
               "CI: tests on three Lua versions. Cut one build from 40 minutes to 8 by splitting it across 12 jobs.\n"
               "Ferry planner: built in a team of five, 2023 to 2025.")


class Counts(unittest.TestCase):
    """Real letters counted a number of tests as a number of things tested, and claimed a span of years no fact
    gave (2026-11-19). The facts here are made up in the same shape."""

    def test_a_number_counting_something_else_than_the_facts_count_is_a_claim(self):
        said = counts.miscounted("I tested the bot against nine scripted intruders.", COUNT_FACTS)
        self.assertEqual(said, ["I tested the bot against nine scripted intruders. (says 9 scripted intruders; "
                                "your facts give 9 only of break tests)"])

    def test_a_span_of_time_no_fact_gives_is_a_claim(self):
        said = counts.miscounted("I have spent the past four years building tools.", COUNT_FACTS)
        self.assertEqual(len(said), 1)
        self.assertIn("says 4 years building", said[0])
        self.assertEqual(len(counts.miscounted("Over the past two years I built tools.", COUNT_FACTS)), 1,
                         "a number word no fact holds at all")

    def test_a_number_the_facts_count_the_same_way_is_never_one(self):
        for said in ("The bot was contained in all nine break-in tests.", "I cut a build from 40 minutes to 8.",
                     "I split it across 12 jobs.", "I ran tests on three Lua versions.",
                     "I worked in a team of five students.", "I aim for about 250 words.",
                     "I worked there from 2023 to 2025.", "I built one tool.", "I wrote 70 tests."):
            self.assertEqual(counts.miscounted(said, COUNT_FACTS), [], said)

    def test_marks_sums_shares_ranges_and_short_lists_are_never_counts(self):
        # the end of a range, a module's mark and a short list counted in words were listed in real packs
        # (2026-11-19); the facts here are made up in the same shape
        for said in ("I graduated with a 2:1 degree.", "I scored 72% in Databases.", "I got 72/100 in the exam.",
                     "The role pays \u00a330,000 a year.", "I can bring two or three projects.",
                     "It took two to three weeks.", "I aim for 250 words.",
                     "I scored 74 in Coastal Mapping.", "I cut a build from 40 minutes to 8 with a cache.",
                     "I ran tests on two operating systems."):
            self.assertEqual(counts.miscounted(said, COUNT_FACTS), [], said)

    def test_a_number_counts_only_what_its_own_fact_sentence_names(self):
        # "9 break-in tests.\n- Logs" was read as counting logs when the window ran on into the next fact
        said = counts.miscounted("I kept 9 logs of every run.", COUNT_FACTS)
        self.assertEqual(len(said), 1)
        self.assertIn("your facts give 9 only of break tests", said[0])


if __name__ == "__main__":
    unittest.main()

"""The reason for this employer: a specific fact of theirs, in their own words, tied to an item of the plan."""
from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import motive
from jobhunt.writing.plan import Item

ADVERT = ("Graduate Analyst at Brightwater Tools. We build flood sensors for river trusts. We value innovation and "
          "excellence, and offer full training in a friendly team.")
BRIEF = "Brightwater Tools makes river data dashboards used by three made-up councils."
PLAN = [Item("E1", "a sensor logger in Lua that records river levels every ten minutes", "CV", "Tide gauge",
             "personal"), Item("E2", "Volunteer guide at a made-up heritage centre", "CV")]
NAMES = ("Graduate Analyst", "Brightwater Tools")


def reply(fact: str, item: str = "E1", reason: str = "both read river levels") -> dict:
    """A pairing reply."""
    return {"employer_fact": fact, "item": item, "reason": reason}


class Pairings(unittest.TestCase):
    def test_a_specific_fact_in_the_employers_words_tied_to_a_plan_item_is_kept(self):
        found = motive.checked(reply("we build Flood Sensors for river trusts"), ADVERT, BRIEF, PLAN, NAMES)
        self.assertEqual((found.fact, found.item.id, found.reason),
                         ("We build flood sensors for river trusts", "E1", "both read river levels"))
        self.assertIsNotNone(motive.checked(reply("river data dashboards"), ADVERT, BRIEF, PLAN, NAMES))

    def test_a_value_any_employer_claims_is_no_reason(self):
        # letters gave generic motivation that echoed the advert in every graded run (2026-11-07)
        for fact in ("We value innovation and excellence", "offer full training in a friendly team",
                     "Graduate Analyst at Brightwater Tools"):
            self.assertIsNone(motive.checked(reply(fact), ADVERT, BRIEF, PLAN, NAMES), fact)

    def test_a_pairing_with_words_the_employer_never_wrote_or_an_item_not_in_the_plan_is_dropped(self):
        self.assertIsNone(motive.checked(reply("you lead the world in flood sensors"), ADVERT, BRIEF, PLAN, NAMES))
        self.assertIsNone(motive.checked(reply("flood sensors for river trusts", "E7"), ADVERT, BRIEF, PLAN, NAMES))
        self.assertIsNone(motive.checked(reply("flood sensors for river trusts", reason=""), ADVERT, BRIEF, PLAN,
                                         NAMES))
        self.assertIsNone(motive.checked(reply(""), ADVERT, BRIEF, PLAN, NAMES))
        self.assertIsNone(motive.checked("none", ADVERT, BRIEF, PLAN, NAMES))

    def test_the_letter_is_told_the_pairing_or_that_there_is_none(self):
        found = motive.checked(reply("flood sensors for river trusts"), ADVERT, BRIEF, PLAN, NAMES)
        self.assertEqual(motive.block(found),
                         'Motivation pairing (write why this employer from this alone): the employer\'s "flood '
                         'sensors for river trusts" and the applicant\'s "a sensor logger in Lua that records river '
                         'levels every ten minutes", because both read river levels')
        self.assertEqual(motive.block(None), motive.NONE_SAID)

    def test_a_duty_or_requirement_of_the_job_is_no_fact_about_the_employer(self):
        # openings led in with "Your requirement to write and improve SQL queries against Postgres ..." (2026-11-08)
        advert = (f"{ADVERT} Write and improve SQL queries against Postgres. Experience of river sensor data. "
                  "You will maintain flood sensor dashboards.")
        for fact in ("Write and improve SQL queries against Postgres", "Experience of river sensor data",
                     "You will maintain flood sensor dashboards"):
            self.assertTrue(motive.duty(fact), fact)
            self.assertIsNone(motive.checked(reply(fact), advert, BRIEF, PLAN, NAMES), fact)
        for fact in ("We build flood sensors for river trusts", "river data dashboards used by three made-up councils"):
            self.assertFalse(motive.duty(fact), fact)

    def test_a_sentence_addresses_the_employer_by_you_its_name_or_its_product(self):
        fact = "river data dashboards used by three made-up councils"
        self.assertTrue(motive.addresses("Your councils rely on it.", "Brightwater Tools", fact))
        self.assertTrue(motive.addresses("Brightwater Tools is where I want to work.", "Brightwater Tools", fact))
        self.assertTrue(motive.addresses("River dashboards for councils are what I want to build.",
                                         "Brightwater Tools", fact))
        self.assertFalse(motive.addresses("I built a sensor logger in Lua.", "Brightwater Tools", fact))
        self.assertFalse(motive.addresses("I enjoy work with real impact.", "Brightwater Tools", fact),
                         "a word any employer uses is no product")

    def test_a_testimonial_a_list_of_asks_or_a_line_to_the_job_holder_is_no_fact_about_the_employer(self):
        # "why here" answers restated "Your focus on ownership and working with stakeholders ..." or paraphrased
        # an employee's testimonial (15 graded packs, 2026-11-08)
        advert = (f"{ADVERT} Your focus on ownership and working with stakeholders. You will need Python, SQL and "
                  "Docker. \"I joined to map flood risk for river trusts\" says Avery, Analyst.")
        for fact in ("Your focus on ownership and working with stakeholders", "Python, SQL and Docker",
                     "I joined to map flood risk for river trusts"):
            self.assertIsNone(motive.checked(reply(fact), advert, BRIEF, PLAN, NAMES), fact)
        brief = "Brightwater Tools makes flood sensors, river gauges and data dashboards for councils."
        self.assertIsNotNone(motive.checked(reply("flood sensors, river gauges and data dashboards"), ADVERT, brief,
                                            PLAN, NAMES), "a list of the employer's own products is a fact")

    def test_the_brief_is_read_before_the_advert(self):
        brief = "Brightwater Tools builds flood sensors for river trusts in three made-up counties."
        found = motive.checked(reply("Flood sensors for river trusts"), ADVERT, brief, PLAN, NAMES)
        self.assertEqual(found.fact, "flood sensors for river trusts")
        with mock.patch.object(llm, "chat_json", return_value={}) as asked:
            motive.make(NAMES, ADVERT, brief, PLAN)
        request = asked.call_args.args[1]
        self.assertLess(request.index("Company brief"), request.index("Advert:"))

    def test_only_work_the_ranking_fits_to_the_job_may_carry_the_reason(self):
        work = [*PLAN, Item("E3", "an expense tracker for a coursework module", "CV", "Pennywise", "degree")]
        self.assertEqual([i.id for i in motive.offered(work, {"Tide gauge"})], ["E1"])
        self.assertEqual([i.id for i in motive.offered(work)], ["E1", "E3"])
        self.assertEqual([i.id for i in motive.offered(work, {"Elsewhere"})], ["E1", "E3"])

    def test_a_reason_restating_the_advert_is_found(self):
        advert = f"{ADVERT} Your focus on ownership and working with stakeholders is key."
        self.assertTrue(motive.restated("Your focus on ownership and working with stakeholders is a clear priority "
                                        "for me.", advert, NAMES))
        self.assertTrue(motive.restated("I want to help you build flood sensors for river trusts.", advert, NAMES))
        self.assertTrue(motive.restated("You value excellence and innovation and training.", advert, NAMES),
                        "most of its own words from one advert sentence")
        for own in ("Flood sensors that warn river trusts early are what draw me to you.",
                    "I am applying for the Graduate Analyst role at Brightwater Tools."):
            self.assertEqual(motive.restated(own, advert, NAMES), "", own)

    def test_no_plan_means_no_call(self):
        with mock.patch.object(llm, "chat_json", side_effect=AssertionError("no call")):
            self.assertIsNone(motive.make(NAMES, ADVERT, BRIEF, []))


if __name__ == "__main__":
    unittest.main()

"""Tests of the lenient settings readers: pay, distances, times, hours, lists, dates and contacts."""

import unittest

import support  # noqa: F401 - sets up the test home before any jobhunt import

from jobhunt.core.settings import parse, registry
from jobhunt.core.settings.registry import REGISTRY, SettingError

registry.load_all()
POUND = "\u00a3"


def check(key, raw):
    return parse.check(REGISTRY[key], raw)


class Pay(unittest.TestCase):
    """Pay is read any way it is typed, as whole pounds a year."""

    def test_a_pay_typed_as_22000_with_a_comma_is_read(self):
        # Setup on the stick crashed on "22,000" (U setupcfg.py:173-174).
        self.assertEqual(check("goal.min_pay", "22,000"), 22000)
        self.assertEqual(parse.describe(REGISTRY["goal.min_pay"], 22000), "Hal reads this as 22,000 pounds a year")

    def test_every_written_form_of_24000_is_read(self):
        for typed in ("24000", "24,000", f"{POUND}24,000", "GBP 24,000", "24k", "24K", "24 thousand",
                      "24000 a year", "24,000 per annum", 24000):
            with self.subTest(typed):
                self.assertEqual(parse.money(typed), 24000)
        self.assertEqual(parse.money("24.5k"), 24500)

    def test_hourly_pay_is_converted_to_a_year_and_shown_as_about(self):
        self.assertEqual(parse.money("12.50 an hour"), 24375)
        self.assertEqual(parse.show_money(24375), "Hal reads this as about 24,400 pounds a year")
        self.assertEqual(parse.money(f"{POUND}450 a week"), 23400)
        self.assertEqual(parse.money("2,000 a month"), 24000)

    def test_no_lowest_pay_is_zero(self):
        for typed in ("", "0", "none", 0):
            with self.subTest(typed):
                self.assertEqual(check("goal.min_pay", typed), 0)

    def test_unreadable_or_unlikely_pay_is_refused_in_plain_words(self):
        for typed in ("lots", "999", "600k", "24,000 pounds or so maybe", True):
            with self.subTest(typed), self.assertRaises(SettingError) as caught:
                check("goal.min_pay", typed)
            self.assertEqual(str(caught.exception), parse.MONEY_HELP)
            self.assertEqual(caught.exception.key, "goal.min_pay")


class DistancesAndTimes(unittest.TestCase):
    """Distances and times of day."""

    def test_15_miles_and_two_search_times_are_read(self):
        for typed in ("15", "15 miles", "15mi", 15):
            with self.subTest(typed):
                self.assertEqual(check("goal.max_distance", typed), 15)
        self.assertEqual(check("goal.max_distance", "25 km"), 16)
        for typed in ("07:30 and 13:30", "7.30, 13:30", "13:30, 7:30am", ["07:30", "13:30"]):
            with self.subTest(typed):
                self.assertEqual(check("schedule.search_times", typed), ["07:30", "13:30"])
        self.assertEqual(parse.times("7:30am"), ["07:30"])
        self.assertEqual(parse.times("1pm"), ["13:00"])

    def test_a_distance_outside_the_slider_is_refused(self):
        with self.assertRaises(SettingError) as caught:
            check("goal.max_distance", "200 miles")
        self.assertEqual(str(caught.exception), "Choose a number between 5 and 60 miles.")

    def test_hours_are_a_span_or_off(self):
        self.assertEqual(check("notify.quiet_hours", "22:00 to 07:30"), ["22:00", "07:30"])
        self.assertEqual(check("notify.quiet_hours", "10pm - 7.30am"), ["22:00", "07:30"])
        self.assertIsNone(check("ai.keep_free", "Off"))
        self.assertEqual(parse.describe(REGISTRY["notify.quiet_hours"], ["22:00", "07:30"]), "22:00 to 07:30")
        for bad in ("25:00 to 07:00", "22:00", "07:00 to 07:00"):
            with self.subTest(bad), self.assertRaises(SettingError):
                check("notify.quiet_hours", bad)


class ListsAndText(unittest.TestCase):
    """Tags, lines, dates, phones, email addresses and web addresses."""

    def test_tags_are_split_stripped_and_deduplicated_keeping_case(self):
        self.assertEqual(check("goal.roles", " Research, policy\nresearch ,  Heritage Work ,,"),
                         ["Research", "policy", "Heritage Work"])

    def test_employer_lists_keep_section_lines(self):
        self.assertEqual(check("goal.avoid_employers", "# Agencies\nAcme Recruit\n\n  Bolt Staffing  "),
                         ["# Agencies", "Acme Recruit", "Bolt Staffing"])

    def test_dates_are_read_in_iso_and_uk_forms(self):
        self.assertEqual(check("you.start_date", "01/09/2026"), "2026-09-01")
        self.assertEqual(check("you.start_date", "2026-09-01"), "2026-09-01")
        with self.assertRaises(SettingError):
            check("you.start_date", "31/02/2026")

    def test_uk_phone_numbers_are_spaced_for_reading(self):
        self.assertEqual(check("you.phone", "07700900123"), "07700 900123")
        self.assertEqual(check("you.phone", "+44 7700 900123"), "07700 900123")
        self.assertEqual(check("you.phone", "02079460018"), "020 7946 0018")
        self.assertEqual(check("you.phone", "+1 555 0100 222"), "+1 555 0100 222")
        with self.assertRaises(SettingError):
            check("you.phone", "call me")

    def test_email_and_web_addresses_are_shape_checked(self):
        self.assertEqual(check("you.email", "robin.testwell@example.org"), "robin.testwell@example.org")
        self.assertEqual(check("you.links.website", "example.org/robin"), "https://example.org/robin")
        self.assertEqual(check("ai.ollama_url", "http://localhost:11434"), "http://localhost:11434")
        for key, bad in (("you.email", "robin at example"), ("you.links.website", "not a site")):
            with self.subTest(bad), self.assertRaises(SettingError):
                check(key, bad)


class Choices(unittest.TestCase):
    """Choices accept the stored value or the words on screen; nothing else."""

    def test_a_choice_is_read_from_its_words_on_screen(self):
        self.assertEqual(check("you.right_to_work", "I need sponsorship"), "need_sponsorship")
        self.assertEqual(check("sites.max_age_days", "45"), 45)
        self.assertEqual(check("goal.kinds", "Paid internships, Graduate schemes"), ["internship", "graduate_scheme"])
        with self.assertRaises(SettingError):
            check("goal.kinds", ["internship", "astronaut"])

    def test_firefox_and_outlook_are_refused_until_supported(self):
        with self.assertRaises(SettingError) as caught:
            check("applying.browser", "firefox")
        self.assertIn("Firefox", str(caught.exception))
        with self.assertRaises(SettingError):
            check("email.provider", "outlook")

    def test_switches_and_numbers_are_read_from_words(self):
        self.assertIs(check("you.relocate", "yes"), True)
        self.assertIs(check("you.relocate", "off"), False)
        self.assertEqual(check("advanced.port", "8740"), 8740)
        with self.assertRaises(SettingError):
            check("advanced.port", "80")


if __name__ == "__main__":
    unittest.main()

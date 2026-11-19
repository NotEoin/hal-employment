"""A job's title as a person would say it: the board's padding goes, the job's own words stay."""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.writing import titles


class Spoken(unittest.TestCase):
    def test_a_bar_tail_and_a_reference_number_go(self):
        """Regression: a letter said it was applying for the advert's raw title (owner's trial, 2026-11-19)."""
        self.assertEqual(titles.spoken("Trainee DevOps Engineer | No experience needed (Ref: 7501)"),
                         "Trainee DevOps Engineer")

    def test_salary_location_and_agency_padding_tails_go(self):
        cases = {("Graduate Software Engineer - \u00a330,000 - Immediate Start", ""): "Graduate Software Engineer",
                 ("Customer Service Advisor - Up to 25k + benefits", ""): "Customer Service Advisor",
                 ("Warehouse Operative - Newcastle", "Newcastle upon Tyne, Tyne and Wear"): "Warehouse Operative",
                 ("Software Developer - Leeds (Ref: JR-4471)", "Leeds"): "Software Developer",
                 ("Junior Data Analyst (Hybrid)", ""): "Junior Data Analyst",
                 ("Data Analyst, Remote", ""): "Data Analyst",
                 ("Trainee Developer - No Experience Required - Full Training Provided", ""): "Trainee Developer",
                 ("URGENT: Care Assistant", ""): "Care Assistant",
                 ("Teaching Assistant - Ref 12345", ""): "Teaching Assistant"}
        for (given, place), said in cases.items():
            with self.subTest(given):
                self.assertEqual(titles.spoken(given, place), said)

    def test_a_tail_that_is_part_of_the_job_is_kept(self):
        for given in ("Software Engineer - Python", "Research Assistant (Fixed Term)", "Senior Engineer, Platform",
                      "Teaching Assistant, SEN", "Software Developer"):
            with self.subTest(given):
                self.assertEqual(titles.spoken(given, "London"), given)

    def test_a_title_in_capitals_is_put_in_title_case_keeping_its_acronyms(self):
        self.assertEqual(titles.spoken("GRADUATE IT SUPPORT ANALYST (SQL)"), "Graduate IT Support Analyst (SQL)")
        self.assertEqual(titles.spoken("HEAD OF DATA AND AI"), "Head of Data and AI")
        self.assertEqual(titles.spoken("iOS Developer"), "iOS Developer", "a title with lower case keeps its capitals")

    def test_a_title_that_is_all_padding_is_kept_as_given(self):
        self.assertEqual(titles.spoken("Immediate Start"), "Immediate Start")
        self.assertEqual(titles.spoken(""), "")

    def test_a_job_row_is_cleaned_with_its_own_location(self):
        self.assertEqual(titles.of_job({"title": "Barista - Bristol", "location": "Bristol"}), "Barista")
        self.assertEqual(titles.of_job({"title": "Barista - Bristol"}), "Barista - Bristol", "no location given")


if __name__ == "__main__":
    unittest.main()

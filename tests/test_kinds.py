"""What kind of role an advert is, and who may apply. Every advert here is made up."""

import unittest
from datetime import date

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.find import kinds


class Kinds(unittest.TestCase):
    """The first kind that matches the title, then the advert, wins."""

    def test_the_title_decides_the_kind_first(self):
        cases = {"Spring Insight Week": "insight", "Level 4 Apprenticeship in Data": "apprenticeship",
                 "Placement Year Software Developer": "placement", "Graduate Internship": "internship",
                 "Graduate Scheme 2027": "graduate_scheme", "Junior Policy Officer": "graduate_job",
                 "Senior Curator": "experienced", "Policy Officer": "other"}
        for title, kind in cases.items():
            with self.subTest(title):
                self.assertEqual(kinds.classify(title, "").kind, kind)

    def test_hours_and_contract_are_flags_on_any_kind(self):
        k = kinds.classify("Junior Archivist", "Part-time, 21 hours a week, fixed-term for 9 months.")
        self.assertEqual((k.kind, k.hours, k.contract), ("graduate_job", "part", "fixed"))

    def test_graduation_years_are_kept_from_the_advert(self):
        req = kinds.requirements("Open to students graduating in 2027 or 2028.")
        self.assertEqual((req["requires"], req["years"]), ("graduating_in", [2027, 2028]))

    def test_a_student_is_one_until_the_course_ends(self):
        self.assertTrue(kinds.is_student("at_uni", "2027-06", date(2026, 10, 22)))
        self.assertFalse(kinds.is_student("at_uni", "2026-06", date(2026, 10, 22)))
        self.assertFalse(kinds.is_student("finished", "2027", date(2026, 10, 22)))


if __name__ == "__main__":
    unittest.main()

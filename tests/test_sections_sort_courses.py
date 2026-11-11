"""Courses and modules from a dump's folders (`sections.sort_courses`): a degree folder, year folders and module
folders, for any subject. Every folder name is made up.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.sections import sort_courses


class CoursesFromFolders(unittest.TestCase):
    def test_a_degree_folder_names_the_course_and_a_coded_folder_its_module(self):
        c = sort_courses.course_of("Avery stuff/Uni/BSc Computing/Year 2/CS2041 Databases/Lectures/week 1.md")
        self.assertEqual((c.key, c.qualification, c.subject, c.module),
                         ("Avery stuff/Uni/BSc Computing", "BSc", "Computing", "CS2041 Databases"))
        c = sort_courses.course_of("Marnie files/BA History/Early Modern Europe - HIS2051/Seminars/week 1.md")
        self.assertEqual((c.qualification, c.subject, c.module), ("BA", "History", "HIS2051 Early Modern Europe"))
        c = sort_courses.course_of("Study/Access Humanities/Lectures/week 1 notes.md")
        self.assertEqual((c.qualification, c.subject), ("Access", "Humanities"))
        c = sort_courses.course_of("Study/access to humanities/Lectures/week 1 notes.md")
        self.assertEqual((c.qualification, c.subject), ("Access", "Humanities"), "not 'access', 'to humanities'")

    def test_with_no_degree_folder_the_folder_holding_years_or_modules_is_the_course(self):
        c = sort_courses.course_of("Dump/Fenwick University/Year 1/notes.md")
        self.assertEqual((c.key, c.institution), ("Dump/Fenwick University", "Fenwick University"))
        c = sort_courses.course_of("Dump/Art and Design/Modules/Drawing - ART1001/sketchbook.md")
        self.assertEqual((c.key, c.subject, c.module), ("Dump/Art and Design", "Art and Design", "ART1001 Drawing"),
                         "a folder that only holds the modules names no course")
        self.assertIsNone(sort_courses.course_of("Dump/Notes/smart contract.md"))

    def test_a_module_written_several_ways_is_listed_once_and_one_pieces_folder_is_no_module(self):
        # regression: a real degree listed "CSC2031", "CSC3632 - System and Network Security" and its two other
        # spellings as separate modules, and "c3046604 CS1032 practical report" as one (2026-11-11)
        found = sort_courses.courses_found([
            (f"D/BA History/{folder}/week 1.md", "2022-10-03T09:00:00Z") for folder in (
                "HIS2040", "HIS2040 Archival Methods", "HIS2051 - Early Modern Europe", "HIS2051 Early modern Europe",
                "b1234567 HIS2040 seminar essay")])
        self.assertEqual(found[0]["modules"], ["HIS2040 Archival Methods", "HIS2051 Early Modern Europe"])

    def test_a_folder_named_master_alone_is_no_masters_degree(self):
        # Regression: a notes vault at "obsidian/Master" was proposed as a course (2026-11-09).
        self.assertIsNone(sort_courses.course_of("Dump/obsidian/Master/Networks/Subnetting.md"))
        self.assertEqual(sort_courses.course_of("Dump/Masters/Year 1/notes.md").key, "Dump/Masters")

    def test_rows_are_grouped_by_module_else_by_their_folder(self):
        self.assertEqual(sort_courses.group_key("D/BA History/HIS2040 Archival Methods/Seminars/week 3.md"),
                         "D/BA History/HIS2040 Archival Methods")
        self.assertEqual(sort_courses.group_key("D/BA History/Year 3/Dissertation/draft.md"),
                         "D/BA History/Year 3/Dissertation")

    def test_one_course_in_two_places_is_one_proposal_with_its_modules_files_and_months(self):
        found = sort_courses.courses_found([
            ("D/Uni/BA History/HIS2040 Archival Methods/week 1.md", "2022-10-03T09:00:00Z"),
            ("D/Old laptop/BA History/HIS2051 Early Modern Europe/week 1.md", "2023-05-01T09:00:00Z"),
            ("D/Notes/loose.md", "2021-01-01T09:00:00Z")])
        self.assertEqual(len(found), 1)
        self.assertEqual({k: found[0][k] for k in ("title", "modules", "files", "start", "end")},
                         {"title": "BA History", "modules": ["HIS2040 Archival Methods", "HIS2051 Early Modern Europe"],
                          "files": 2, "start": "2022-10", "end": "2023-05"})
        self.assertEqual(len(found[0]["keys"]), 2)


# A real dump's courses (2026-11-09), made up: three academic-year folders of modules beside two folders naming
# one degree two ways.
YEARS_AND_DEGREES = [
    ("D/Uni/2023-24/Semester 1/CSC1001 Programming/week 1.md", "2023-10-02T09:00:00Z"),
    ("D/Uni/2024-25/Term 2/CSC2002 Databases/week 1.md", "2025-02-03T09:00:00Z"),
    ("D/Uni/2025-2026/CSC3003 Graphics/week 1.md", "2025-10-06T09:00:00Z"),
    ("D/Uni/Computer Science BSc/CSC3004 Compilers/week 1.md", "2026-01-05T09:00:00Z"),
    ("D/Old laptop/Computer Science BsC Fenwick/CSC3005 Networks/week 1.md", "2026-03-02T09:00:00Z")]
MODULES = ["CSC1001 Programming", "CSC2002 Databases", "CSC3003 Graphics", "CSC3004 Compilers", "CSC3005 Networks"]


class CoursesMerged(unittest.TestCase):
    def test_an_academic_year_folder_is_never_a_qualification(self):
        for name in ("2023-24", "2024-2025", "Year 1", "Semester 2", "Term 1", "Academic Year 2023-24"):
            self.assertTrue(sort_courses.year_folder(name), name)
        self.assertFalse(sort_courses.year_folder("2023 photos"))
        c = sort_courses.course_of("D/Uni/2023-24/Semester 1/CSC1001 Programming/week 1.md")
        self.assertEqual((c.key, c.qualification, c.institution, c.module),
                         ("D/Uni", "", "Uni", "CSC1001 Programming"))
        self.assertEqual(sort_courses.course_of("D/2024-25/CSC2002 Databases/week 1.md").subject, "D",
                         "a year folder never names the course; the folder above it does")

    def test_names_of_one_degree_written_two_ways_are_one_course_with_every_years_modules(self):
        # Regression: "2023-24", "2024-25", "2025-26", "Computer Science BSc" and "Computer Science BsC
        # Newcastle" were five courses for one graduate (2026-11-09).
        found = sort_courses.courses_found(YEARS_AND_DEGREES)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["qualification"], found[0]["subject"], found[0]["modules"], found[0]["files"]),
                         ("BSc", "Computer Science", MODULES, 5))
        self.assertEqual((found[0]["start"], found[0]["end"]), ("2023-10", "2026-03"))

    def test_the_settings_or_a_transcript_name_the_course_and_a_cv_only_places_the_years(self):
        settings = {"qualification": "BSc (Hons)", "subject": "Computer Science", "institution": "Fenwick University"}
        found = sort_courses.courses_found(YEARS_AND_DEGREES, [settings])
        self.assertEqual([c["title"] for c in found], ["BSc (Hons) Computer Science, Fenwick University"])
        self.assertEqual(found[0]["modules"], MODULES)
        transcript = sort_courses.qualification_in("Avery Quillon\nBSc (Hons) Computer Science, Fenwick University")
        self.assertEqual(transcript, settings)
        cv = {"qualification": "BA", "subject": "History", "institution": "Fenwick University"}
        years = [r for r in YEARS_AND_DEGREES if "/20" in r[0]]
        found = sort_courses.courses_found(years, cvs=[cv])
        self.assertEqual(([c["title"] for c in found], found[0]["modules"]),
                         (["BA History, Fenwick University"], MODULES[:3]),
                         "with no degree folder, the CV's degree takes the years' modules")
        self.assertEqual(sort_courses.courses_found(YEARS_AND_DEGREES, cvs=[cv])[0]["title"],
                         "BSc Computer Science", "a CV never renames a degree the folders name")

    def test_two_degrees_stay_two_and_the_years_wait_for_a_name(self):
        rows = [*YEARS_AND_DEGREES[:3], ("D/BSc Computer Science and Maths/MAS1001 Algebra/week 1.md", ""),
                ("D/MSc Computer Science/CSC8001 Research/week 1.md", "")]
        titles = sorted(c["title"] for c in sort_courses.courses_found(rows))
        self.assertEqual(titles, ["BSc Computer Science and Maths", "MSc Computer Science", "Uni"],
                         "a joint degree or another level is another course")
        self.assertEqual(sort_courses.split_qualification("Computer Science B.Sc. (Hons)"),
                         ("BSc (Hons)", "Computer Science"))


if __name__ == "__main__":
    unittest.main()

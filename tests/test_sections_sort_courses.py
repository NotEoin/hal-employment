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

    def test_with_no_degree_folder_the_folder_holding_years_or_modules_is_the_course(self):
        c = sort_courses.course_of("Dump/Fenwick University/Year 1/notes.md")
        self.assertEqual((c.key, c.institution), ("Dump/Fenwick University", "Fenwick University"))
        c = sort_courses.course_of("Dump/Art and Design/Modules/Drawing - ART1001/sketchbook.md")
        self.assertEqual((c.key, c.subject, c.module), ("Dump/Art and Design", "Art and Design", "ART1001 Drawing"),
                         "a folder that only holds the modules names no course")
        self.assertIsNone(sort_courses.course_of("Dump/Notes/smart contract.md"))

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


if __name__ == "__main__":
    unittest.main()

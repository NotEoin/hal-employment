"""List-shaped lines taken apart into one fact per entry, each with its list's heading; the lines are made up."""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import atoms

MODULES = "Second year modules (overall 73.3): Ferry Studies 86, River Trade 79, Software Engineering Team Project 73"


class Split(unittest.TestCase):
    def test_a_module_list_is_one_line_per_module_with_its_heading(self):
        self.assertEqual(atoms.split(MODULES), ["Second year modules (overall 73.3): Ferry Studies 86",
                                                "Second year modules (overall 73.3): River Trade 79",
                                                "Second year modules (overall 73.3): Software Engineering Team "
                                                "Project 73"])

    def test_skills_and_achievements_lists_are_one_line_per_entry(self):
        self.assertEqual(atoms.split("Languages: Lua, SQL"), ["Languages: Lua", "Languages: SQL"])
        self.assertEqual(atoms.split("Skills: Python (advanced, five years), SQL"),
                         ["Skills: Python (advanced, five years)", "Skills: SQL"], "brackets keep their commas")
        self.assertEqual(atoms.split("Achievements: Dean's list 2024; Hackathon winner 2025; Mentor of the year."),
                         ["Achievements: Dean's list 2024", "Achievements: Hackathon winner 2025",
                          "Achievements: Mentor of the year"])

    def test_a_line_about_one_piece_of_work_or_prose_stays_whole(self):
        for line in ("Project, Ferry planner (Lua, SQL): wrote the timetable views, tests, and docs",
                     "Barista, Old Mill Inn (2024 to 2025): served customers, ran the till, trained staff",
                     "Volunteering: Guide at a made-up heritage centre, 2024 to 2026",
                     "Project (personal project): Tide gauge. Logs river levels. Tools: Lua, SQL, Make",
                     "Course ends or ended: 2026-06. Right to work: I have the right to work in the UK.",
                     "Avery Quillon. BSc Imaginary Studies."):
            with self.subTest(line=line):
                self.assertEqual(atoms.split(line), [line])

    def test_a_module_lists_entries_are_named_without_their_marks(self):
        self.assertEqual([name for name, _ in atoms.module_entries(MODULES)],
                         ["Ferry Studies", "River Trade", "Software Engineering Team Project"])
        self.assertEqual(atoms.module_entries("Languages: Lua, SQL"), [])


if __name__ == "__main__":
    unittest.main()

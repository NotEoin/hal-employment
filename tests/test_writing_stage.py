"""Where the person is today: the stage line every writing request carries, and the kind of each project.

Each test runs in its own temporary home with a fixed date. The course, employer and projects are made up.
"""
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import db, profile
from jobhunt.core import paths, settings
from jobhunt.profile import store
from jobhunt.writing import stage

NOW = "2026-11-07T09:00:00Z"
TODAY = date(2026, 11, 7)
LETTER = "I can start in 2026 upon completing my degree. I built a made-up tide gauge."


def ev(source: str, doc_type: str, quote: str = "") -> dict:
    """Evidence as the ingest gives it."""
    return {"source_rel": source, "quote": quote, "field": "", "doc_type": doc_type}


class Stage(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def degree(self, end: str, *, checked: bool = True) -> int:
        item = store.add_found(self.con, "education", {"qualification": "BSc", "subject": "Imaginary Studies",
                                                       "institution": "Made-up University", "start": "2023-09",
                                                       "end": end}, ev("Sources/Education/t.md", "transcript"))
        if checked:
            store.confirm(self.con, item)
        return item

    def test_a_degree_that_ended_says_finished_and_available_now(self):
        # a letter in November 2026 offered to start "upon completing my degree", which ended in June (2026-11-06)
        settings.set("goal.study_status", "at_uni", by="you")
        settings.set("goal.study_end", "2026", by="you")
        self.degree("2026-06")
        now = stage.current(self.con)
        self.assertEqual(now.words, "Finished BSc Imaginary Studies in 2026-06; available now")
        self.assertEqual(stage.today_lines(now), "Today: 2026-11-07\nStage: Finished BSc Imaginary Studies in 2026-06; "
                                           "available now.")

    def test_the_course_says_its_subject_institution_and_grade_for_the_messages_opening(self):
        self.degree("2026-06")
        settings.set("you.education.grade", "2:1", by="you")
        settings.set("you.education.university", "Another Made-up College", by="you")
        now = stage.current(self.con, TODAY)
        self.assertEqual((now.subject, now.institution, now.grade),
                         ("Imaginary Studies", "Made-up University", "2:1"), "the checked course first")

    def test_a_degree_ending_within_a_year_is_the_final_year(self):
        self.degree("2027-06")
        settings.set("you.start", "date", by="you")
        settings.set("you.start_date", "2027-07-01", by="you")
        self.assertEqual(stage.current(self.con, TODAY).words,
                         "Final year of BSc Imaginary Studies, finishing 2027-06; available from 2027-07-01")

    def test_a_degree_fact_nobody_checked_never_decides(self):
        self.degree("2026-06", checked=False)
        settings.set("goal.study_status", "at_uni", by="you")
        settings.set("goal.study_end", "2028-06", by="you")
        settings.set("you.education.degree", "MSci", by="you")
        settings.set("you.start", "1m", by="you")
        settings.set("you.notice", "One month", by="you")
        self.assertEqual(stage.current(self.con, TODAY).words,
                         "Studying MSci, finishing 2028-06; available 1 month after an offer (notice: One month)")

    def test_finishing_an_ended_course_is_found_and_a_running_one_is_not(self):
        settings.set("goal.study_end", "2026-06", by="you")
        self.degree("2026-06")
        self.assertEqual(stage.ended_course(LETTER, stage.current(self.con)),
                         ["I can start in 2026 upon completing my degree."])
        self.assertEqual(stage.ended_course(LETTER, stage.current(self.con, date(2026, 3, 1))), [])

    def test_a_graduation_spoken_of_as_still_to_come_is_found_once_the_course_has_ended(self):
        # "available to start immediately following my graduation in June 2026", written on 2026-11-07
        settings.set("goal.study_end", "2026-06", by="you")
        self.degree("2026-06")
        now = stage.current(self.con, date(2026, 11, 7))
        for said in ("I am available to start immediately following my graduation in June 2026.",
                     "I can join after my graduation.",
                     "Once I have graduated I will be free to relocate.",
                     "I will be ready to start once my degree ends.",
                     "Upon completion of my degree I can begin at once.",
                     "I am graduating in June 2026."):
            with self.subTest(said):
                self.assertEqual(stage.ended_course(said, now), [said])
        for said in ("Following my graduation in June 2026, I have built two made-up tools.",
                     "Since my graduation I spent a summer cataloguing letters.",
                     "I graduated in June 2026 and am available now."):
            with self.subTest(said):
                self.assertEqual(stage.ended_course(said, now), [])


    def test_calling_you_a_student_after_the_course_ended_is_found(self):
        # messages called a person who had graduated "a Computer Science student" (2026-11-08)
        settings.set("goal.study_end", "2026-06", by="you")
        self.degree("2026-06")
        now, then = stage.current(self.con, date(2026, 11, 8)), stage.current(self.con, date(2026, 3, 1))
        for said in ("I am a Computer Science student at a made-up university.",
                     "As an Imaginary Studies student, I am keen to join your team.",
                     "I'm currently a final-year student.",
                     "I am currently studying for a BSc in Imaginary Studies."):
            with self.subTest(said):
                self.assertEqual(stage.ended_course(said, now), [said])
                self.assertEqual(stage.ended_course(said, then), [], "true while the course runs")
        for said in ("I am currently completing a BSc in Imaginary Studies, on track to achieve a First.",
                     "I am on track to graduate with a 2:1.",
                     "I am expected to graduate in June.",
                     "My predicted grade is a First.",
                     "Now in my final year, I enjoy working with made-up data."):
            with self.subTest(said):
                # "I am currently completing a Bachelor of Science ... on track to achieve a First" passed
                # after graduation (2026-11-08)
                self.assertEqual(stage.ended_course(said, now), [said])
                self.assertEqual(stage.ended_course(said, then), [], "true while the course runs")
        for said in ("As a student, I built a made-up tide logger in my own time.",
                     "In my final year, I built a made-up ferry planner.",
                     "I completed a project on track records of made-up ferries.",
                     "I was an Imaginary Studies student from 2023 to 2026.",
                     "I am a recent Imaginary Studies graduate.",
                     "I now help the student volunteers at a made-up museum."):
            with self.subTest(said):
                self.assertEqual(stage.ended_course(said, now), [])


class ProjectKind(unittest.TestCase):
    def test_each_project_says_the_kind_its_evidence_shows(self):
        kind = stage.project_kind
        self.assertEqual(kind(["Tide gauge"], ["coursework"]), "degree work")
        self.assertEqual(kind(["Tide gauge for IMAG2041"], ["repository"]), "degree work")
        self.assertEqual(kind(["Sources/Coursework/Tide gauge.md"], ["report"]), "degree work")
        self.assertEqual(kind(["Dashboard for Brightwater Tools"], ["cv"], ["Brightwater Tools"]), "at work")
        self.assertEqual(kind(["Tide gauge", "Built at home in Lua"], ["repository"]), "personal project")
        self.assertEqual(kind(["A group project on tides"], ["coursework"]), "degree work, team project")
        self.assertEqual(kind(["Tide gauge"], ["cv"]), "", "no sign either way is left unsaid")

    def test_the_words_of_the_evidence_decide_before_the_kind_of_file(self):
        # a CV line naming a team of developers at a university was called personal, a project named with a
        # module code got no kind, and a README extending a dissertation in the author's own time was called
        # degree work (2026-11-07)
        kind = stage.project_kind
        self.assertEqual(kind(["Back-end developer, 7 developers, Ferryapp, Wexley University"], ["cv"]),
                         "team project")
        self.assertEqual(kind(["Ferryapp", "Built with the Wexley University app team"], ["repository"]),
                         "personal project, team project")
        self.assertEqual(kind(["Software engineer, team project (7 developers), Ferryapp, Wexley University"],
                              ["cv"]), "team project")
        self.assertEqual(kind(["IMAG2031blog"], ["repository"]), "degree work")
        self.assertEqual(kind(["Tide gauge", "Submitted for the second-year practical 3"], ["repository"]),
                         "degree work")
        self.assertEqual(kind(["Tide gauge", "This extends my dissertation on tides, built in my own time."],
                              ["repository"]), "personal project")
        self.assertEqual(kind(["Tide gauge", "A side project"], ["coursework"]), "personal project")
        self.assertEqual(kind(["Tide gauge", "A practical tool for sailors"], ["repository"]), "personal project",
                         "practical as a plain word is no module")

    def test_each_kind_gives_one_context_and_a_team_project_is_a_university_ones(self):
        # a university team project kept in a repository was called a personal project (2026-11-07)
        context = stage.context_of
        self.assertEqual([context(k) for k in ("degree work", "at work", "personal project", "")],
                         ["degree", "work", "personal", ""])
        self.assertEqual(context("degree work, team project"), "team")
        self.assertEqual(context("personal project, team project"), "team")
        self.assertEqual(context("at work, team project"), "work")

    def test_the_profile_block_names_each_projects_kind(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(home))
        con = db.connect()
        self.addCleanup(con.close)
        for name, doc_type in (("Tide gauge", "repository"), ("Harbour survey", "dissertation")):
            store.confirm(con, store.add_found(con, "project", {"name": name, "tools": ["Lua"]},
                                               ev(f"Sources/Projects/{name}.md", doc_type)))
        block = profile.context_block(con)
        self.assertIn("- Project (personal project): Tide gauge. Tools: Lua", block)
        self.assertIn("- Project (degree work): Harbour survey. Tools: Lua", block)


if __name__ == "__main__":
    unittest.main()

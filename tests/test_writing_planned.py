"""Planned is not done: the words and documents that say what a project was going to use.

A team project card listed tools the team's design document planned and the built app never used, and every
pack said the person used them (2026-11-08). The sentences and file names are made up.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import planned


class Planned(unittest.TestCase):
    def test_words_of_a_plan_are_found(self):
        for said in ("The app will use Docker and PostgreSQL.", "We plan to deploy it on a made-up cloud.",
                     "Docker was planned for the second term.", "The proposed stack is Flask and Redis.",
                     "It is to be deployed with Docker.", "The design uses Docker (planned)."):
            self.assertTrue(planned.said(said), said)

    def test_work_done_is_not_a_plan(self):
        for said in ("I planned rotas for 30 staff.", "We built the app with SQLite.", "I used Lua for the logger.",
                     "The planner app shows ferry times."):
            self.assertFalse(planned.said(said), said)

    def test_a_plan_document_is_known_by_its_name(self):
        for source in ("Sources/Projects/Ferry planner/Design Document.md", "Sources/Coursework/design.md",
                       "Sources/Projects/proposal-v2.md", "Sources/Projects/Project Plan.docx",
                       "Sources/Projects/requirements-spec.md"):
            self.assertTrue(planned.plan_document(source), source)
        for source in ("Sources/Code/ferry-planner/README.md", "Sources/Code/ferry/requirements.txt",
                       "Sources/CVs and letters/CV.md"):
            self.assertFalse(planned.plan_document(source), source)

    def test_a_plan_documents_words_are_planned_unless_they_say_it_was_done(self):
        design = "Sources/Projects/Ferry planner/Design Document.md"
        self.assertTrue(planned.planned("The app uses Docker and PostgreSQL.", design))
        self.assertFalse(planned.planned("In the end we used SQLite.", design))
        self.assertFalse(planned.planned("The app uses SQLite.", "Sources/Code/ferry-planner/README.md"))
        self.assertEqual(planned.document_name(design), "Design Document")


if __name__ == "__main__":
    unittest.main()

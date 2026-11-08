"""Wrong-place hints: a CV, a transcript or a letter added to a section where it does not belong is named, and
one in its place is left alone. Every text is made up."""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.sections.wrong_place import suggest

CV = ("Avery Quillon\n\nWork experience\n\nJunior developer, Made-up Tides Ltd, 2024.\n\nEducation\n\n"
      "BSc Made-up Computing, University of Nowhere Fen.\n\nSkills\n\nPython, SQL.\n")
TRANSCRIPT = "Academic transcript\n\nMarnie Quillfeather\nModule HIS101 Archives 68\nModule HIS102 Sources 72\n"
LETTER = "Dear hiring manager,\n\nI would like to apply for the archive trainee role.\n\nYours sincerely,\nMarnie\n"
ESSAY = "The parish registers of Harrowgate Vale, 1750 to 1850: a study of baptisms.\n"


class WrongPlace(unittest.TestCase):
    def test_a_cv_in_coursework_or_documents_is_suggested_for_cvs(self):
        self.assertEqual(suggest("coursework", "Avery Quillon CV.pdf", CV), "cv")
        self.assertEqual(suggest("document", "notes.txt", CV), "cv")

    def test_a_cv_in_cvs_is_where_it_belongs(self):
        self.assertEqual(suggest("cv", "Avery Quillon CV.pdf", CV), "")

    def test_a_transcript_in_lecture_material_is_suggested_for_official_papers(self):
        self.assertEqual(suggest("lecture", "results.pdf", TRANSCRIPT), "official")
        self.assertEqual(suggest("document", "results.pdf", TRANSCRIPT), "")

    def test_a_letter_in_coursework_is_suggested_but_a_reference_in_a_jobs_documents_is_not(self):
        self.assertEqual(suggest("coursework", "application.docx", LETTER), "letter")
        self.assertEqual(suggest("job_doc", "reference.docx", LETTER), "")

    def test_an_essay_is_left_where_it_was_put(self):
        self.assertEqual(suggest("coursework", "parish registers.docx", ESSAY), "")


if __name__ == "__main__":
    unittest.main()

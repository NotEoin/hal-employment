"""The sections of Your record: each says what it takes, whose its files are, what its facts may become, and
how much they are trusted."""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.profile import merge
from jobhunt.sections import readers, registry


class Sections(unittest.TestCase):
    def test_every_section_named_in_the_plan_is_there_with_a_plain_label(self):
        self.assertEqual(list(registry.SECTIONS), ["cv", "letter", "job_doc", "coursework", "lecture", "official",
                                                   "project", "document"])
        for s in registry.SECTIONS.values():
            with self.subTest(s.id):
                self.assertTrue(s.label[0].isupper() and s.hint)
                self.assertIn(s.reader, readers.READERS)
                for kind in s.doc_kinds:
                    self.assertIn(s.reader_of(kind), readers.READERS)

    def test_coursework_and_lecture_material_need_an_education_card_and_a_jobs_documents_a_job(self):
        need = {s.id: (s.target, s.target_required) for s in registry.SECTIONS.values() if s.target_required}
        self.assertEqual(need, {"job_doc": ("job", True), "coursework": ("education", True),
                                "lecture": ("education", True)})

    def test_lecture_material_is_taught_and_gives_skills_only(self):
        lecture = registry.get("lecture")
        self.assertEqual((lecture.role, lecture.evidence_role, lecture.creates), ("taught", "taught", ("skill",)))

    def test_official_papers_count_as_the_persons_own_and_set_marks_above_a_cv(self):
        official = registry.get("official")
        self.assertEqual(official.evidence_role, "own")
        self.assertEqual(official.rank("modules"), merge.RANKS["transcript"])
        self.assertGreater(official.rank("institution"), registry.get("cv").rank("institution"))

    def test_cover_letters_give_no_facts(self):
        letter = registry.get("letter")
        self.assertEqual((letter.creates, letter.rank("title")), ((), 0))

    def test_each_project_source_has_its_own_reader(self):
        project = registry.get("project")
        self.assertEqual((project.reader_of("github_account"), project.reader_of("web_page")),
                         ("github_account", "page"))

    def test_a_kind_of_document_a_section_does_not_take_is_refused_in_plain_words(self):
        self.assertEqual(registry.doc_kind(registry.get("cv"), None).id, "cv")
        with self.assertRaisesRegex(ValueError, "Official papers takes transcript, certificate or award"):
            registry.doc_kind(registry.get("official"), "essay")
        with self.assertRaisesRegex(LookupError, "not a section"):
            registry.get("photos")


if __name__ == "__main__":
    unittest.main()

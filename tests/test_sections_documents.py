"""The Documents reader: a transcript sets an education card's modules, marks and grade above every CV, a
certificate makes a certificate card, a reference attaches to its job with the referee's words as another
person's, and anything else keeps only its text, title and date. Private papers are kept out.

The two made-up graduates of `support.graduates` (Avery Quillon, software; Marnie Quillfeather, history) and
every name, place and file are invented.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.graduates import AVERY, GRADUATES, MARNIE
from test_ingest import home

from jobhunt.profile import store as profile_store
from jobhunt.sections import documents, pipeline, store
from jobhunt.sections.pipeline import Incoming

MEDICAL = ("# Fit note\n\nStatement of fitness for work. Diagnosed with a sprained ankle by the GP at the NHS "
           "clinic; not fit for work for two weeks. Repeat prescription attached.\n")
CHECK = "# Basic check certificate\n\nNo unspent convictions were found. Issued 3 June 2025.\n"
TRANSCRIPT_MARKS = {
    AVERY.name: [{"name": "CSC2010 Databases", "mark": "74"}, {"name": "CSC2020 Algorithms", "mark": "68"},
                 {"name": "CSC3099 Dissertation", "mark": "77"}],
    MARNIE.name: [{"name": "HIS1010 Medieval Europe", "mark": "66"}, {"name": "HIS2040 Archival Methods", "mark": "75"},
                  {"name": "HIS3001 Dissertation: The parish registers of Harrowgate Vale", "mark": "71"}]}


def add_cv(con, text: str) -> int:
    """`text` added to CVs and read; its source's id."""
    source_id = pipeline.add_uploads(con, "cv", [Incoming("CV.md", text.encode())])["added"][0]
    pipeline.read(con, source_id)
    return source_id


def add_document(con, name: str, text: str, kind: str, target=None, section: str = "document") -> dict:
    """`text` added to `section` as `kind`, for the card `target`, and read; its source as the page shows it."""
    source_id = pipeline.add_uploads(con, section, [Incoming(name, text.encode())], doc_kind=kind,
                                     target_id=target)["added"][0]
    pipeline.read(con, source_id)
    return store.get(con, source_id)


def only(con, kind: str) -> dict:
    """The one shown card of `kind`."""
    (found,) = profile_store.items(con, kind, hidden=False)
    return found


class Transcripts(unittest.TestCase):
    def test_marks_and_grade_come_from_the_transcript_on_the_card_it_names_not_from_the_cv(self):
        for person in GRADUATES:
            with self.subTest(person=person.name), home() as (con, _, _):
                add_cv(con, person.cv)
                degree = only(con, "education")
                add_document(con, "transcript.md", person.transcript, "transcript", degree["id"])
                after = only(con, "education")
            self.assertEqual(after["id"], degree["id"])
            self.assertEqual(after["fields"]["modules"], TRANSCRIPT_MARKS[person.name])
            self.assertIn("transcript", {e["doc_type"] for e in after["evidence"]})

    def test_a_transcripts_grade_beats_the_cvs_and_its_rows_are_quoted(self):
        with home() as (con, _, _):
            add_cv(con, MARNIE.cv)
            add_document(con, "transcript.md", MARNIE.transcript, "transcript", only(con, "education")["id"])
            degree = only(con, "education")
        self.assertEqual(degree["fields"]["grade"], "Upper second class (2:1)")
        self.assertEqual(degree["fields"]["end"], "2024-07")
        ev = next(e for e in degree["evidence"] if e["doc_type"] == "transcript")
        self.assertEqual((ev["field"], ev["quote"], ev["line"], ev["role"], ev["verified"]),
                         ("grade", "Final classification: Upper second class (2:1)", 6, "own", True))

    def test_a_transcript_with_no_card_named_lands_on_the_course_at_its_institution(self):
        with home() as (con, _, _):
            add_cv(con, AVERY.cv)
            add_document(con, "transcript.md", AVERY.transcript, "transcript")
            degree = only(con, "education")
        self.assertEqual((degree["fields"]["grade"], degree["fields"]["start"], degree["fields"]["end"]),
                         ("First", "2021", "2024"))
        self.assertEqual(degree["fields"]["modules"], TRANSCRIPT_MARKS[AVERY.name])

    def test_a_transcript_added_as_something_else_is_suggested_as_a_transcript(self):
        with home() as (con, _, _):
            got = add_document(con, "marks.md", MARNIE.transcript, "other")
            cards = profile_store.items(con, "education")
        self.assertEqual((got["suggestion"], got["suggestion_kind"], got["suggestion_kind_label"]),
                         ("", "transcript", "Transcript"))
        self.assertEqual(cards, [], "Hal suggests; the person's choice stands")


class OtherDocuments(unittest.TestCase):
    def test_a_certificate_becomes_a_certificate_card_with_its_issuer_and_date(self):
        expected = {AVERY.name: ("Certificate in Data Protection Essentials", "Made-up Data Institute", "2024-03"),
                    MARNIE.name: ("Certificate in Archive Handling", "Made-up Records Society", "2024-05")}
        for person in GRADUATES:
            with self.subTest(person=person.name), home() as (con, _, _):
                add_document(con, "certificate.md", person.certificate, "certificate")
                card = only(con, "certificate")
            fields = card["fields"]
            self.assertEqual((fields["name"], fields["issuer"], fields["date"]), expected[person.name])
            self.assertEqual(card["evidence"][0]["doc_type"], "certificate")

    def test_a_reference_attaches_to_its_job_with_the_referees_words_as_another_persons(self):
        for person, employer in ((AVERY, "Made-up Tides Ltd"), (MARNIE, "Fenwick Lantern Museum Trust")):
            with self.subTest(person=person.name), home() as (con, _, _):
                add_cv(con, person.cv)
                job = next(j for j in profile_store.items(con, "job") if j["fields"]["employer"] == employer)
                add_document(con, "reference.md", person.reference, "reference", job["id"])
                after = profile_store.item(con, job["id"])
                jobs = len(profile_store.items(con, "job"))
            ev = next(e for e in after["evidence"] if e["doc_type"] == "letter")
            self.assertEqual(ev["role"], "others")
            self.assertIn(employer, ev["quote"])
            self.assertEqual(after["fields"]["title"], job["fields"]["title"])
            self.assertEqual(jobs, 2, "no new job card")

    def test_a_reference_about_no_job_card_gives_no_facts(self):
        with home() as (con, _, _):
            got = add_document(con, "reference.md", MARNIE.reference, "reference")
            facts = con.execute("SELECT COUNT(*) FROM profile_evidence").fetchone()[0]
        self.assertEqual((got["state"], facts), ("read", 0))

    def test_anything_else_keeps_its_text_title_and_date_only(self):
        with home() as (con, vault, _):
            got = add_document(con, "check.md", CHECK, "other")
            facts = con.execute("SELECT COUNT(*) FROM profile_evidence").fetchone()[0]
            note = (vault / got["note_rel"]).read_text(encoding="utf-8")
        self.assertEqual((got["title"], got["dated"], facts), ("Basic check certificate", "2025-06", 0))
        self.assertIn("No unspent convictions", note)

    def test_a_medical_paper_is_kept_out(self):
        with home() as (con, _, _):
            got = add_document(con, "fit note.md", MEDICAL, "other")
            facts = con.execute("SELECT COUNT(*) FROM profile_evidence").fetchone()[0]
        self.assertEqual((got["state"], got["note_rel"], facts), ("kept_out", None, 0))

    def test_a_document_may_name_an_education_job_or_certificate_card_and_no_other(self):
        with home() as (con, _, _):
            add_cv(con, AVERY.cv)
            skill = profile_store.items(con, "skill")[0]["id"]
            with self.assertRaisesRegex(ValueError, "belong to a education or job or certificate, not a skill"):
                add_document(con, "check.md", CHECK, "other", skill)

    def test_modules_are_read_from_a_table_by_its_mark_column(self):
        text = "| Module | Credits | Grade |\n| --- | --- | --- |\n| Studio practice | 40 | Merit |\n"
        self.assertEqual(documents.modules_in(text), [{"name": "Studio practice", "mark": "Merit"}])


if __name__ == "__main__":
    unittest.main()

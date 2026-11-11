"""The Documents reader: a transcript sets an education card's modules, marks and grade above every CV, a
certificate makes a certificate card, a reference attaches to its job with the referee's words as another
person's, and anything else keeps only its text, title and date. Private papers are kept out. A course's
Official papers use the same rules on that course's card: a transcript sets its modules, marks and grade, and a
degree certificate its grade.

The two made-up graduates of `support.graduates` (Avery Quillon, software; Marnie Quillfeather, history) and
every name, place and file are invented.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import graduates
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


def job_card(con, **match) -> dict:
    """The one shown job card whose fields hold `match`."""
    (found,) = [j for j in profile_store.items(con, "job", hidden=False)
                if all(j["fields"].get(k) == v for k, v in match.items())]
    return found


class JobDocuments(unittest.TestCase):
    def test_a_job_description_adds_duties_below_the_current_cvs(self):
        with home() as (con, _, _):
            add_cv(con, AVERY.cv)
            job = job_card(con, employer="Made-up Tides Ltd")
            got = add_document(con, "job description.md", AVERY.job_document, "job_description", job["id"], "job_doc")
            after = profile_store.item(con, job["id"])
            jobs = len(profile_store.items(con, "job"))
        self.assertEqual((got["state"], got["note_rel"]),
                         ("read", "Sources/Work/Junior developer/Job description.md"))
        self.assertEqual(after["fields"]["title"], "Junior developer")
        self.assertEqual(after["fields"]["duties"], ["Built a tide alert service in Python",
                                                     "Answer customer questions about tide data",
                                                     "Review other developers' code"])
        ev = next(e for e in after["evidence"] if e["source_id"] == got["id"])
        self.assertEqual((ev["doc_type"], ev["role"], ev["quote"]), ("letter", "own", "Job title: Junior developer"))
        self.assertEqual(jobs, 2)

    def test_a_contract_beats_an_older_cv_on_the_job_only_that_cv_gives(self):
        with home() as (con, _, _):
            add_cv(con, MARNIE.cv)
            pipeline.read(con, pipeline.add_uploads(con, "cv", [Incoming("old.md", MARNIE.older_cv.encode())])
                          ["added"][0])
            paper = job_card(con, employer="Harrowgate Gazette")
            add_document(con, "contract.md", MARNIE.job_document, "contract", paper["id"], "job_doc")
            after = profile_store.item(con, paper["id"])
        fields = after["fields"]
        self.assertEqual((fields["title"], fields["start"], fields["end"]),
                         ("Paper delivery round", "2018-03", "2020-08"))
        self.assertEqual(fields["duties"], ["delivering papers", "collecting payments", "keeping the round book"],
                         "the older CV's duties stay out once a document gives some")
        self.assertEqual(fields["location"], "Harrowgate", "the older CV still fills what nothing else gives")

    def test_a_reference_on_a_job_card_is_the_referees_word_and_never_beats_the_current_cv(self):
        with home() as (con, _, _):
            add_cv(con, MARNIE.cv)
            job = job_card(con, employer="Fenwick Lantern Museum Trust")
            got = add_document(con, "reference.md", MARNIE.reference, "reference", job["id"], "job_doc")
            after = profile_store.item(con, job["id"])
            said = con.execute("SELECT said FROM profile_evidence WHERE source_id = ?", (got["id"],)).fetchone()[0]
        ev = next(e for e in after["evidence"] if e["source_id"] == got["id"])
        self.assertEqual(ev["role"], "others")
        self.assertIn("volunteered as an Archive volunteer", ev["quote"])
        self.assertIn('"start": "2023-09"', said)
        self.assertEqual((after["fields"]["title"], after["fields"]["start"]), ("Archive volunteer", "2023-09"))

    def test_a_job_document_needs_the_job_it_is_for(self):
        with home() as (con, _, _), self.assertRaisesRegex(ValueError, "Choose which job"):
            pipeline.add_uploads(con, "job_doc", [Incoming("contract.md", MARNIE.job_document.encode())],
                                 doc_kind="contract")



class OfficialPapers(unittest.TestCase):
    def test_a_transcript_on_a_course_card_gives_its_modules_marks_and_grade_on_that_card_alone(self):
        with home() as (con, _, _):
            bsc = graduates.education(con, "bsc")
            access = graduates.education(con, "access")
            got = add_document(con, "transcript.md", graduates.TRANSCRIPT, "transcript", bsc, section="official")
            card, other = profile_store.item(con, bsc), profile_store.item(con, access)
        self.assertEqual(got["state"], "read")
        self.assertEqual([(m["name"], m["mark"]) for m in card["fields"]["modules"]],
                         [("COMP1010 Programming Foundations", "71"), ("COMP2040 Data Analysis", "68"),
                          ("COMP3990 Dissertation", "74")])
        self.assertEqual((card["fields"]["grade"], card["fields"]["subject"]), ("First Class Honours",
                                                                            "Made-up Computing"))
        self.assertNotIn("transcript", [e["doc_type"] for e in other["evidence"]])

    def test_a_degree_certificate_gives_its_course_the_class_of_degree_it_names_and_no_card_of_its_own(self):
        with home() as (con, _, _):
            ba = graduates.education(con, "ba")
            add_document(con, "certificate.md", graduates.CERTIFICATE, "certificate", ba, section="official")
            card = profile_store.item(con, ba)
            certificates = profile_store.items(con, "certificate")
        self.assertEqual((card["fields"]["grade"], certificates), ("Upper Second Class Honours", []))

    def test_papers_added_with_no_card_go_on_the_course_they_name(self):
        """A sorted folder added a transcript and a HEAR with no card: each made a card of its own from its headers,
        "University Student Number 4401" and "awarding bodies in England use credit" (2026-11-11)."""
        transcript = ("TRANSCRIPT OF STUDY\nMade-up Student\n Student Details\n  University Student Number    4401\n"
                      " Programme of Study\n G400      BSc Made-up Computing\n Module Details\n"
                      "COMP1010  Programming Foundations     Semester 1 20    71   Pass   1\n"
                      "The programme, approved by the University of Nowhere Fen, was taught in English.\n")
        hear = ("HIGHER EDUCATION ACHIEVEMENT REPORT\nThe University of Nowhere Fen awards the BSc Made-up Computing.\n"
                "degree-awarding bodies in England use credit in the same way.\n")
        with home() as (con, _, _):
            ids = {k: graduates.education(con, k) for k in ("access", "bsc")}
            profile_store.add_found(con, "education", {"institution": "University Student Number 4401"},
                                    {"source_rel": graduates.CV_REL, "doc_type": "cv", "role": "own"})
            got = [add_document(con, n, t, "transcript", section="official")
                   for n, t in (("transcript.pdf.md", transcript), ("hear.md", hear))]
            cards = profile_store.items(con, "education", hidden=False)
            bsc = profile_store.item(con, ids["bsc"])
        self.assertEqual([g["target_id"] for g in got], [ids["bsc"], ids["bsc"]])
        self.assertEqual(len(cards), 3, "no card of their own")
        self.assertIn({"name": "COMP1010 Programming Foundations", "mark": "71"}, bsc["fields"]["modules"])
        self.assertEqual(bsc["fields"]["qualification"], "BSc")

    def test_papers_naming_no_one_course_plainly_are_not_put_on_a_card(self):
        both = "The University of Nowhere Fen: modules in Made-up Computing and History.\n"
        cards = tuple({"id": i, "fields": dict(graduates.COURSES[k])} for i, k in enumerate(("bsc", "ba")))
        self.assertIsNone(documents.card_named(cards, both))
        self.assertEqual(documents.card_named(cards, both.replace(" and History", ""))["id"], 0)

    def test_credits_before_a_mark_are_not_part_of_the_modules_name(self):
        text = "HIS2040 Archival Methods 20 75\nYear 2 average 68\n"
        self.assertEqual(documents.modules_in(text), [{"name": "HIS2040 Archival Methods", "mark": "75"}])

    def test_a_rows_status_and_attempts_after_the_mark_are_not_the_mark(self):
        """A transcript's "CSC1034 Programming Portfolio 1 Semester 1 30 85 Pass 1" gave the mark 1 (2026-11-11)."""
        text = ("HIS1010  Medieval Europe              Semester 1 30    85   Pass      1\n"
                "HIS1020  Early Modern Records         Full year  20    58   Compensated 2\n"
                "HIS3990  Dissertation                         40        20      71    P       1\n")
        self.assertEqual(documents.modules_in(text), [{"name": "HIS1010 Medieval Europe", "mark": "85"},
                                                      {"name": "HIS1020 Early Modern Records", "mark": "58"},
                                                      {"name": "HIS3990 Dissertation", "mark": "71"}])


if __name__ == "__main__":
    unittest.main()

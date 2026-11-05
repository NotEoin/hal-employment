"""The profile store: found facts merge into items with their sources, conflicts are named, and the person's
choices win.

Each test opens a fresh database in a temporary folder. The people, employers, places and files are made up
(see support/people.py); the humanities graduate is Marnie Quillfeather.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.people import HUMANITIES

from jobhunt import db
from jobhunt.profile import conflicts, merge, store

NOW = "2026-11-05T09:00:00Z"
CV_A = "Sources/CVs and letters/Marnie Quillfeather CV 2025.md"
CV_B = "Sources/CVs and letters/Marnie CV museums.md"
TRANSCRIPT = "Sources/Education/Nowhere Fen transcript.md"
NOTES = "Sources/Study notes/Archive week.md"


def ev(source_rel: str, doc_type: str, quote: str = "", field: str = "") -> dict:
    """Evidence as the ingest gives it."""
    return {"source_rel": source_rel, "quote": quote, "field": field, "doc_type": doc_type}


class Store(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect(folder / "jobs.db")
        self.addCleanup(self.con.close)

    def job(self, source: str, doc_type: str = "cv", **fields) -> int:
        base = {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"}
        return store.add_found(self.con, "job", {**base, **fields}, ev(source, doc_type, "Collections volunteer"))

    def open_conflicts(self, kind: str | None = None) -> list[dict]:
        return [c for c in conflicts.listing(self.con) if kind is None or c["kind"] == kind]

    def test_the_same_job_from_two_cvs_is_one_item_with_both_sources(self):
        a = self.job(CV_A, period="Sept 2023 - present", duties=["Catalogued donated letters"])
        b = self.job(CV_B, employer="The Fenwick Lantern Museum Trust Ltd", start="09/2023", end="present",
                     duties=["Catalogued donated letters", "Led Saturday tours"])
        self.assertEqual(a, b)
        got = store.item(self.con, a)
        self.assertEqual([e["source_rel"] for e in got["evidence"]], [CV_A, CV_B])
        self.assertEqual((got["fields"]["start"], got["fields"].get("current")), ("2023-09", True))
        self.assertEqual(got["fields"]["duties"], ["Catalogued donated letters", "Led Saturday tours"])
        self.assertEqual(got["when"], "September 2023 to now")
        self.assertEqual(self.open_conflicts(), [])

    def test_a_job_with_another_title_at_the_same_employer_and_touching_dates_is_merged(self):
        a = self.job(CV_A, title="Visitor assistant", start="May 2022", end="Aug 2023")
        b = self.job(CV_B, title="Front of house assistant", start="Sep 2023", end="Jun 2024")
        self.assertEqual(a, b)

    def test_conflicting_start_dates_make_one_conflict_naming_both_sources(self):
        a = self.job(CV_A, start="May 2024", current=True)
        self.job(CV_B, start="June 2024", current=True)
        self.job(CV_B, start="June 2024", current=True)        # the same file read again adds nothing new
        found = self.open_conflicts("field")
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual((c["field"], c["item_ids"]), ("start", [a]))
        self.assertEqual([v["words"] for v in c["values"]], ["May 2024", "June 2024"])
        self.assertIn(CV_A, c["why"])
        self.assertIn(CV_B, c["why"])

    def test_a_date_written_two_ways_is_no_conflict(self):
        self.job(CV_A, start="May 2024", current=True)
        self.job(CV_B, start="05/2024", current=True)
        self.job(NOTES, "notes", start="2024", current=True)
        self.assertEqual(self.open_conflicts(), [])

    def test_a_transcript_grade_beats_a_cv_grade(self):
        degree = {"qualification": "BA (Hons)", "subject": HUMANITIES.you["education.subject"],
                  "institution": HUMANITIES.you["education.university"], "period": "2022-25"}
        a = store.add_found(self.con, "education", {**degree, "grade": "First"}, ev(CV_A, "cv", "BA (Hons), First"))
        b = store.add_found(self.con, "education", {**degree, "grade": "2:1"},
                            ev(TRANSCRIPT, "transcript", "Classification: Upper Second (2:1)"))
        store.add_found(self.con, "education", {**degree, "grade": "First"}, ev(CV_B, "resume", "First"))
        self.assertEqual(a, b)
        self.assertEqual(store.item(self.con, a)["fields"]["grade"], "2:1")
        (c,) = self.open_conflicts("field")
        self.assertEqual(c["field"], "grade")
        self.assertEqual(c["values"][0]["value"], "2:1")
        self.assertIn("transcript", c["why"])

    def test_an_edit_survives_a_later_found_fact(self):
        a = self.job(CV_A, title="Collections volunter", start="May 2024", current=True)
        store.edit(self.con, a, {"title": "Collections volunteer", "start": "June 2024"})
        b = self.job(CV_A, title="Collections volunter", start="May 2024", current=True,
                     duties=["Sorted a donated letter collection"])
        self.assertEqual(a, b, "the file the person corrected still matches its item")
        got = store.item(self.con, a)
        self.assertEqual((got["state"], got["fields"]["title"], got["fields"]["start"]),
                         ("edited", "Collections volunteer", "2024-06"))
        self.assertNotIn("duties", got["fields"])
        self.assertEqual(len(got["evidence"]), 2)
        self.assertEqual(self.open_conflicts("field"), [], "an edited item has no field conflicts")

    def test_a_confirmed_item_is_not_changed_by_a_later_found_fact(self):
        a = self.job(CV_A, start="May 2024", current=True)
        store.confirm(self.con, a)
        self.job(CV_B, start="May 2024", location="Harrowgate Vale", current=True)
        got = store.item(self.con, a)
        self.assertEqual((got["state"], got["fields"].get("location")), ("confirmed", None))

    def test_a_hidden_item_stays_hidden_when_its_file_is_read_again(self):
        a = self.job(CV_A, start="May 2024", current=True)
        store.hide(self.con, a)
        self.assertEqual(self.job(CV_A, start="May 2024", current=True), a)
        self.assertEqual(store.item(self.con, a)["state"], "hidden")
        self.assertEqual(store.items(self.con, "job", hidden=False), [])

    def test_overlapping_full_time_jobs_and_a_long_gap_are_flagged(self):
        a = self.job(CV_A, title="Archive assistant", employer="Brackenholt Record Office",
                     start="Jan 2021", end="Dec 2021")
        b = self.job(CV_A, title="Library assistant", employer="Saltmarsh Cross Library",
                     start="Oct 2021", end="Mar 2022")
        c = self.job(CV_A, title="Museum assistant", employer="Fenwick Lantern Museum Trust",
                     start="Jan 2023", end="Jun 2023")
        d = self.job(CV_A, title="Weekend guide", employer="Harrowgate Vale Abbey", hours="part-time",
                     start="Feb 2023", end="Jun 2023")
        (overlap,) = self.open_conflicts("overlap")
        self.assertEqual(overlap["item_ids"], [a, b])
        self.assertNotIn(d, overlap["item_ids"], "a part-time job may overlap another")
        (gap,) = self.open_conflicts("gap")
        self.assertEqual(gap["item_ids"], [b, c])
        self.assertIn("9 months", gap["why"])
        self.assertGreater(9, conflicts.GAP_MONTHS)

    def test_a_short_gap_is_not_flagged(self):
        self.job(CV_A, title="Archive assistant", employer="Brackenholt Record Office", start="Jan 2021",
                 end="Dec 2021")
        self.job(CV_A, title="Museum assistant", employer="Fenwick Lantern Museum Trust", start="Jun 2022",
                 end="Jun 2023")
        self.assertEqual(self.open_conflicts("gap"), [])

    def test_an_end_before_a_start_is_flagged_and_a_chosen_value_settles_it(self):
        a = self.job(CV_A, start="May 2024", end="March 2023")
        (c,) = self.open_conflicts("dates")
        self.assertEqual(c["item_ids"], [a])
        done = conflicts.resolve(self.con, c["id"], value="March 2025")
        self.assertEqual((done["state"], done["resolution"]), ("resolved", {"value": "March 2025"}))
        got = store.item(self.con, a)
        self.assertEqual((got["state"], got["fields"]["end"]), ("edited", "2025-03"))
        self.assertEqual(self.open_conflicts(), [])

    def test_keeping_one_of_two_overlapping_jobs_hides_the_other(self):
        a = self.job(CV_A, title="Archive assistant", employer="Brackenholt Record Office", period="2021")
        b = self.job(CV_B, title="Archive assistant", employer="Brackenholt Records", period="2021")
        store.edit(self.con, a, {"start": "Jan 2021", "end": "Dec 2021"})
        store.edit(self.con, b, {"start": "Feb 2021", "end": "Dec 2021"})
        (c,) = self.open_conflicts("overlap")
        with self.assertRaises(ValueError):
            conflicts.resolve(self.con, c["id"], keep_item=999)
        conflicts.resolve(self.con, c["id"], keep_item=a)
        self.assertEqual(store.item(self.con, b)["state"], "hidden")
        with self.assertRaises(ValueError):
            conflicts.resolve(self.con, c["id"], keep_all=True)

    def test_a_resolved_conflict_stays_resolved_when_the_files_are_read_again(self):
        self.job(CV_A, title="Archive assistant", employer="Brackenholt Record Office", start="Jan 2021",
                 end="Dec 2021")
        self.job(CV_A, title="Museum assistant", employer="Fenwick Lantern Museum Trust", start="Jan 2023",
                 end="Jun 2023")
        (gap,) = self.open_conflicts("gap")
        conflicts.resolve(self.con, gap["id"], keep_all=True)
        self.job(CV_A, title="Museum assistant", employer="Fenwick Lantern Museum Trust", start="Jan 2023",
                 end="Jun 2023")
        self.assertEqual(self.open_conflicts(), [])
        self.assertEqual(len(conflicts.listing(self.con, "resolved")), 1)

    def test_skills_merge_without_case_and_through_other_names(self):
        a = store.add_found(self.con, "skill", {"name": "MS Excel"}, ev(CV_A, "cv", "MS Excel (pivot tables)"))
        b = store.add_found(self.con, "skill", {"name": "excel", "level": "advanced"},
                            ev(NOTES, "notes", "built the loans spreadsheet in Excel"))
        c = store.add_found(self.con, "skill", {"name": "Archival Research"}, ev(CV_A, "cv", "archival research"))
        d = store.add_found(self.con, "skill", {"name": "archive research"}, ev(NOTES, "notes", "archive research"))
        self.assertEqual((a, c), (b, d))
        self.assertNotEqual(a, c)
        got = store.item(self.con, a)
        self.assertEqual((got["fields"]["name"], got["fields"]["level"]), ("MS Excel", "advanced"))
        self.assertEqual([e["quote"] for e in got["evidence"]],
                         ["MS Excel (pivot tables)", "built the loans spreadsheet in Excel"])

    def test_a_humanities_profile_keeps_modules_and_marks_with_the_transcripts_mark_first(self):
        course = {"qualification": "BA (Hons)", "subject": HUMANITIES.you["education.subject"],
                  "institution": HUMANITIES.you["education.university"]}
        a = store.add_found(self.con, "education", {**course, "modules": [{"name": "Early Modern Letters"},
                                                                          {"name": "Oral History in Practice"}]},
                            ev(CV_A, "cv", "Modules: Early Modern Letters, Oral History in Practice"))
        store.add_found(self.con, "education",
                        {**course, "modules": [{"name": "Early modern letters", "mark": "72"},
                                               {"name": "Archives and Memory", "mark": "68"}]},
                        ev(TRANSCRIPT, "transcript", "Early Modern Letters 72"))
        mods = store.item(self.con, a)["fields"]["modules"]
        self.assertEqual(mods, [{"name": "Early modern letters", "mark": "72"},
                                {"name": "Archives and Memory", "mark": "68"},
                                {"name": "Oral History in Practice", "mark": ""}])
        store.add_found(self.con, "skill", {"name": "Archival research", "category": "research"},
                        ev(TRANSCRIPT, "transcript", "dissertation drew on parish records"))
        self.assertEqual([i["title"] for i in store.items(self.con, "skill")], ["Archival research"])

    def test_a_hand_added_item_can_be_deleted_and_a_found_one_cannot(self):
        mine = store.add_by_hand(self.con, "certificate", {"name": "Emergency First Aid at Work",
                                                           "issuer": "Made-up Safety Council", "date": "March 2025"})
        self.assertEqual((mine["state"], mine["fields"]["date"], mine["can_delete"]), ("edited", "2025-03", True))
        found = self.job(CV_A, start="May 2024")
        with self.assertRaises(PermissionError):
            store.delete(self.con, found)
        store.delete(self.con, mine["id"])
        with self.assertRaises(LookupError):
            store.item(self.con, mine["id"])

    def test_an_unknown_kind_or_field_is_refused_by_name(self):
        with self.assertRaisesRegex(ValueError, "pet"):
            store.add_found(self.con, "pet", {"name": "Biscuit"}, ev(NOTES, "notes"))
        with self.assertRaisesRegex(ValueError, "company"):
            store.add_found(self.con, "job", {"company": "Fenwick"}, ev(NOTES, "notes"))
        with self.assertRaisesRegex(ValueError, "source_rel"):
            store.add_found(self.con, "skill", {"name": "Cataloguing"}, {"quote": "cataloguing"})
        with self.assertRaisesRegex(ValueError, "needs"):
            store.add_found(self.con, "job", {"location": "Harrowgate Vale"}, ev(NOTES, "notes"))


    def test_one_degree_written_many_ways_is_one_item_and_a_ba_then_an_ma_stay_two(self):
        # a real ingest gave one degree as ten items: "BSc History", "History BSc", "Bachelor of Arts with
        # Honours in History" and the like, at "Wexley University" and "University of Wexley" (2026-11-05)
        def edu(source, **fields):
            return store.add_found(self.con, "education", fields, ev(source, "cv", "History"))
        first = edu("Sources/CVs and letters/a.md", qualification="BA History", institution="University of Wexley",
                    start="2021-09")
        self.assertEqual(edu("Sources/CVs and letters/b.md", qualification="History BA (Hons)",
                             institution="Wexley University"), first)
        self.assertEqual(edu("Sources/CVs and letters/c.md", qualification="Bachelor of Arts with Honours in History",
                             institution="The University of Wexley"), first)
        self.assertNotEqual(edu("Sources/CVs and letters/d.md", qualification="MA Archives and Records",
                                institution="University of Wexley", start="2024-09"), first)
        with self.assertRaises(ValueError):
            edu("Sources/Coursework/e.md", qualification="Dissertation (HIS3020)", institution="University of Wexley")

    def test_near_identical_certificate_names_merge_but_different_levels_do_not(self):
        def cert(source, name):
            return store.add_found(self.con, "certificate", {"name": name}, ev(source, "certificate", name))
        marshal = cert("Sources/Certificates/a.md", "Fire Marshal")
        self.assertEqual(cert("Sources/Certificates/b.md", "The Fire Marshall"), marshal)
        level2 = cert("Sources/Certificates/c.md", "Food Safety Level 2")
        self.assertNotEqual(cert("Sources/Certificates/d.md", "Food Safety Level 3"), level2)

    def test_a_rebuild_merges_found_items_again_and_keeps_what_the_person_checked(self):
        mine = self.job("Sources/CVs and letters/a.md", start="2023-09")
        store.edit(self.con, mine, {"title": "Collections volunteer (Saturdays)"})
        store.add_found(self.con, "certificate", {"name": "Fire Marshal"}, ev("Sources/x.md", "certificate", "x"))
        with self.con:   # two items the old rules left apart
            self.con.execute("INSERT INTO profile_items (kind, fields, state, origin, created, updated) "
                             "VALUES ('certificate', '{\"name\": \"The Fire Marshall\"}', 'hal', 'found', '', '')")
            self.con.execute("INSERT INTO profile_evidence (item_id, source_rel, quote, field, doc_type, said, "
                             "found_at) VALUES (last_insert_rowid(), 'Sources/y.md', 'y', '', 'certificate', "
                             "'{\"name\": \"The Fire Marshall\"}', '')")
        store.rebuild_found(self.con)
        certs = store.items(self.con, "certificate")
        self.assertEqual(len(certs), 1)
        self.assertEqual(len(certs[0]["evidence"]), 2)
        self.assertEqual(store.item(self.con, mine)["fields"]["title"], "Collections volunteer (Saturdays)")

class Dates(unittest.TestCase):
    def test_uk_ways_of_writing_a_date_are_read(self):
        cases = {"May 2024": ("2024-05", "", False), "05/2024": ("2024-05", "", False),
                 "2023-24": ("2023-09", "2024-06", False), "Sept 2023 - present": ("2023-09", "", True),
                 "Summer 2023": ("2023-06", "2023-08", False), "1st May 2024": ("2024-05", "", False),
                 "Sep 2021 to Jun 2024": ("2021-09", "2024-06", False), "2019 - 2022": ("2019", "2022", False),
                 "Winter 2023": ("2023-12", "2024-02", False), "01/05/2024": ("2024-05", "", False)}
        for text, want in cases.items():
            with self.subTest(text):
                self.assertEqual(merge.parse_period(text), want)

    def test_an_en_dash_range_is_read_like_a_hyphen(self):
        self.assertEqual(merge.parse_period("Sep 2021 \u2013 Jun 2024"), ("2021-09", "2024-06", False))

    def test_words_that_are_not_dates_are_kept_as_written(self):
        self.assertEqual(merge.parse_period("Marketing 2024"), ("Marketing 2024", "", False))
        self.assertEqual(merge.clean_date("the spring term"), "the spring term")

    def test_a_stored_month_reads_back_as_itself(self):
        self.assertEqual(merge.clean_date("2011-12"), "2011-12")

    def test_ranks_put_the_transcript_first_and_notes_last(self):
        order = ["transcript", "certificate", "cv", "letter", "notes"]
        self.assertEqual(sorted(order, key=merge.rank, reverse=True), order)
        self.assertEqual(merge.rank("HEAR"), merge.rank("transcript"))
        self.assertEqual(merge.rank("resume"), merge.rank("cv"))


if __name__ == "__main__":
    unittest.main()

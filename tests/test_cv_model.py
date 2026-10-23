"""Schema 2 of the CV: ids, canonical JSON, changes by target, the diff and today's cv.json shape.

Every CV here is a made-up person's.
"""
import unittest

import support  # noqa: F401 - the temporary home, before any jobhunt import

from jobhunt.cvstudio import model
from jobhunt.cvstudio.model import Change, CVError

V1 = {"title": "History graduate", "contact": {"location": "Exampleton"}, "summary": "Careful researcher.",
      "moreProjects": "Also: a podcast", "closing": "", "pdfProjects": 1,
      "education": [{"qualification": "BA (Hons) History", "grade": "2:1", "meta": "Made-up University | 2023 - 2026",
                     "detail": "- Dissertation on canal towns\n- Ran the history society",
                     "modules": [{"year": "Final", "mark": "68", "rows": [["Archives", 74], ["Oral history", 66]]}],
                     "pdfModulesMin": 65, "modulesNote": "Marks out of 100"}],
      "projects": [{"slug": "canal-map", "name": "Canal map", "stack": "QGIS", "body": "A map of old wharves."}],
      "experience": [{"role": "Bookseller", "org": "Made-up Books", "dates": "Sep 2023 - Jun 2024",
                      "body": "Ran the local history shelf."},
                     {"role": "Tour guide", "org": "Made-up Museum", "dates": "Summer 2022", "body": "Led tours.",
                      "hidden": True}],
      "skills": [["Research", ["Archives", "Interviews"]]], "additional": [["Interests", "Fell running"]],
      "_comment": ["kept by the website"]}


class Ids(unittest.TestCase):
    def test_an_entry_id_comes_from_its_kind_and_original_heading(self):
        self.assertEqual(model.entry_id("education", "BA (Hons) History"),
                         model.entry_id("education", "BA (Hons) History"))
        self.assertNotEqual(model.entry_id("education", "X"), model.entry_id("projects", "X"))
        self.assertRegex(model.entry_id("projects", "Canal map"), r"^pr-[0-9a-f]{8}$")

    def test_two_entries_with_the_same_heading_get_different_ids(self):
        v1 = {**V1, "projects": [{"name": "Map", "stack": "", "body": "One."},
                                 {"name": "Map", "stack": "", "body": "Two."}]}
        ids = [e.id for e in model.from_v1(v1).sections[2].entries]
        self.assertEqual(len(set(ids)), 2)


class JsonForm(unittest.TestCase):
    def test_the_same_document_always_gives_the_same_json(self):
        doc = model.from_v1(V1)
        self.assertEqual(model.to_json(doc), model.to_json(model.from_json(model.to_json(doc))))
        self.assertEqual(model.from_json(model.to_json(doc)), doc)
        self.assertTrue(model.to_json(doc).isascii())

    def test_a_wrong_field_is_named_by_its_path(self):
        data = model.to_dict(model.from_v1(V1))
        data["sections"][1]["entries"][0]["bullets"][0]["text"] = 5
        with self.assertRaisesRegex(CVError, r"cv\.sections\[1\]\.entries\[0\]\.bullets\[0\]\.text"):
            model.from_dict(data)
        with self.assertRaisesRegex(CVError, "cv.colour"):
            model.from_dict({**model.to_dict(model.from_v1(V1)), "colour": "red"})

    def test_validate_reports_duplicate_ids_and_missing_titles(self):
        doc = model.from_v1(V1)
        doc.sections[2].entries.append(doc.sections[1].entries[0])
        doc.sections[3].title = ""
        problems = model.validate(doc)
        self.assertTrue(any("Two parts have the id" in p for p in problems))
        self.assertTrue(any("has no title" in p for p in problems))
        self.assertEqual(model.validate(model.from_v1(V1)), [])


class Changes(unittest.TestCase):
    def setUp(self):
        self.doc = model.from_v1(V1)
        self.edu = self.doc.sections[1]
        self.entry = self.edu.entries[0]
        self.t = model.target_of(self.edu, self.entry)

    def test_a_change_returns_a_new_document_and_leaves_the_old_one(self):
        b = self.entry.bullets[0]
        new = model.apply(self.doc, Change("replace", f"{self.t}/b:{b.id}", "Dissertation on canal towns, 1840s"))
        self.assertEqual(model.find(new, f"{self.t}/b:{b.id}").text, "Dissertation on canal towns, 1840s")
        self.assertEqual(self.entry.bullets[0].text, "Dissertation on canal towns")

    def test_insert_move_hide_retitle_reorder_and_delete_work_by_target(self):
        doc = model.apply(self.doc, Change("insert", self.t, "Won the department prize", position=0))
        self.assertEqual(model.find(doc, self.t).bullets[0].text, "Won the department prize")
        doc = model.apply(doc, Change("move", f"{self.t}/b:{model.find(doc, self.t).bullets[0].id}", position=2))
        self.assertEqual(model.find(doc, self.t).bullets[2].text, "Won the department prize")
        doc = model.apply(doc, Change("hide", "s:projects"))
        self.assertFalse(model.find(doc, "s:projects").visible)
        doc = model.apply(doc, Change("retitle", "s:experience", "Work experience"))
        self.assertEqual(model.find(doc, "s:experience/title"), "Work experience")
        doc = model.apply(doc, Change("reorder", "s:experience", [e.id for e in doc.sections[3].entries][::-1]))
        self.assertEqual(doc.sections[3].entries[0].heading, "Tour guide")
        doc = model.apply(doc, Change("delete", f"s:experience/e:{doc.sections[3].entries[0].id}"))
        self.assertEqual(len(model.find(doc, "s:experience").entries), 1)
        with self.assertRaises(CVError):
            model.apply(doc, Change("reorder", "s:skills", ["sk-nothere"]))

    def test_a_missing_target_is_a_cv_error(self):
        with self.assertRaisesRegex(CVError, "no 'nope'"):
            model.find(self.doc, "s:nope")
        with self.assertRaises(CVError):
            model.apply(self.doc, Change("replace", "s:education/e:ed-00000000/text", "x"))


class Diff(unittest.TestCase):
    def test_a_renamed_and_reworded_entry_is_reworded_not_added(self):
        a = model.from_v1(V1)
        exp = a.sections[3]
        t = model.target_of(exp, exp.entries[0])
        b = model.apply(a, Change("replace", t, {"heading": "Senior bookseller"}))
        b = model.apply(b, Change("replace", f"{t}/text", "Ran the local history shelf and its events."))
        kinds = {(c["kind"], c["target"].rsplit("/", 1)[-1]) for c in model.diff(a, b)}
        self.assertEqual(kinds, {("Reworded", "heading"), ("Reworded", "text")})
        self.assertEqual(model.diff(a, a), [])

    def test_moves_additions_hidden_and_left_out_are_named(self):
        a = model.from_v1(V1)
        b = model.apply(a, Change("reorder", "s:experience", [e.id for e in a.sections[3].entries][::-1]))
        b = model.apply(b, Change("hide", f"s:projects/e:{a.sections[2].entries[0].id}"))
        b = model.apply(b, Change("insert", "s:skills", {"heading": "Languages", "items": ["Welsh"]}))
        b = model.apply(b, Change("delete", f"s:additional/e:{a.sections[5].entries[0].id}"))
        kinds = sorted(c["kind"] for c in model.diff(a, b))
        self.assertEqual(kinds, ["Added", "Hidden", "Left out", "Moved"])


class TodaysShape(unittest.TestCase):
    def test_cv_json_goes_through_schema_2_and_back_unchanged(self):
        from jobhunt.cvstudio import import_v1

        back = model.to_v1(model.from_v1(V1), {"contact": V1["contact"]})
        self.assertEqual(import_v1.printable(back), import_v1.printable(V1))

    def test_marks_come_back_as_numbers_and_the_year_mark_is_kept(self):
        back = model.to_v1(model.from_v1(V1), {})
        self.assertEqual(back["education"][0]["modules"], V1["education"][0]["modules"])

    def test_the_contact_block_comes_from_the_profile_never_the_cv(self):
        doc = model.from_v1(V1)
        self.assertEqual(doc.contact_snapshot, V1["contact"])
        self.assertEqual(model.to_v1(doc, {"contact": {"location": "Newtown"}})["contact"], {"location": "Newtown"})

    def test_plain_text_leaves_out_hidden_entries(self):
        text = model.plain_text(model.from_v1(V1), {"name": "Ada Example", "status": "Course ends: 2026."})
        self.assertIn("Ran the local history shelf.", text)
        self.assertNotIn("Led tours.", text)
        self.assertTrue(text.startswith("Ada Example\nHistory graduate"))
        self.assertTrue(text.endswith("Course ends: 2026."))

    def test_nothing_is_lost_on_the_way_in(self):
        from jobhunt.cvstudio import import_v1

        self.assertEqual(model.not_found(import_v1.printable(V1), model.from_v1(V1)), [])
        lost = model.from_v1(V1)
        lost.sections[3].entries[0].text = ""
        self.assertEqual(model.not_found(import_v1.printable(V1), lost), ["Ran the local history shelf."])


if __name__ == "__main__":
    unittest.main()

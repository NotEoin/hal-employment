"""The courses a sort's folders name, proposed first as education cards (`sections.sort_review`): accepted or
renamed by the person, after which the coursework and lecture notes waiting for them find their card.

The dump is `test_sections_sorter`'s made-up Marnie Quillfeather (history); the AI is the fake one.
"""
import json
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import fake_ai
from test_ingest import home
from test_sections_sort import sorted_folder
from test_sections_sorter import history_dump

from jobhunt.profile import store as profile_store
from jobhunt.sections import sort_review, sorts

NONE = {"sort_pick": json.dumps({"choice": "none"})}


class CoursesHalFound(unittest.TestCase):
    def test_a_course_its_folders_name_is_proposed_and_accepting_it_gives_its_files_their_card(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, history_dump(files))
            self.assertGreater(sorts.accept(con, sort_id)["need_card"], 0, "with no course, files wait")
            found = sort_review.courses_found(con, sort_id)
            self.assertEqual([(c["title"], c["modules"]) for c in found],
                             [("BA History", ["HIS2040 Archival Methods"])])
            self.assertEqual(profile_store.items(con, "education"), [], "a course found is only a proposal")
            out = sort_review.accept_course(con, sort_id, found[0]["key"], {"institution": "University of Nowhere Fen"})
            card = profile_store.item(con, out["card"]["id"])
            self.assertEqual((card["title"], card["fields"]["institution"], card["fields"]["modules"]),
                             ("BA History", "University of Nowhere Fen",
                              [{"name": "HIS2040 Archival Methods", "mark": ""}]))
            self.assertGreater(out["matched"], 0)
            self.assertEqual(sort_review.courses_found(con, sort_id), [], "a course with its card is no longer listed")
            waiting = [r for r in sorts.rows(con, sort_id, limit=sorts.PAGE_MAX)["rows"] if r["needs_card"]]
            self.assertEqual(waiting, [], "every file in its folders, and the rest, found the card")

    def test_a_renamed_course_keeps_the_persons_words_and_an_unknown_one_is_refused(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, history_dump(files))
            key = sort_review.courses_found(con, sort_id)[0]["key"]
            with self.assertRaises(LookupError):
                sort_review.accept_course(con, sort_id, "Marnie files/Somewhere else")
            out = sort_review.accept_course(con, sort_id, key, {"qualification": "BA (Hons)",
                                                                 "subject": "History and Heritage"})
            self.assertEqual(out["card"]["title"], "BA (Hons) History and Heritage")
            with self.assertRaises(LookupError):
                sort_review.accept_course(con, sort_id, key)


class ModuleGroups(unittest.TestCase):
    def test_a_modules_lecture_material_is_one_row_changed_as_a_whole(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, history_dump(files))
            flat = sorts.rows(con, sort_id, section="lecture", limit=sorts.PAGE_MAX)
            grouped = sort_review.rows(con, sort_id, section="lecture")
            self.assertEqual([(r["name"], r["count"]) for r in grouped["rows"]], [("HIS2040 Archival Methods", 3)])
            self.assertEqual((flat["total"], grouped["total"]), (3, 1))
            key = grouped["rows"][0]["group"]
            self.assertEqual(sorted(r["name"] for r in sort_review.rows(con, sort_id, group=key)["rows"]),
                             [f"Seminar notes week {n}.md" for n in (1, 2, 3)])
            self.assertEqual(sort_review.change_group(con, sort_id, key, {"ticked": False}), {"changed": 3})
            self.assertFalse(any(r["ticked"] for r in flat_rows(con, sort_id, "lecture")))
            self.assertEqual(sorts.accept(con, sort_id, "lecture")["accepted"], 0, "an unticked group stays out")
            with self.assertRaises(ValueError):
                sort_review.change_group(con, sort_id, key, {"star": True})
            with self.assertRaises(LookupError):
                sort_review.change_group(con, sort_id, "lecture||Marnie files/Nowhere", {"ticked": True})
            card = sort_review.accept_course(con, sort_id, sort_review.courses_found(con, sort_id)[0]["key"])["card"]
            moved = sort_review.rows(con, sort_id, section="lecture")["rows"][0]
            self.assertEqual((moved["target_id"], moved["target_title"], moved["needs_card"]),
                             (card["id"], "BA History", False))


def flat_rows(con, sort_id: int, section: str) -> list[dict]:
    """Every proposed row of one section, file by file."""
    return sorts.rows(con, sort_id, section=section, limit=sorts.PAGE_MAX)["rows"]


if __name__ == "__main__":
    unittest.main()

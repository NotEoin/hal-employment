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


if __name__ == "__main__":
    unittest.main()

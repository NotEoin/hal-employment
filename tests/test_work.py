"""Work classes: the default class of each kind of action, and the class registered kinds end up with."""
import unittest

import support  # noqa: F401 - puts the run in a temporary home before jobhunt is imported

from jobhunt.core import runner, work


class Defaults(unittest.TestCase):
    def test_a_kinds_default_class_comes_from_its_priority_then_its_first_word(self):
        self.assertEqual(work.for_kind("find.check_job", 0), work.ON_SCREEN)
        self.assertEqual(work.for_kind("record.sync", 2), work.RECORD)
        self.assertEqual(work.for_kind("record.sources", 1), work.RECORD)
        self.assertEqual(work.for_kind("docs.prepare", 1), work.DOCUMENTS)
        self.assertEqual(work.for_kind("cv.suggest", 1), work.ON_SCREEN)
        self.assertEqual(work.for_kind("cv.import_v1", 2), work.OTHER)
        self.assertEqual(work.for_kind("mail.check", 2), work.OTHER)

    def test_the_registered_kinds_put_the_person_first_and_documents_after_the_record(self):
        kinds = runner.load_kinds()
        self.assertEqual(kinds["cv.recruiter"].work_class, work.ON_SCREEN)
        self.assertEqual(kinds["docs.change"].work_class, work.ON_SCREEN)
        for kind in ("record.ingest", "record.sources", "record.sort_accept", "record.sync"):
            self.assertEqual(kinds[kind].work_class, work.RECORD, kind)
        self.assertEqual(kinds["docs.prepare"].work_class, work.DOCUMENTS)
        self.assertEqual(kinds["record.sort"].work_class, work.OTHER)   # proposes places, reads nothing in
        self.assertEqual(kinds["find.rescore"].work_class, work.OTHER)

    def test_an_unknown_class_is_refused(self):
        with self.assertRaises(ValueError), work.entered(7):
            pass


if __name__ == "__main__":
    unittest.main()

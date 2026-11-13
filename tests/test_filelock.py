"""Tests of the shared non-blocking file lock."""

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - puts the run in a temporary home before jobhunt is imported

from jobhunt.core import filelock


class TryLockTests(unittest.TestCase):
    def test_second_taker_gets_none_until_the_first_lets_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "x.lock"
            first = filelock.try_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(filelock.try_lock(path))
            first.close()
            again = filelock.try_lock(path)
            self.assertIsNotNone(again)
            again.close()

    def test_lock_creates_the_missing_parent_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "x.lock"
            file = filelock.try_lock(path)
            self.assertTrue(path.is_file())
            file.close()


if __name__ == "__main__":
    unittest.main()

"""The matching index: naming a connection never leaves it in a transaction that pins an old snapshot."""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import db
from jobhunt.profile import index


class Token(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = folder / "jobs.db"
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)

    def test_a_write_after_another_connection_commits_still_lands_once_the_index_named_the_connection(self):
        """Regression (2.3.3's CI, 2026-11-19): the token's insert left an implicit transaction open, the record
        read's next SELECT pinned a snapshot, an Activity row committed on another connection meanwhile, and
        the read's write failed at once with "database is locked"."""
        index.match(self.con, "skill", {"name": "Palaeography"})
        self.assertFalse(self.con.in_transaction, "naming the connection left a transaction open")
        self.con.execute("SELECT COUNT(*) FROM profile_items").fetchone()
        other = db.connect(self.path)
        with other:
            other.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('made-up', '1')")
        other.close()
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('made-up-too', '1')")
        self.assertEqual(self.con.execute("SELECT v FROM meta WHERE k = 'made-up-too'").fetchone()[0], "1")

    def test_naming_a_connection_inside_the_callers_transaction_leaves_it_to_the_caller(self):
        self.con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('kept-for-the-caller', '1')")
        index.match(self.con, "skill", {"name": "Palaeography"})
        self.assertTrue(self.con.in_transaction, "the caller's own transaction was committed for it")
        self.con.rollback()
        self.assertIsNone(self.con.execute("SELECT v FROM meta WHERE k = 'kept-for-the-caller'").fetchone())


if __name__ == "__main__":
    unittest.main()

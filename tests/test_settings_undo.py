"""Undo in Activity for a setting change: the row the settings store writes can be undone from Activity.

Regression: neither the settings nor the history module registered the "setting" undo handler, so Undo on a setting's
setting's Activity row answered "Hal cannot undo this kind of change yet."
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_runner import fresh_home

from jobhunt.core import activity, settings
from jobhunt.core.settings import store


class SettingUndo(unittest.TestCase):
    def setUp(self):
        store.drop_cache()
        self.addCleanup(store.drop_cache)
        self.con = self.enterContext(fresh_home())

    def test_undo_in_activity_puts_the_old_setting_back(self):
        settings.set("you.city", "Harrowgate Vale")
        changed = settings.set("you.city", "Lindenford")
        activity.undo(changed.activity_id)
        self.assertEqual(settings.get("you.city"), "Harrowgate Vale")
        undone = self.con.execute("SELECT undone FROM activity WHERE id = ?", (changed.activity_id,)).fetchone()
        self.assertTrue(undone["undone"])
        with self.assertRaisesRegex(RuntimeError, "undone already"):
            activity.undo(changed.activity_id)


if __name__ == "__main__":
    unittest.main()

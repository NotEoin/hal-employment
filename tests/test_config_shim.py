"""The old configuration names: answered from live settings, never from the checkout's config.toml
outside the legacy layout, read-only outside tests, and never holding the carer answer.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import config, record
from jobhunt.core import paths, settings
from jobhunt.core.settings import secrets, store

OLD_FILE = """alert_score = 91
web_port = 8811
apply_browser = "window"
signed_in_apply = ["made-up-board.example"]

[applicant]
city = "Oldbridge"
carer = "Made-up carer answer"
"""


class Shim(unittest.TestCase):
    """Each test in a fresh temporary home."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hal-shim-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        override = paths.set_override(self.home)
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)
        store.drop_cache()
        self.addCleanup(store.drop_cache)
        self.old_file = self.home / "checkout-config.toml"
        self.old_file.write_text(OLD_FILE, encoding="utf-8")
        patch = mock.patch.object(config, "CONFIG_PATH", self.old_file)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(setattr, config, "_file_cache", None)

    def test_the_board_list_comes_from_the_imported_copy(self):
        # the checkout's boards.toml is gone; the import left a copy in the data folder
        imported = paths.data_dir() / "import" / "boards.toml"
        imported.parent.mkdir(parents=True, exist_ok=True)
        imported.write_text('[[board]]\nname = "Made-up Board"\nats = "greenhouse"\nslug = "madeup"\n')
        self.assertEqual(config.boards_path(), imported)
        self.assertEqual([b["name"] for b in config.boards()], ["Made-up Board"])

    def test_old_config_keys_still_answer_through_the_shim(self):
        settings.set("notify.alert_score", 80)
        settings.set("advanced.port", 8800)
        settings.set("applying.window", "this")
        settings.set("you.city", "Harrowgate Vale")
        settings.set("you.relocate", True)
        self.assertEqual(config.CONFIG.get("alert_score", 70), 80)
        self.assertEqual(config.CONFIG.get("web_port", 8737), 8800)
        self.assertEqual(config.CONFIG.get("apply_window", "separate"), "same")
        self.assertEqual(config.CONFIG.get("apply_browser", "window"), "extension")
        self.assertEqual(config.CONFIG.get("applicant", {}), {"city": "Harrowgate Vale", "relocate": "Yes"})
        self.assertEqual(config.CONFIG["web_port"], 8800)

    def test_an_empty_setting_leaves_the_callers_own_default(self):
        self.assertEqual(config.CONFIG.get("hal_root", "~/made-up/Hal"), "~/made-up/Hal")
        settings.set("addon.hal_root", "/made-up/elsewhere/Hal")
        self.assertEqual(config.CONFIG.get("hal_root", "~/made-up/Hal"), "/made-up/elsewhere/Hal")
        self.assertEqual(config.CONFIG.get("no_such_old_key", "fallback"), "fallback")

    def test_a_new_home_never_reads_the_checkout_config_toml(self):
        with mock.patch.object(config.tomllib, "loads", side_effect=AssertionError("config.toml was read")):
            self.assertEqual(config.CONFIG.get("alert_score", 70), 75)
            self.assertEqual(config.CONFIG.get("applicant", {}), {})
            self.assertEqual(config.legacy_file(), {})
        self.assertEqual(record._LIVE["VAULT"](), paths.record_default())   # the live value, not a test's assignment

    def test_the_legacy_layout_still_reads_the_old_file_until_the_import(self):
        with mock.patch.object(paths, "is_legacy_layout", return_value=True):
            self.assertEqual(config.CONFIG.get("alert_score", 70), 91)
            self.assertEqual(config.CONFIG.get("apply_browser", "window"), "window")
            self.assertEqual(config.CONFIG.get("applicant", {}), {"city": "Oldbridge"})
            settings.set("notify.alert_score", 60)
            self.assertEqual(config.CONFIG.get("alert_score", 70), 60)

    def test_hal_never_gets_a_signed_in_apply_list(self):
        with mock.patch.object(paths, "is_legacy_layout", return_value=True):
            self.assertEqual(config.CONFIG.get("signed_in_apply", []), [])

    def test_the_carer_answer_is_never_in_the_applicant_view(self):
        with mock.patch.object(paths, "is_legacy_layout", return_value=True):
            self.assertNotIn("carer", config.CONFIG.get("applicant", {}))

    def test_the_phone_alert_code_comes_from_the_secrets_store(self):
        with secrets.use_keyring(None):
            self.assertIsNone(config.CONFIG.get("ntfy_topic"))
            secrets.put("notify.ntfy_topic", "made-up-topic-5521")
            self.assertEqual(config.CONFIG.get("ntfy_topic"), "made-up-topic-5521")

    def test_config_is_read_only_outside_tests(self):
        env = {k: v for k, v in os.environ.items() if k != paths.ENV_TEST}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(paths, "_under_test_runner", return_value=False), \
                self.assertRaises(TypeError):
            config.CONFIG["alert_score"] = 1

    def test_a_test_may_patch_config_and_it_is_put_back(self):
        with mock.patch.dict(config.CONFIG, {"alert_score": 42}, clear=True):
            self.assertEqual(config.CONFIG.get("alert_score"), 42)
        self.assertEqual(config.CONFIG.get("alert_score"), 75)

    def test_private_answers_keep_the_old_function(self):
        file = paths.private_path()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('[applicant]\ndisability = "No"\n', encoding="utf-8")
        self.assertEqual(config.private(), {"disability": "No"})


if __name__ == "__main__":
    unittest.main()

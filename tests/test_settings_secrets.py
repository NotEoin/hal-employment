"""The secrets store: the keyring first, a protected file without one, never a prompt from the
background, and never a value in the database or a status.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import db
from jobhunt.core import paths
from jobhunt.core.settings import secrets, store

APP_PASSWORD = "made-up-app-password-1234"


class FakeKeyring:
    """A keyring that can be locked; it counts unlock prompts so a test can see none were shown."""

    def __init__(self, *, locked: bool = False, keeps: bool = True) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.locked = locked
        self.keeps = keeps
        self.prompts = 0

    def is_locked(self) -> bool:
        return self.locked

    def unlock(self) -> None:
        self.prompts += 1
        self.locked = False

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        if self.keeps:
            self.values[(service, name)] = value

    def delete_password(self, service, name):
        self.values.pop((service, name), None)


class Secrets(unittest.TestCase):
    """Each test in a fresh temporary home."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hal-secrets-")
        self.addCleanup(tmp.cleanup)
        override = paths.set_override(Path(tmp.name))
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)
        store.drop_cache()
        self.addCleanup(store.drop_cache)

    def keyring(self, fake):
        ctx = secrets.use_keyring(fake)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        return fake

    def test_secrets_fall_back_to_a_protected_file_without_a_keyring(self):
        self.keyring(None)
        self.assertEqual(secrets.put("email.app_password", f"  {APP_PASSWORD}  "), "file")
        self.assertEqual(secrets.get("email.app_password"), APP_PASSWORD)
        self.assertEqual(secrets.status("email.app_password"), "saved")
        self.assertEqual(secrets.backend(), {"name": "a protected file", "locked": False})
        path = secrets.file_path()
        self.assertEqual(path.parent, paths.config_dir())
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        secrets.delete("email.app_password")
        self.assertEqual(secrets.status("email.app_password"), "not saved")
        self.assertNotIn(APP_PASSWORD, path.read_text())

    def test_a_locked_keyring_is_reported_and_never_prompts_from_the_background(self):
        fake = self.keyring(FakeKeyring())
        self.assertEqual(secrets.put("sites.reed_key", "made-up-reed-key"), "keyring")
        fake.locked = True
        with self.assertRaises(secrets.SecretsLocked) as caught:
            secrets.get("sites.reed_key")
        self.assertIn("unlock your keyring", str(caught.exception))
        self.assertEqual(secrets.status("sites.reed_key"), "locked")
        self.assertEqual(secrets.backend()["locked"], True)
        self.assertEqual(fake.prompts, 0)
        self.assertEqual(secrets.get("sites.reed_key", interactive=True), "made-up-reed-key")
        self.assertEqual(fake.prompts, 1)

    def test_a_keyring_that_does_not_keep_the_value_falls_back_to_the_file(self):
        self.keyring(FakeKeyring(keeps=False))
        self.assertEqual(secrets.put("sites.adzuna_key", "made-up-adzuna-key"), "file")
        self.assertEqual(secrets.get("sites.adzuna_key"), "made-up-adzuna-key")

    def test_saving_to_the_keyring_removes_an_older_copy_in_the_file(self):
        self.keyring(None)
        secrets.put("notify.ntfy_topic", "made-up-topic-one")
        with secrets.use_keyring(FakeKeyring()):
            self.assertEqual(secrets.put("notify.ntfy_topic", "made-up-topic-two"), "keyring")
            self.assertEqual(secrets.get("notify.ntfy_topic"), "made-up-topic-two")
        self.assertNotIn("notify.ntfy_topic", json.loads(secrets.file_path().read_text()))

    def test_a_home_override_never_reaches_the_system_keyring(self):
        self.assertIsNone(secrets._keyring())

    def test_secret_values_never_reach_the_database(self):
        self.keyring(None)
        secrets.put("email.app_password", APP_PASSWORD)
        con = db.connect()
        try:
            dump = "\n".join(con.iterdump())
        finally:
            con.close()
        self.assertNotIn(APP_PASSWORD, dump)

    def test_an_unknown_secret_name_is_refused(self):
        with self.assertRaises(KeyError):
            secrets.put("email.not_a_secret", "x")
        with self.assertRaises(ValueError):
            secrets.put("email.app_password", "   ")


class Routes(unittest.TestCase):
    """The secrets routes never answer with a value."""

    def setUp(self):
        from support.api import HalServer

        ctx = secrets.use_keyring(None)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()

    def test_a_saved_secret_is_only_ever_reported_as_saved(self):
        reply = self.client.request("PUT", "/api/v2/secrets/email.app_password", {"value": APP_PASSWORD})
        self.assertEqual(reply.json(), {"status": "saved", "store": {"name": "a protected file", "locked": False}})
        listing = self.client.get("/api/v2/settings")
        self.assertEqual(listing.json()["secrets"]["email.app_password"], "saved")
        self.assertNotIn(APP_PASSWORD, listing.text)
        self.assertEqual(self.client.request("DELETE", "/api/v2/secrets/email.app_password").json(),
                         {"status": "not saved"})

    def test_an_empty_secret_is_refused_with_its_field(self):
        reply = self.client.request("PUT", "/api/v2/secrets/sites.reed_key", {"value": " "})
        self.assertEqual((reply.status, reply.json()["error"]["field"]), (400, "sites.reed_key"))
        self.assertEqual(self.client.request("PUT", "/api/v2/secrets/nothing", {"value": "x"}).status, 404)

    def test_retry_is_offered_only_on_the_computer_hal_runs_on(self):
        remote = self.hal.signed_in(remote=True, remote_host="desktop")
        self.assertEqual(remote.post("/api/v2/secrets/retry").status, 409)
        self.assertEqual(self.client.post("/api/v2/secrets/retry").json(), {"locked": False})


if __name__ == "__main__":
    unittest.main()

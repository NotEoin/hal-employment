"""Privacy and data: where things are kept, Export everything and Delete everything."""
import io
import unittest
import zipfile
from unittest import mock

import support  # noqa: F401 - sets the test home before jobhunt is imported
from support.api import HalServer
from support.private_answers import saved

from jobhunt.core import helpfile, paths, privacy_routes, runner
from jobhunt.core.settings import secrets

CANARY = "SYNTH-PRIVATE-5521 made-up adjustment"
SECRET = "made-up-app-password-9183"


class Privacy(unittest.TestCase):
    def setUp(self):
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()

    def export_zip(self) -> zipfile.ZipFile:
        reply = self.client.post("/api/v2/privacy/export")
        self.assertEqual(reply.status, 200, reply.text)
        done = runner.wait(reply.json()["action_id"], timeout=20)
        self.assertEqual(done["state"], "done", done.get("error"))
        from jobhunt import db

        con = db.connect()
        self.addCleanup(con.close)
        rel = con.execute("SELECT rel FROM files WHERE id = ?", (done["result"]["file_id"],)).fetchone()[0]
        return zipfile.ZipFile(io.BytesIO((helpfile.exports_dir() / rel).read_bytes()))

    def test_export_holds_no_secrets_or_private_answers(self):
        secrets.put("email.app_password", SECRET)
        docs = paths.applications_dir() / "0001 Made-up Museum - Archive Assistant"
        docs.mkdir(parents=True)
        (docs / "letter.txt").write_text("Dear Made-up Museum, a made-up letter.", encoding="utf-8")
        (docs / "answers.md").write_text(f"Adjustments: {CANARY}", encoding="utf-8")
        (docs / "notes.txt").write_text(f"pasted by mistake: {SECRET}", encoding="utf-8")
        with saved({"adjustments": CANARY, "disability": "Yes"}):
            zf = self.export_zip()
        names = zf.namelist()
        self.assertIn("data/jobs.db", names)
        self.assertIn("Applications/0001 Made-up Museum - Archive Assistant/letter.txt", names)
        self.assertNotIn("data/private.toml", names)
        self.assertFalse([n for n in names if n.startswith("config")])
        for name in names:
            data = zf.read(name)
            self.assertNotIn(SECRET.encode(), data, name)
            self.assertNotIn(b"SYNTH-PRIVATE", data, name)
        readme = zf.read("README.txt").decode()
        self.assertIn("answers.md: it held one of your private answers", readme)
        self.assertIn("notes.txt: it held a saved password or key", readme)

    def test_the_page_says_where_data_is_and_what_leaves_this_computer(self):
        body = self.client.get("/api/v2/privacy").json()
        self.assertEqual(body["where"]["data"], str(paths.data_dir()))
        self.assertTrue(body["can_open"])
        self.assertEqual(body["leaves"][0], "Searches sent to job sites")
        self.assertNotIn("Reading your email (read only)", body["leaves"])
        secrets.put("email.app_password", SECRET)
        self.assertIn("Reading your email (read only)", self.client.get("/api/v2/privacy").json()["leaves"])

    def test_delete_needs_the_confirm_word(self):
        reply = self.client.post("/api/v2/privacy/delete", {})
        self.assertEqual(reply.status, 400)
        self.assertTrue(paths.data_dir().exists())

    def test_delete_everything_leaves_the_code_folder(self):
        code = paths.code_dir()
        before = sorted(p.name for p in code.iterdir())
        secrets.put("email.app_password", SECRET)
        record = paths.record_default()
        record.mkdir(parents=True, exist_ok=True)
        (record / "certificate.txt").write_text("A made-up certificate.", encoding="utf-8")
        paths.applications_dir().mkdir(parents=True, exist_ok=True)
        with mock.patch.object(privacy_routes, "stop_hal") as stop:
            reply = self.client.post("/api/v2/privacy/delete", {"confirm": "delete"})
        self.assertEqual(reply.status, 200, reply.text)
        self.assertEqual(reply.json(), {"ok": True, "not_deleted": []})
        stop.assert_called_once()
        for folder in (paths.data_dir(), paths.config_dir(), paths.cache_dir(), paths.documents_dir()):
            self.assertFalse(folder.exists(), folder)
        self.assertEqual(secrets.status("email.app_password"), "not saved")
        self.assertTrue((record / "certificate.txt").exists())
        self.assertEqual(sorted(p.name for p in code.iterdir()), before)
        self.assertTrue((code / "jobhunt" / "__init__.py").exists())

    def test_delete_refuses_a_folder_that_holds_the_program(self):
        with mock.patch.object(paths, "cache_dir", return_value=paths.code_dir().parent):
            with self.assertRaises(Exception) as caught:
                privacy_routes.folders_to_delete()
        self.assertIn("program files", str(caught.exception))

    def test_open_data_folder_refuses_in_a_remote_session(self):
        remote = self.hal.signed_in(remote=True, remote_host="desktop")
        with mock.patch.object(privacy_routes, "open_folder") as opened:
            reply = remote.post("/api/v2/privacy/open-data-folder")
        self.assertEqual(reply.status, 409)
        opened.assert_not_called()
        with mock.patch.object(privacy_routes, "open_folder") as opened:
            self.assertEqual(self.client.post("/api/v2/privacy/open-data-folder").status, 200)
        opened.assert_called_once_with(paths.data_dir())


if __name__ == "__main__":
    unittest.main()

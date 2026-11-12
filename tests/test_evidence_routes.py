"""The Your record page's API: overview, files, downloads (also from another computer), uploads and actions.

Every test runs a real server in its own temporary home; the record folder is that home's default. The AI
is the fake one.
"""
import http.client
import json
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from support.api import TIMEOUT_S, HalServer
from support.fake_ai import fake_ai

from jobhunt import record
from jobhunt.core import paths, runner
from jobhunt.evidence import routes

WAIT_S = 10


def upload(hal, client, files: dict[str, bytes]):
    """POSTs `files` as a multipart form, the way the page's drop zone sends them."""
    boundary = uuid.uuid4().hex
    parts = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file{i}\"; filename=\"{name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n"
        for i, (name, data) in enumerate(files.items()))
    body = parts + f"--{boundary}--\r\n".encode()
    con = http.client.HTTPConnection("127.0.0.1", hal.port, timeout=TIMEOUT_S)
    try:
        con.request("POST", "/api/v2/record/files", body=body, headers={
            "Host": f"127.0.0.1:{hal.port}", "X-Hal": "1", "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": "; ".join(f"{k}={v}" for k, v in client.jar.items())})
        r = con.getresponse()
        return r.status, json.loads(r.read())
    finally:
        con.close()


def done(action_id: str) -> dict:
    """The action once it has ended."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        found = runner.wait(action_id, timeout=1)
        if found["state"] not in ("queued", "running"):
            return found
    raise AssertionError(f"action {action_id} did not end")


class RecordRoutes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.folder = Path(paths.record_default())
        # Other test modules point the record somewhere of their own; here it is the server home's default.
        self.enterContext(mock.patch.object(record, "VAULT", self.folder, create=True))
        self.addCleanup(record._CACHE.clear)

    def put(self, rel: str, text: str) -> None:
        path = self.folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_reading_a_record_folder_not_made_yet_is_no_error(self):
        """A fresh install showed every hourly read of the record as an error naming a path (2026-11-12)."""
        import shutil

        shutil.rmtree(self.folder, ignore_errors=True)
        done = runner.run("record.sync")
        self.assertEqual((done["state"], done["result"]), ("done", {"converted": [], "changed": 0, "removed": 0}))

    def test_making_the_folder_gives_the_starter_folders_and_a_start_note(self):
        c = self.hal.signed_in()
        self.assertFalse(c.get("/api/v2/record").json()["exists"])
        self.assertEqual(c.post("/api/v2/record/create-folder").status, 200)
        names = {p.name for p in self.folder.iterdir()}
        self.assertTrue({"Certificates", "Education", "Jobs", "Inbox", "Private", "Start Here.md"} <= names)
        page = c.get("/api/v2/record").json()
        self.assertEqual((page["exists"], page["can_open"], page["private_folders"]), (True, True, ["Private"]))

    def test_the_page_lists_files_by_group_and_marks_private_ones(self):
        self.put("Certificates/First Aid.md", "# First Aid\n\nEmergency first aid at work, 2025.\n")
        self.put("Private/Health.md", "# Health\n\nA private note.\n")
        self.put("loose.txt", "A note at the top.")
        c = self.hal.signed_in()
        groups = {g["name"]: g for g in c.get("/api/v2/record").json()["groups"]}
        self.assertEqual(list(groups), ["Certificates", "Private", "Other files"])
        self.assertEqual((groups["Certificates"]["n"], groups["Private"]["private"]), (1, True))
        found = c.get("/api/v2/record/files?group=Private").json()
        self.assertEqual([(f["name"], f["private"]) for f in found], [("Health.md", True)])

    def test_a_record_file_downloads_in_a_remote_session(self):
        self.put("References/Made-up Archive.txt", "Zoe was a careful volunteer.")
        laptop = self.hal.signed_in(remote=True, remote_host="laptop")
        page = laptop.get("/api/v2/record").json()
        self.assertFalse(page["can_open"])
        fid = laptop.get("/api/v2/record/files").json()[0]["file_id"]
        got = laptop.get(f"/api/v2/record/file/{fid}")
        self.assertEqual((got.status, got.body), (200, b"Zoe was a careful volunteer."))
        self.assertIn("attachment", got.headers["Content-Disposition"])
        # Opening the folder acts on the desktop, so it is refused from the laptop.
        self.assertEqual(laptop.post("/api/v2/record/open-folder").status, 409)

    def test_no_file_outside_the_record_can_be_downloaded(self):
        self.put("Notes/a.md", "# A\n")
        c = self.hal.signed_in()
        for rel in ("../config/local-api-key", "../data/jobs.db", "Notes/missing.md"):
            self.assertEqual(c.get(f"/api/v2/record/file/{routes.file_id(rel)}").status, 404, rel)
        self.assertEqual(c.get("/api/v2/record/file/!!!").status, 404)

    def test_asking_the_record_answers_with_its_sources(self):
        self.put("Certificates/First Aid.md", "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n")
        record.sync()
        c = self.hal.signed_in()
        self.assertEqual(c.post("/api/v2/record/ask", {"question": " "}).status, 400)
        with fake_ai({"record_ask": "On 5 March 2025 [Certificates/First Aid.md]."}):
            action = c.post("/api/v2/record/ask", {"question": "When did I do first aid at work?"}).json()
            result = done(action["action_id"])
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["result"]["sources"], ["Certificates/First Aid.md"])
        self.assertIn("5 March 2025", result["result"]["answer"])

    def test_open_folder_opens_it_on_this_computer(self):
        routes.make_default()
        c = self.hal.signed_in()
        with mock.patch.object(routes, "open_folder") as opened:
            self.assertEqual(c.post("/api/v2/record/open-folder").status, 200)
        opened.assert_called_once_with(self.folder)

    def test_choosing_another_folder_needs_one_that_exists(self):
        c = self.hal.signed_in()
        self.assertEqual(c.post("/api/v2/record/folder", {"path": str(self.hal.home / "nowhere")}).status, 400)
        other = self.hal.home / "Elsewhere"
        other.mkdir()
        self.assertEqual(c.post("/api/v2/record/folder", {"path": str(other)}).status, 200)
        # the setting decides again, as it does outside the tests; put back before setUp's patch ends, which
        # failed when this module ran alone (no other module had set record.VAULT)
        saved = record.VAULT
        del record.VAULT
        self.addCleanup(setattr, record, "VAULT", saved)
        self.assertEqual(Path(record._live("VAULT")), other.resolve())


if __name__ == "__main__":
    unittest.main()

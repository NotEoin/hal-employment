"""The API of the "Add" panels on Your record, against a real server in a temporary home.

Uploads go as the page sends them (a multipart form of `file0`, `path0` ...). The made-up graduates are
Avery Quillon (software) and Marnie Quillfeather (history); every name and file is invented.
"""
import http.client
import json
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

from support.api import TIMEOUT_S, HalServer

from jobhunt import db, record
from jobhunt.core import paths, runner
from jobhunt.evidence.routes import UPLOAD_MAX_BODY
from jobhunt.profile import store as profile_store
from jobhunt.web import router

WAIT_S = 10
AWARD = b"# Volunteer of the year\n\nAwarded to Marnie Quillfeather on 5 March 2025.\n"
ESSAY = b"# The parish registers of Harrowgate Vale\n\nAn essay on baptisms, 1750 to 1850.\n"
CV = (b"# Avery Quillon\n\nCurriculum vitae\n\n## Work experience\n\nJunior developer, Made-up Tides Ltd.\n\n"
      b"## Education\n\nBSc Made-up Computing, University of Nowhere Fen.\n")


def form(hal, client, path: str, files: list[tuple[str, bytes, str]], fields: dict | None = None):
    """POSTs `files` [(name, data, path inside a folder or "")] and `fields` as the page's multipart form."""
    boundary = uuid.uuid4().hex
    parts = []
    for i, (name, data, rel) in enumerate(files):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file{i}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        if rel:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"path{i}\"\r\n\r\n{rel}\r\n".encode())
    for key, value in (fields or {}).items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    con = http.client.HTTPConnection("127.0.0.1", hal.port, timeout=TIMEOUT_S)
    try:
        con.request("POST", "/api/v2/record/sources" + path, body=body, headers={
            "Host": f"127.0.0.1:{hal.port}", "X-Hal": "1", "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": "; ".join(f"{k}={v}" for k, v in client.jar.items())})
        r = con.getresponse()
        return r.status, json.loads(r.read())
    finally:
        con.close()


def ended(action_id: str) -> dict:
    """The action once it has ended."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        found = runner.wait(action_id, timeout=1)
        if found["state"] not in ("queued", "running"):
            return found
    raise AssertionError(f"action {action_id} did not end")


def card(kind: str, fields: dict) -> int:
    """A card found on a made-up CV; its id."""
    with closing(db.connect()) as con:
        return profile_store.add_found(con, kind, fields, {"source_rel": "Sources/CVs/CV.md", "doc_type": "cv"})


class SourceRoutes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.folder = Path(paths.record_default())
        self.folder.mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.object(record, "VAULT", self.folder, create=True))
        self.addCleanup(record._CACHE.clear)
        self.c = self.hal.signed_in()

    def put(self, path: str, body: dict):
        return self.c.request("PUT", path, body)

    def test_the_sections_say_what_they_take_and_which_readers_are_still_to_come(self):
        got = {s["id"]: s for s in self.c.get("/api/v2/record/sections").json()}
        self.assertEqual(next(iter(got)), "cv")
        self.assertEqual((got["coursework"]["target"], got["coursework"]["target_required"]), ("education", True))
        self.assertTrue(all(k["ready"] for k in got["document"]["doc_kinds"]))
        self.assertEqual({k["id"] for k in got["project"]["doc_kinds"] if k["ready"]},
                         {"repo", "upload", "repo_url", "github_account", "local_folder"})
        held = {k["id"]: k["held"] for k in got["project"]["doc_kinds"]}
        self.assertEqual((held["github_account"], held["local_folder"], held["web_page"], held["upload"]),
                         ("account", "folder", "link", "files"))

    def test_documents_are_added_read_and_listed_and_the_same_file_twice_is_named(self):
        status, out = form(self.hal, self.c, "/document", [("award.md", AWARD, ""), ("essay.md", ESSAY, "")])
        self.assertEqual(status, 200, out)
        self.assertEqual(len(out["added"]), 2)
        self.assertEqual(ended(out["action_id"])["state"], "done")
        listed = self.c.get("/api/v2/record/sources?section=document").json()
        self.assertEqual(sorted((s["title"], s["state"]) for s in listed),
                         [("The parish registers of Harrowgate Vale", "read"), ("Volunteer of the year", "read")])
        status, again = form(self.hal, self.c, "/coursework", [("award copy.md", AWARD, "")],
                             {"target_id": card("education", {"qualification": "BA", "subject": "History",
                                                              "institution": "University of Nowhere Fen"})})
        self.assertEqual((status, again["added"], again["action_id"]), (200, [], None))
        self.assertEqual(again["duplicates"][0]["section_label"], "Documents")

    def test_a_chosen_folder_arrives_as_one_source(self):
        status, out = form(self.hal, self.c, "/document", [("a.md", AWARD, "Awards/a.md"),
                                                           ("b.md", ESSAY, "Awards/older/b.md")])
        self.assertEqual(status, 200, out)
        self.assertEqual([(s["held"], s["name"], s["files"]) for s in out["added"]], [("files", "Awards", 2)])

    def test_coursework_needs_its_education_card_in_plain_words(self):
        status, out = form(self.hal, self.c, "/coursework", [("essay.md", ESSAY, "")])
        self.assertEqual(status, 400)
        self.assertIn("Choose which education", out["error"]["message"])
        status, out = form(self.hal, self.c, "/photos", [("essay.md", ESSAY, "")])
        self.assertEqual(status, 404)

    def test_a_link_whose_reader_is_still_to_come_waits_and_says_why(self):
        r = self.c.post("/api/v2/record/sources/project/link", {"locator": "https://example.org/avery/tide-gauge",
                                                                "doc_kind": "web_page"})
        self.assertEqual(r.status, 200, r.text)
        ended(r.json()["action_id"])
        got = self.c.get("/api/v2/record/sources?section=project").json()[0]
        self.assertEqual(got["state"], "new")
        self.assertIn("phase 3", got["error"])
        bad = self.c.post("/api/v2/record/sources/project/link", {"locator": "example.org", "doc_kind": "web_page"})
        self.assertEqual(bad.status, 400)

    def test_a_folder_on_this_computer_is_added_only_from_this_computer_and_never_the_record(self):
        repo = Path(self.hal.home) / "code" / "tide-gauge"
        repo.mkdir(parents=True)
        body = {"locator": str(repo), "doc_kind": "local_folder"}
        laptop = self.hal.signed_in(remote=True, remote_host="laptop")
        self.assertEqual(laptop.post("/api/v2/record/sources/project/link", body).status, 409)
        inside = self.c.post("/api/v2/record/sources/project/link", {**body, "locator": str(self.folder)})
        self.assertEqual(inside.status, 400)
        self.assertIn("inside your record", inside.json()["error"]["message"])
        ok = self.c.post("/api/v2/record/sources/project/link", body)
        self.assertEqual(ok.status, 200, ok.text)
        self.assertEqual(ok.json()["added"][0]["locator"], str(repo.resolve()))

    def test_a_source_is_changed_starred_marked_current_moved_read_again_and_removed(self):
        degree = card("education", {"qualification": "BA", "subject": "History",
                                    "institution": "University of Nowhere Fen"})
        _, essay = form(self.hal, self.c, "/coursework", [("essay.md", ESSAY, "")],
                        {"target_id": degree, "doc_kind": "dissertation"})
        essay_id = essay["added"][0]["id"]
        ended(essay["action_id"])
        path = f"/api/v2/record/sources/{essay_id}"
        r = self.put(path, {"starred": True, "title": "My dissertation"})
        self.assertEqual((r.status, r.json()["starred"], r.json()["title"]), (200, True, "My dissertation"))
        ended(r.json()["action_id"])           # a starred piece is read again into a showcase project
        self.assertEqual(self.put(path, {"current": True}).status, 400)
        _, cvs = form(self.hal, self.c, "/document", [("Avery Quillon CV.md", CV, "")])
        ended(cvs["action_id"])
        cv_id = cvs["added"][0]["id"]
        self.assertEqual(self.c.get("/api/v2/record/sources?section=document").json()[0]["suggestion"], "cv")
        moved = self.put(f"/api/v2/record/sources/{cv_id}", {"section": "cv"}).json()
        self.assertEqual((moved["section"], moved["doc_kind"], moved["suggestion"]), ("cv", "cv", ""))
        ended(moved["action_id"])
        self.assertTrue(self.put(f"/api/v2/record/sources/{cv_id}", {"current": True}).json()["current"])
        again = self.c.post(f"{path}/reread", {}).json()
        self.assertEqual(ended(again["action_id"])["state"], "done")
        self.assertEqual(self.c.request("DELETE", path).status, 200)
        self.assertEqual(self.c.request("DELETE", path).status, 404)
        self.assertEqual(self.put(path, {"title": "x"}).status, 404)

    def test_the_upload_takes_the_same_size_as_the_record_upload(self):
        found = router.find("POST", "/api/v2/record/sources/document")
        self.assertEqual(found[0].max_body, UPLOAD_MAX_BODY)

    def test_a_project_card_is_starred_and_a_skill_cannot_be(self):
        project = card("project", {"name": "Tide gauge"})
        skill = card("skill", {"name": "Python"})
        r = self.put(f"/api/v2/profile/items/{project}/star", {"star": True})
        self.assertEqual((r.status, r.json()["star"], r.json()["star_by"]), (200, True, "you"))
        self.assertEqual(self.put(f"/api/v2/profile/items/{skill}/star", {"star": True}).status, 400)
        self.assertEqual(self.put("/api/v2/profile/items/9999/star", {"star": True}).status, 404)


if __name__ == "__main__":
    unittest.main()

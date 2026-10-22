"""The Documents and files routes through a real server: the list, one application's files, Copy,
Download all, Open the folder (refused from another computer) and copy-paste details without private answers.
"""
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from support.api import HalServer

from jobhunt import db
from jobhunt.core import settings
from jobhunt.docs import details as details_mod
from jobhunt.docs import files as registry
from jobhunt.docs import folders

JOB = {"id": "demo:archive-1", "source": "demo", "company": "Riverside Records", "title": "Junior Archivist",
       "location": "Leeds", "url": "https://jobs.example.org/archive-1", "description": "Catalogue <b>old</b> maps.",
       "salary": "", "posted_at": None, "board_size": None}


def fake_render(out):
    """Two made-up PDFs, as the CV and letter makers would leave them."""
    (out / "cv.pdf").write_bytes(b"%PDF-1.4 cv\n%%EOF\n")
    (out / "letter.pdf").write_bytes(b"%PDF-1.4 letter\n%%EOF\n")
    return {"cv": str(out / "cv.pdf"), "letter": str(out / "letter.pdf")}


class Documents(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.web = self.hal.signed_in()
        settings.set("you.name", "Robin Testwell")
        con = db.connect()
        self.addCleanup(con.close)
        db.upsert(con, JOB)
        con.execute("UPDATE jobs SET status = 'applying' WHERE id = ?", (JOB["id"],))
        job = con.execute("SELECT * FROM jobs WHERE id = ?", (JOB["id"],)).fetchone()
        docs = {"letter": "Dear team, a made-up letter of nine words here.", "message": "A made-up note.",
                "answers": [{"question": "Why us?", "answer": "A made-up answer."},
                            {"question": "When can you start?", "answer": "Next month."}]}
        self.folder = folders.publish(con, job, docs, render=fake_render, approved_at="2026-10-22T10:44:00Z")
        con.commit()
        self.number = folders.number_for(con, JOB["id"], create=False)

    def test_the_list_and_one_application_answer_in_the_api_shapes(self):
        apps = self.web.get("/api/v2/documents").json()["apps"]
        self.assertEqual([(a["number"], a["title"], a["column"]) for a in apps],
                         [(self.number, "Junior Archivist", "ready")])
        self.assertEqual(self.web.get("/api/v2/documents?q=nothing-like-it").json()["apps"], [])
        one = self.web.get(f"/api/v2/documents/{self.number}").json()
        self.assertEqual(one["approved_at"], "2026-10-22T10:44:00Z")
        kinds = [f["kind"] for f in one["files"]]
        self.assertEqual(kinds, ["cv_pdf", "letter_pdf", "answers_md", "message_txt", "advert_html"])
        self.assertNotIn("pack.md", json.dumps(one))
        self.assertEqual(one["files"][2]["sub"], "2 answers")
        self.assertEqual(self.web.get(f"/api/v2/documents/{JOB['id']}").json()["number"], self.number)
        self.assertEqual(self.web.get("/api/v2/documents/9999").status, 404)

    def test_copy_gives_the_approved_text(self):
        text = self.web.get(f"/api/v2/documents/{self.number}/text/letter").json()["text"]
        self.assertEqual(text, "Dear team, a made-up letter of nine words here.")
        self.assertEqual(self.web.get(f"/api/v2/documents/{self.number}/text/secrets").status, 404)

    def test_download_all_holds_every_file(self):
        reply = self.web.get(f"/api/v2/documents/{self.number}/zip")
        self.assertEqual(reply.status, 200)
        self.assertIn("attachment", reply.headers["Content-Disposition"])
        names = sorted(zipfile.ZipFile(io.BytesIO(reply.body)).namelist())
        listed = sorted(p.name for _, p in folders.listed_files(self.folder))
        self.assertEqual(names, listed)
        self.assertIn("Robin Testwell CV.pdf", names)

    def test_files_are_served_only_through_the_registry(self):
        one = self.web.get(f"/api/v2/documents/{self.number}").json()
        cv = next(f for f in one["files"] if f["kind"] == "cv_pdf")
        reply = self.web.get(f"/api/v2/files/{cv['file_id']}?download=1")
        self.assertEqual((reply.status, reply.body[:8]), (200, b"%PDF-1.4"))
        advert = next(f for f in one["files"] if f["kind"] == "advert_html")
        page = self.web.get(f"/api/v2/files/{advert['file_id']}")
        self.assertIn("default-src 'none'", page.headers["Content-Security-Policy"])
        self.assertNotIn("<b>old</b>", page.text)
        self.assertEqual(self.web.get("/api/v2/files/0123456789abcdef").status, 404)
        self.assertEqual(self.hal.client().get(f"/api/v2/files/{cv['file_id']}").status, 401)

    def test_open_folder_refuses_in_a_remote_session(self):
        with mock.patch.object(registry, "open_folder") as opened:
            here = self.web.post(f"/api/v2/documents/{self.number}/open-folder")
            self.assertEqual(here.status, 200)
            opened.assert_called_once_with(self.folder)
            laptop = self.hal.signed_in(remote=True, remote_host="desk-pc")
            away = laptop.post(f"/api/v2/documents/{self.number}/open-folder")
            self.assertEqual((away.status, away.json()["error"]["code"]), (409, "remote"))
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(laptop.get(f"/api/v2/documents/{self.number}/zip").status, 200)

    def test_copy_paste_details_come_from_your_profile_and_never_hold_private_answers(self):
        settings.set("you.email", "robin.testwell@example.org")
        rows = self.web.get("/api/v2/documents/details").json()["rows"]
        self.assertIn({"label": "Email", "value": "robin.testwell@example.org"}, rows)
        self.assertEqual(rows[0], {"label": "Full name", "value": "Robin Testwell"})
        source = Path(details_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("settings.private", source)
        self.assertNotIn("value_for_form", source)


class Tailored(unittest.TestCase):
    """Regression: a base CV with no title (the made-up world's) raised KeyError, so no CV changes showed."""

    def test_a_cv_without_a_title_or_summary_is_tailored(self):
        from jobhunt import cv, documents

        with mock.patch.object(cv, "tailor", lambda text: ({"skills": ["Cataloguing"]}, None)):
            self.assertEqual(documents.tailored(JOB, {}), {"skills": ["Cataloguing"]})
            self.assertEqual(documents.tailored(JOB, {"cv_title": "Archivist"})["title"], "Archivist")


if __name__ == "__main__":
    unittest.main()

"""What the previews show of a CV set to send "Your original file", against a real server in a temporary home.

The preview on My CV drew Hal's page for a CV that sends the person's own PDF, a page employers never get
(reported 2026-11-19). The files are the made-up CVs of `tests/fixtures/cv`, kept straight into the store as
an upload keeps them, with no reading, so these tests stay quick.
"""
import unittest
from unittest import mock

from support.api import HalServer
from support.make_cv_fixtures import FOLDER, NAMES

from jobhunt import db
from jobhunt.apply import queries
from jobhunt.cvstudio import original, store
from jobhunt.cvstudio.model import CVError

PNG_START = b"\x89PNG\r\n\x1a\n"


def keep(name: str, *, send: str = "original") -> str:
    """The made-up file `name` kept as the lead CV's own file, sent as `send`; its file id."""
    store.current()
    file_id = store.add_file((FOLDER / name).read_bytes(), name, role="baseline")
    store.set_baseline_file(file_id)
    store.set_send(send)
    return file_id


class OriginalPreview(unittest.TestCase):
    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.client = self.hal.signed_in()

    def summary(self) -> dict:
        reply = self.client.get(f"/api/v2/cv?cv={store.lead_id()}")
        self.assertEqual(reply.status, 200, reply.text)
        return reply.json()

    def test_a_cv_that_sends_its_own_pdf_previews_a_picture_of_each_of_its_pages(self):
        name = f"{NAMES['technical']}.pdf"
        file_id = keep(name)
        shown = self.summary()["original"]
        lead = store.lead_id()
        self.assertEqual((shown["kind"], shown["name"], shown["pages"], shown["why"]), ("pdf", name, 1, ""))
        self.assertEqual(shown["page_urls"], [f"/api/v2/cv/original/page/1?cv={lead}&f={file_id}"])
        page = self.client.get(shown["page_urls"][0])
        self.assertEqual((page.status, page.body[:8]), (200, PNG_START))

    def test_a_cv_that_sends_hals_copy_has_no_file_to_preview(self):
        keep(f"{NAMES['technical']}.pdf", send="hal_copy")
        self.assertIsNone(self.summary()["original"])

    def test_a_word_file_sent_as_it_is_has_no_pages_and_points_at_hals_reading(self):
        name = f"{NAMES['changer']}.docx"
        keep(name)
        body = self.summary()
        shown = body["original"]
        self.assertEqual((shown["kind"], shown["name"], shown["page_urls"]), ("docx", name, []))
        self.assertEqual(shown["version_id"], body["version"]["id"])

    def test_a_file_changed_on_disk_says_why_it_cannot_be_shown(self):
        keep(f"{NAMES['technical']}.pdf")
        changed = CVError("Your file has changed on disk since Hal kept it, so Hal will not send it.")
        con = db.connect()
        self.addCleanup(con.close)
        with mock.patch.object(store, "file_bytes", side_effect=changed):
            shown = original.view(con, store.lead_id())
        self.assertEqual((shown["kind"], shown["page_urls"], shown["why"]), (None, [], str(changed)))

    def test_an_applications_cv_tab_previews_the_file_its_cv_sends(self):
        # an application with no job's CV of its own is built from the lead CV, as the documents step says
        file_id = keep(f"{NAMES['technical']}.pdf")
        con = db.connect()
        self.addCleanup(con.close)
        shown = queries._cv_original(con, "job-with-no-cv-yet")
        self.assertEqual(shown["page_urls"], [f"/api/v2/cv/original/page/1?cv={store.lead_id()}&f={file_id}"])
        store.set_send("hal_copy")
        self.assertIsNone(queries._cv_original(con, "job-with-no-cv-yet"))


if __name__ == "__main__":
    unittest.main()

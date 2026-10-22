"""The help file: what a helper needs to see, with names, contact details, answers and private answers taken out."""
import json
import unittest

import support  # noqa: F401 - sets the test home before jobhunt is imported
from support.api import HalServer
from support.private_answers import saved

from jobhunt.core import activity, helpfile, settings

CANARY = "SYNTH-PRIVATE-4410 made-up adjustment"
NAME = "Robin Ashdown"
EMAIL = "robin.ashdown@example.com"
PHONE = "07700 900456"
CONTACT = "Priya Vantongeren"
ANSWER = "I enjoy cataloguing museum archives"


class HelpFile(unittest.TestCase):
    def setUp(self):
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()

    def seed(self, con):
        settings.set_many({"you.name": NAME, "you.email": EMAIL, "you.phone": PHONE})
        con.execute("INSERT INTO jobs (id, source, title, company, url, first_seen, last_seen)"
                    " VALUES ('demo:1', 'demo', 'Archive Assistant', 'Made-up Museum', 'https://example.com/1',"
                    " '2026-10-20', '2026-10-20')")
        con.execute("INSERT INTO applications (job_id, stage, contacts, updated) VALUES ('demo:1', 'documents', ?,"
                    " '2026-10-20')", (json.dumps([{"name": CONTACT, "email": "p.v@example.org"}]),))
        con.execute("INSERT INTO answers2 (question, label, answer, scope, updated)"
                    " VALUES ('why', 'Why us?', ?, 'job', '2026-10-20')", (json.dumps(ANSWER),))
        con.commit()
        for words in (f"Wrote a letter for {NAME} ({EMAIL}, {PHONE})", f"Found {CONTACT} at Made-up Museum",
                      f"Typed the answer {ANSWER}", f"Typed {CANARY} into a form"):
            activity.record("documents", "test", words, "A made-up reason")

    def test_the_help_file_holds_no_names_contacts_answers_or_private_answers(self):
        from jobhunt import db

        con = db.connect()
        self.addCleanup(con.close)
        self.seed(con)
        with saved({"adjustments": CANARY, "disability": "Yes"}):
            reply = self.client.post("/api/v2/help-file")
        self.assertEqual(reply.status, 200, reply.text)
        body = reply.json()
        text = body["preview_text"]
        stored = (helpfile.exports_dir() / con.execute("SELECT rel FROM files WHERE id = ?",
                                                       (body["file_id"],)).fetchone()[0]).read_text("utf-8")
        for words in (text, stored):
            for secret in (NAME, "Robin", "Ashdown", EMAIL, PHONE, CONTACT, "p.v@example.org", ANSWER,
                           "SYNTH-PRIVATE"):
                self.assertNotIn(secret, words)
        self.assertIn("[your details]", text)
        self.assertIn("Is Hal working?", text)
        self.assertIn("Recent activity", text)

    def test_the_help_file_is_registered_for_download(self):
        reply = self.client.post("/api/v2/help-file").json()
        from jobhunt import db

        con = db.connect()
        self.addCleanup(con.close)
        row = con.execute("SELECT root, kind FROM files WHERE id = ?", (reply["file_id"],)).fetchone()
        self.assertEqual(tuple(row), ("exports", "help_file"))


if __name__ == "__main__":
    unittest.main()

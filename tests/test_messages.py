"""Messages: emails Hal sorted and alerts from Hal in one list, opening marks read, filters, archive,
and Check email starting the `mail.check` action.
"""
import unittest

from support.api import HalServer
from support.world import World

from jobhunt import db
from jobhunt.core import runner
from jobhunt.mail import actions as mail_actions
from jobhunt.mail import messages


class Listing(unittest.TestCase):
    def setUp(self):
        self.w = self.enterContext(World())
        self.con = self.w.con
        self.con.execute("""INSERT INTO alerts (at, level, title, body, kind, action)
                            VALUES (?, 'urgent', 'Sign in to a made-up council job site', 'Hal never makes accounts.',
                                    'alert', '{"label": "Open the site", "url": "https://jobs.example.org"}')""",
                         (db.now(),))
        self.con.commit()

    def test_opening_a_message_marks_it_read(self):
        first = messages.listing(self.con)[0]
        self.assertTrue(first["unread"])
        messages.mark_read(self.con, first["id"])
        again = next(m for m in messages.listing(self.con) if m["id"] == first["id"])
        self.assertFalse(again["unread"])
        self.assertNotIn(first["id"], [m["id"] for m in messages.listing(self.con, "unread")])
        with self.assertRaises(LookupError):
            messages.mark_read(self.con, "e:no-such-message")

    def test_filters_keep_employers_and_hal_apart(self):
        hal = messages.listing(self.con, "hal")
        self.assertEqual([m["from"] for m in hal], ["Hal"])
        self.assertEqual(hal[0]["action"], {"label": "Open the site", "url": "https://jobs.example.org"})
        employers = messages.listing(self.con, "employers")
        self.assertTrue(employers)
        self.assertNotIn("Hal", [m["from"] for m in employers])
        self.assertNotIn("12 new jobs for you", [m["title"] for m in employers])
        with self.assertRaises(ValueError):
            messages.listing(self.con, "spam")

    def test_archive_and_mark_all_read(self):
        before = messages.listing(self.con)
        messages.archive(self.con, before[0]["id"])
        self.assertEqual(len(messages.listing(self.con)), len(before) - 1)
        messages.mark_all_read(self.con)
        self.assertEqual(messages.unread_count(self.con), 0)

    def test_check_email_sorts_new_mail_and_makes_to_dos(self):
        self.assertIn("mail.check", runner.KINDS, mail_actions.__name__)
        done = runner.run("mail.check")
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual(done["result"]["new"], 3)
        row = self.con.execute("SELECT why, changed FROM activity WHERE action_id = ?", (done["id"],)).fetchone()
        self.assertIn("Email check", row["why"])
        self.assertIn("Sorted 3 new emails", row["changed"])


class Routes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.web = self.hal.signed_in()
        con = db.connect()
        self.addCleanup(con.close)
        con.execute("""INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, snippet)
                       VALUES ('m1', '11', ?, 'Made-up Museum <jobs@museum.example>', 'robin@example.org',
                               'Interview invitation: Archive Assistant', 'interview', 'Made-up Museum',
                               'Thursday.')""",
                    (db.now(),))
        con.commit()

    def test_the_routes_list_open_and_archive(self):
        items = self.web.get("/api/v2/messages").json()["items"]
        self.assertEqual([(m["id"], m["unread"], m["from"]) for m in items], [("e:m1", True, "Made-up Museum")])
        self.assertEqual(self.web.post("/api/v2/messages/e%3Am1/read").status, 200)
        self.assertEqual(self.web.get("/api/v2/messages?filter=unread").json()["items"], [])
        self.assertEqual(self.web.post("/api/v2/messages/e%3Anope/read").status, 404)
        self.assertEqual(self.web.get("/api/v2/messages?filter=spam").status, 400)
        self.assertEqual(self.web.post("/api/v2/messages/e%3Am1/archive").status, 200)
        self.assertEqual(self.web.get("/api/v2/messages").json()["items"], [])
        self.assertEqual(self.web.post("/api/v2/messages/read-all").status, 200)


if __name__ == "__main__":
    unittest.main()

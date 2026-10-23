"""Job alert emails: an alert from a UK student or graduate board becomes leads through the generic
parser, links that are not jobs are left alone, application news from the same senders still reaches the
application, and the person's own sender list replaces Hal's.

Every sender, employer and address here is made up (on reserved domains), and advert pages are fakes, so nothing
touches the network. The built-in sender list is checked on bare domain names, which hold no address.
"""
import contextlib
import email.message
import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer

from jobhunt import db, gmail
from jobhunt.core import settings
from jobhunt.core.settings import store
from jobhunt.find import sources_registry as registry

GOAL = {"goal.stage": "new_grad", "goal.kinds": ["graduate_job", "internship"], "goal.places": ["Fernvale"],
        "goal.roles": ["marketing"], "scoring.preset": "humanities_grad", "goal.avoid_sectors": []}
ALERT_HTML = """<html><body>
<p>New opportunities matching your alert</p>
<a href="https://www.placements.example/job/1001/summer-internship-marketing">Summer Internship - Marketing</a>
<div>Harbour and Finch Ltd</div><div>Fernvale</div>
<a href="https://www.placements.example/job/1002/graduate-scheme">Graduate Scheme: Communications</a>
<div>Kestrel Lane Studio</div><div>Hollybridge</div>
<a href="https://www.placements.example/alerts/manage">Manage your alerts</a>
<a href="https://www.placements.example/unsubscribe">Unsubscribe</a>
<a href="https://www.placements.example/">Home page</a>
</body></html>"""
ADVERT = """<html><head><title>Summer Internship - Marketing</title></head><body>
A paid summer internship in our Fernvale marketing team for ten weeks. 2,100 pounds per month. Open to
students and recent graduates. You will help plan campaigns, write social posts and report on results.
We are a small made-up consultancy with a friendly team, and we train every intern from the first day.</body></html>"""


def alert(sender: str, subject: str, html: str) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "rowan.example@example.com", subject
    msg.set_content("Open this email in a browser to see your jobs.")
    msg.add_alternative(html, subtype="html")
    return msg


class Alerts(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        settings.set_many(GOAL, by="setup")
        store.drop_cache()
        self.con = self.enterContext(contextlib.closing(db.connect()))
        registry.sync(self.con)
        settings.set("email.alert_senders", ["alerts@placements.example", "civilservice.example"], by="you")

    def test_an_internship_alert_email_becomes_a_lead(self):
        msg = alert("RateMyPlacement <alerts@placements.example>", "3 new jobs for you", ALERT_HTML)
        sender = msg["From"]
        self.assertEqual(gmail.classify(sender, msg["To"], msg["Subject"], "New opportunities"), "alert")
        read = []

        def page(url):
            read.append(url)
            return ADVERT

        with mock.patch.object(gmail, "_detail_page", side_effect=page):
            new = gmail.ingest_alert(self.con, sender, msg["Subject"], "New opportunities", "2026-10-22T08:00:00Z",
                                     links=gmail.alert_links(msg))
        self.con.commit()
        self.assertEqual(new, 2)
        rows = {r["title"]: r for r in self.con.execute("SELECT * FROM jobs WHERE source = 'alert'")}
        self.assertEqual(set(rows), {"Summer Internship - Marketing", "Graduate Scheme: Communications"})
        intern = rows["Summer Internship - Marketing"]
        self.assertEqual((intern["company"], intern["location"]), ("Harbour and Finch Ltd", "Fernvale"))
        self.assertEqual(intern["source_id"], "alerts")
        self.assertEqual(intern["kind"], "internship")
        self.assertIn("ten weeks", intern["description"])
        self.assertIsNone(intern["hidden_rule"])
        self.assertEqual(len(read), 2)
        alerts = registry.get(self.con, "alerts")
        self.assertEqual(alerts["seen"], 2)

        # the same email read again adds nothing
        with mock.patch.object(gmail, "_detail_page", side_effect=page):
            self.assertEqual(gmail.ingest_alert(self.con, sender, msg["Subject"], "", "2026-10-22T08:00:00Z",
                                                links=gmail.alert_links(msg)), 0)

    def test_links_that_are_not_jobs_are_left_alone(self):
        self.assertIsNone(gmail.lead_from_link("https://board.example/unsubscribe", "Unsubscribe", "", "A board"))
        self.assertIsNone(gmail.lead_from_link("https://board.example/", "RateMyPlacement", "", "A board"))
        self.assertIsNone(gmail.lead_from_link("mailto:hello@board.example", "Graduate scheme", "", "A board"))
        lead = gmail.lead_from_link("https://board.example/j/9", "Collections Assistant", "\n\n", "CharityJob")
        self.assertEqual(lead["company"], "CharityJob")

    def test_application_news_from_an_alert_sender_is_not_an_alert(self):
        sender = "Civil Service Jobs <noreply@civilservice.example>"
        self.assertEqual(gmail.classify(sender, "", "New jobs matching your saved search", ""), "alert")
        self.assertNotEqual(gmail.classify(sender, "", "Your application for Policy Adviser: interview invitation",
                                           "We would like to invite you to an interview."), "alert")

    def test_your_own_sender_list_replaces_hals(self):
        settings.reset("email.alert_senders", by="you")
        for domain, name in (("mail.prospects.ac.uk", "Prospects"), ("ratemyplacement.co.uk", "RateMyPlacement"),
                             ("charityjob.co.uk", "CharityJob"), ("findapprenticeship.service.gov.uk",
                                                                  "Find an apprenticeship")):
            self.assertEqual(gmail.alert_sender(domain), name)
        settings.set("email.alert_senders", ["alerts@madeupboard.example"], by="you")
        self.assertIsNone(gmail.alert_sender("mail.prospects.ac.uk"))
        self.assertEqual(gmail.alert_sender("Board <alerts@madeupboard.example>"), "alerts@madeupboard.example")


if __name__ == "__main__":
    unittest.main()

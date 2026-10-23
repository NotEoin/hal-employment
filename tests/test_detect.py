"""Adding a job site: Hal knows an application system from its address or from the employer's own
page, follows a linked feed, reads schema.org job data without the AI, and offers "Add one advert" for a page
it cannot read. Saving keeps the jobs the person ticked, with Undo in Activity.

The careers pages are the made-up ones in `tests/site/careers-*.html`; boards and feeds are fakes, so no test
touches the network.
"""
import contextlib
import json
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer
from support.jobs import advert

from jobhunt import db
from jobhunt.core import activity, runner, settings
from jobhunt.core.settings import store
from jobhunt.find import detect, source_actions
from jobhunt.sources import ats, feeds

SITE = Path(__file__).resolve().parent / "site"
WAIT_S = 10
GOAL = {"goal.stage": "new_grad", "goal.kinds": ["graduate_job", "graduate_scheme"],
        "goal.roles": ["learning officer", "designer"], "goal.places": ["Hollybridge"],
        "scoring.preset": "humanities_grad", "scoring.min_show": 1, "goal.avoid_sectors": []}
FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Fernvale Borough Council vacancies</title>
<item><title>Graduate Policy Officer</title><link>https://careers.fernvale.example/jobs/101</link>
<description>A graduate role in our policy team in Fernvale. Salary 25,400 a year.</description>
<pubDate>Tue, 20 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>Library Assistant (part time)</title><link>https://careers.fernvale.example/jobs/102</link>
<description>Part time, 18 hours a week, at Fernvale Central Library.</description></item>
</channel></rss>"""


def page(name: str) -> str:
    return (SITE / f"careers-{name}.html").read_text(encoding="utf-8")


def no_board(*args):
    raise AssertionError("no board should be read here")


def board_jobs(company: str, source: str = "workable") -> list[dict]:
    return [advert("Graduate Learning Officer", "A graduate role in Hollybridge. Graduates are welcome.",
                   id=f"{source}:{company}:1", source=source, company=company, location="Hollybridge",
                   url="https://apply.workable.example/1"),
            advert("Head of Finance", "Ten years of experience leading a finance team. Senior role.",
                   id=f"{source}:{company}:2", source=source, company=company, location="Hollybridge",
                   url="https://apply.workable.example/2"),
            advert("Gallery Designer", "An entry level designer role in Hollybridge for a recent graduate.",
                   id=f"{source}:{company}:3", source=source, company=company, location="Hollybridge",
                   url="https://apply.workable.example/3")]


class Detection(unittest.TestCase):
    def test_known_application_systems_are_known_from_their_address(self):
        cases = {"https://apply.workable.com/harbour-arts-trust/": ("workable", "harbour-arts-trust"),
                 "https://boards.greenhouse.io/harbourartstrust/jobs/123": ("greenhouse", "harbourartstrust"),
                 "https://job-boards.eu.greenhouse.io/kestrellane": ("greenhouse", "kestrellane"),
                 "https://jobs.lever.co/saltmarsh": ("lever", "saltmarsh"),
                 "https://jobs.eu.lever.co/saltmarsh/abc": ("lever_eu", "saltmarsh"),
                 "https://jobs.ashbyhq.com/fernvale-labs": ("ashby", "fernvale-labs"),
                 "https://fernvale.recruitee.com/o/graduate": ("recruitee", "fernvale"),
                 "https://kestrel.jobs.personio.de/": ("personio", "kestrel"),
                 "https://madeup.wd3.myworkdayjobs.com/en-GB/careers": ("workday", "madeup"),
                 "https://kestrel.teamtailor.com/jobs": ("teamtailor", "kestrel")}
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect.ats_from_url(url), expected)
        self.assertIsNone(detect.ats_from_url("https://careers.fernvale.example/"))

    def test_an_embedded_board_is_found_in_the_employers_own_page(self):
        seen = []

        def read(ats_id, slug, name):
            seen.append((ats_id, slug, name))
            return board_jobs(name, "greenhouse"), 3

        found = detect.detect("https://harbourarts.example/join-us", page("embed"), fetch_ats=read)
        self.assertEqual((found.kind, found.ats, found.slug), ("ats_board", "greenhouse", "harbourartstrust"))
        self.assertEqual(seen, [("greenhouse", "harbourartstrust", "Harbourartstrust")])
        self.assertIn("Hal can check it at every search. It has 3 jobs now.", found.words)

    def test_a_page_with_job_posting_data_needs_no_ai(self):
        with mock.patch("jobhunt.ai.chat", side_effect=AssertionError("the AI was asked")):
            found = detect.detect("https://kestrellane.example/careers", page("jobposting"), fetch_ats=no_board)
        self.assertEqual(found.kind, "careers_page")
        self.assertEqual(found.name, "Kestrel Lane Studio")
        self.assertEqual([j["title"] for j in found.jobs], ["Junior Designer", "Marketing Intern (paid, 10 weeks)"])
        first = found.jobs[0]
        self.assertEqual((first["location"], first["closes_at"]), ("Hollybridge", "2026-11-30"))
        self.assertEqual(first["salary"], "GBP 24000 - 26000 per year")
        self.assertEqual(found.words, "This is a careers page for Kestrel Lane Studio. Hal can read its jobs at "
                                      "every search. It has 2 jobs now.")

    def test_a_linked_feed_becomes_a_feed_source(self):
        asked = []

        def read(url, name):
            asked.append(url)
            return feeds.parse_feed(FEED, url, name)

        found = detect.detect("https://careers.fernvale.example/vacancies", page("feed"), fetch_ats=no_board,
                              read_feed=read)
        self.assertEqual(asked, ["https://careers.fernvale.example/vacancies/feed.xml"])
        self.assertEqual((found.kind, found.name), ("rss", "Vacancies"))
        self.assertEqual([j["title"] for j in found.jobs], ["Graduate Policy Officer", "Library Assistant (part time)"])
        self.assertEqual(found.jobs[0]["description"], "A graduate role in our policy team in Fernvale. Salary 25,400 "
                                                       "a year.")
        self.assertEqual(found.jobs[0]["posted_at"], "Tue, 20 Oct 2026 09:00:00 GMT")
        self.assertEqual(found.config(), {"url": "https://careers.fernvale.example/vacancies/feed.xml"})

    def test_a_page_hal_cannot_read_offers_one_advert_instead(self):
        found = detect.detect("https://saltmarsh.example/opportunities", page("plain"), fetch_ats=no_board)
        self.assertEqual(found.kind, "ai_page")
        self.assertFalse(found.can_watch)
        self.assertEqual(found.words, "Hal cannot read this page by itself yet. Add one advert instead.")
        workday = detect.detect("https://madeup.wd3.myworkdayjobs.com/careers", None, fetch_ats=no_board)
        self.assertFalse(workday.can_watch)
        self.assertIn("Hal cannot read Workday pages by itself yet. Add one advert instead.", workday.words)

    def test_watching_a_company_tries_its_name_on_every_system(self):
        def lever(slug, name):
            if slug == "saltmarsh-heritage":
                return board_jobs(name, "lever"), 3
            raise ConnectionError("not here")

        found = detect.probe(["Saltmarsh Heritage"], fetchers={"lever": lever, "greenhouse": lambda s, n: ([], 0)})
        hit = found["Saltmarsh Heritage"]
        self.assertEqual((hit.ats, hit.slug, hit.size), ("lever", "saltmarsh-heritage", 3))
        self.assertEqual(detect.slugs("Bloom & Wild"), ["bloomwild", "bloom-wild"])


class AddASite(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        settings.set_many(GOAL, by="setup")
        store.drop_cache()
        self.con = self.enterContext(contextlib.closing(db.connect()))

    def look(self, body: dict) -> dict:
        reply = self.c.post("/api/v2/sources/detect", body)
        self.assertEqual(reply.status, 200, reply.text)
        done = runner.wait(reply.json()["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done.get("error"))
        return done["result"]

    def test_a_pasted_workable_page_is_recognised_and_lists_its_jobs(self):
        with mock.patch.dict(ats.FETCHERS, {"workable": lambda slug, name: (board_jobs(name), 3)}), \
                mock.patch.object(source_actions, "_page", side_effect=AssertionError("no page read needed")):
            found = self.look({"url": "https://apply.workable.com/harbour-arts-trust/"})
        self.assertEqual(found["words"], "This is a Workable careers page for Harbour Arts Trust. Hal can check it "
                                         "at every search. It has 3 jobs now.")
        self.assertEqual((found["kind"], found["ats"], found["name"]), ("ats_board", "workable", "Harbour Arts Trust"))
        titles = [j["title"] for j in found["jobs"]]
        self.assertEqual(set(titles), {"Graduate Learning Officer", "Head of Finance", "Gallery Designer"})
        self.assertGreater(found["jobs"][0]["score"], found["jobs"][-1]["score"])
        best = found["jobs"][0]

        reply = self.c.post("/api/v2/sources", {"detected_id": found["detected_id"], "keep_urls": [best["url"]]})
        self.assertEqual(reply.status, 200, reply.text)
        self.assertEqual(reply.json(), {"id": "ats:workable:harbour-arts-trust", "added": 1})
        row = self.con.execute("SELECT * FROM sources WHERE id = 'ats:workable:harbour-arts-trust'").fetchone()
        self.assertEqual((row["kind"], row["added_by"], row["enabled"]), ("ats_board", "you", 1))
        self.assertEqual(json.loads(row["config"])["slug"], "harbour-arts-trust")
        kept = self.con.execute("SELECT title, source_id FROM jobs WHERE source_id = ?", (row["id"],)).fetchall()
        self.assertEqual([k["title"] for k in kept], [best["title"]])

        act = self.con.execute("SELECT id, text FROM activity WHERE kind = 'find.source_added'").fetchone()
        self.assertEqual(act["text"], "You added a job site")
        activity.undo(act["id"])
        self.assertIsNone(self.con.execute("SELECT 1 FROM sources WHERE id = ?", (row["id"],)).fetchone())

    def test_a_look_that_ran_out_asks_to_look_again(self):
        reply = self.c.post("/api/v2/sources", {"detected_id": "made-up", "keep_urls": []})
        self.assertEqual(reply.status, 409)
        self.assertEqual(reply.json()["error"]["message"], "Hal no longer has that look at the site. Press Look again.")

    def test_a_company_with_no_board_found_asks_for_its_careers_page(self):
        with mock.patch.object(detect, "probe", return_value={}):
            found = self.look({"company": "Saltmarsh Heritage"})
        self.assertFalse(found["can_watch"])
        self.assertTrue(found["ask_for_page"])
        self.assertEqual(found["words"], "Hal could not find a job board for Saltmarsh Heritage. Paste the address "
                                         "of its careers page instead.")


if __name__ == "__main__":
    unittest.main()

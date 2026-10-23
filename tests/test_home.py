"""Home: what needs you, most urgent first, from every area; the rail's counts; what Hal did today,
which Activity keeps whole across a restart.
"""
import types
import unittest
from unittest import mock

from support.api import HalServer
from support.world import World, job_id

from jobhunt.core import activity, clock, home_routes, plugins, runner
from jobhunt.mail import home as mail_home


def area(name: str, needs=None, sections=None) -> types.ModuleType:
    """A made-up area's home.py."""
    mod = types.ModuleType(name)
    if needs is not None:
        mod.needs = lambda con: needs
    if sections is not None:
        mod.sections = lambda con: sections
    return mod


def broken(con):
    raise RuntimeError("a made-up area failed")


class Home(unittest.TestCase):
    def setUp(self):
        self.w = self.enterContext(World())
        self.con = self.w.con

    def test_home_lists_what_needs_you_most_urgent_first(self):
        self.con.execute("UPDATE applications SET docs_need_redraft = 1 WHERE job_id = ?", (job_id("sorrel-ready"),))
        self.con.commit()
        apps = area("jobhunt.apply.home", needs=[
            {"id": "app:1", "icon": "file", "tone": "info", "title": "Check and approve your CV and letter",
             "detail": "Made-up Trust.", "due": "Closes Tue", "act": {"label": "Check them", "route": "app-1"},
             "urgency": 12}])
        failing = area("jobhunt.learn.home")
        failing.needs = broken
        with mock.patch.object(plugins, "modules", return_value=[apps, failing, mail_home]), \
                self.assertLogs("jobhunt.core.home_routes", "ERROR"):
            needs = home_routes.home(self.con)["needs"]
        titles = [n["title"] for n in needs]
        self.assertEqual(titles[0], "Check and approve your CV and letter")
        self.assertEqual(titles[1], "Book the interview with Cobalt Ledger")
        self.assertLess(titles.index("Interview email: Interview invitation: Graduate Engineer"),
                        titles.index("Documents for Software Engineer, Graduate need making again"))
        self.assertEqual(titles[-1], "Update the portfolio page")
        urgencies = [n["urgency"] for n in needs]
        self.assertEqual(urgencies, sorted(urgencies))

    def test_sections_from_areas_and_the_rail_counts(self):
        job = {"id": job_id("fernhill-grad"), "title": "Graduate Software Engineer"}
        find = area("jobhunt.find.home", sections={"new_matches": [job], "counts": {"memory": 2}})
        with mock.patch.object(plugins, "modules", return_value=[find]):
            page = home_routes.home(self.con)
        self.assertEqual(page["new_matches"], [job])
        self.assertEqual(page["counts"]["jobs"], 1)
        self.assertEqual(page["counts"]["memory"], 2)
        self.assertEqual(page["counts"]["inbox"], 5)
        self.assertTrue(page["summary"].endswith("Hal found 1 new match since yesterday."))
        self.assertEqual([r[0] for r in page["goal"]["rows"]][:2], ["Stage", "Looking for"])
        self.assertEqual([p["stage"] for p in page["week"]["pipeline"]],
                         ["Getting ready", "Form filled", "Sent", "Interview", "Offer", "Closed"])

    def test_this_weeks_new_matches_count_only_jobs_matches_and_saved_show(self):
        # Windows 11 install test (2026-10-23): Home said "255 new matches" while Matches showed none, as the goal
        # hid every job the first search found
        from jobhunt import stats
        from jobhunt.find import routes as find_routes

        self.con.execute("UPDATE jobs SET first_seen = ?", (clock.iso(),))
        self.con.commit()
        shown = sum(self.con.execute(f"SELECT count(*) FROM jobs j WHERE {w}", a).fetchone()[0]
                    for w, a in (find_routes._shown_where("matches"), find_routes._shown_where("saved")))
        self.assertGreater(shown, 0)
        self.assertEqual(stats.week(self.con)["matches"], shown)
        self.con.execute("UPDATE jobs SET hidden_rule = 'place'")
        self.con.commit()
        self.assertEqual(stats.week(self.con)["matches"], 0)

    def test_activity_shows_the_whole_day_after_a_restart(self):
        for n in range(6):
            activity.record("find", "find.discover", f"Searched made-up site {n}", "Scheduled search at 07:30")
        runner.recover()
        page = activity.page(self.con)
        self.assertEqual(page["days"][0]["day"], "Today")
        searched = [i["text"] for i in page["days"][0]["items"] if i["text"].startswith("Searched made-up")]
        self.assertEqual(len(searched), 6)
        today = home_routes.home(self.con)["today"]
        self.assertEqual([i["text"] for i in today], [f"Searched made-up site {n}" for n in (5, 4, 3, 2)])


class Route(unittest.TestCase):
    def test_the_home_route_answers_in_the_api_shape(self):
        with HalServer() as hal:
            page = hal.signed_in().get("/api/v2/home").json()
        for key in ("date_words", "greeting", "summary", "needs", "new_matches", "week", "goal", "today", "counts"):
            self.assertIn(key, page)
        self.assertEqual(page["summary"], "Nothing needs you right now.")


if __name__ == "__main__":
    unittest.main()

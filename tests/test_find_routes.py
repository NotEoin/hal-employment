"""The Jobs routes: Matches, Hidden grouped by reason with a fix, one job's points, Save
with its closing reminder, "Not for me", requirements that do not matter, adding one advert and goal versions.

Each test runs a real server in a temporary home with made-up adverts scored by the real rules. Every
employer, place and advert here is invented.
"""
import os
import unittest
from unittest import mock
from urllib.parse import quote

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer
from support.jobs import advert

from jobhunt import db
from jobhunt.core import clock, runner, settings
from jobhunt.core.settings import store
from jobhunt.find import goal as goal_mod
from jobhunt.find import job_actions, rules

NOW = "2026-10-22T09:00:00Z"
GRADUATE = {"goal.stage": "new_grad", "goal.study_status": "finished", "goal.study_end": "2026",
            "goal.kinds": ["graduate_job", "graduate_scheme"], "goal.places": ["Northby"],
            "goal.min_pay": 20000, "goal.pattern": "any", "goal.avoid_sectors": [], "scoring.preset": "humanities_grad",
            "scoring.min_show": 30}
GRAD_TEXT = "A graduate role in our Northby office. Graduates are welcome. Salary \u00a325,000 a year."
INTERN_TEXT = "A paid summer internship in Northby. \u00a32,000 per month. Open to recent graduates."
UNPAID_TEXT = "A graduate volunteer role in Northby. This is an unpaid role; expenses only."
STUDENTS_TEXT = ("Our research graduate programme in Northby is for current students only: you must be "
                 "returning to university to finish your degree afterwards. Salary \u00a321,000 a year.")
ADVERT_PAGE = """<html><head><title>Junior Archivist</title>
<script type="application/ld+json">{"@type": "JobPosting", "title": "Junior Archivist",
 "hiringOrganization": {"name": "Wellbrook Records Trust"},
 "jobLocation": {"address": {"addressLocality": "Northby"}},
 "description": "<p>A graduate role caring for our Northby collections. Salary 24,000 per year.</p>",
 "validThrough": "2026-11-20"}</script></head><body>Junior Archivist</body></html>"""
WAIT_S = 10


def add(con, key: str, title: str, text: str, company: str = "Lanternfield Ltd", **cols) -> str:
    """Adds one made-up advert, scores it under the live goal and returns its id."""
    job = advert(title, text, id=f"test:{key}", company=company, location="Northby",
                 url=f"https://jobs.example.org/{key}", posted_at=None)
    db.upsert(con, job)
    for col, value in cols.items():
        con.execute(f"UPDATE jobs SET {col} = ? WHERE id = ?", (value, job["id"]))
    rules.score_and_save(con, dict(con.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()))
    con.commit()
    return job["id"]


class JobsApi(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {clock.ENV_FAKE_NOW: NOW}))
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        settings.set_many(GRADUATE, by="setup")
        store.drop_cache()
        self.con = db.connect()
        self.addCleanup(self.con.close)
        self.grad = add(self.con, "grad", "Graduate Research Assistant", GRAD_TEXT)
        self.intern = add(self.con, "intern", "Summer Intern, Research", INTERN_TEXT)
        self.unpaid = add(self.con, "unpaid", "Graduate Research Volunteer", UNPAID_TEXT)
        self.scheme = add(self.con, "scheme", "Research Graduate Programme", STUDENTS_TEXT)

    def path(self, job_id: str, rest: str = "") -> str:
        return f"/api/v2/jobs/{quote(job_id, safe='')}{rest}"

    def get(self, path: str):
        reply = self.c.get(path)
        self.assertEqual(reply.status, 200, reply.text)
        return reply.json()

    def post(self, path: str, body=None):
        reply = self.c.post(path, body)
        self.assertEqual(reply.status, 200, reply.text)
        return reply.json()

    def matches(self, query: str = "") -> list[str]:
        return [j["id"] for j in self.get("/api/v2/jobs" + query)["jobs"]]

    def test_hidden_jobs_are_grouped_by_reason_with_a_way_to_change_it(self):
        self.assertEqual(self.matches(), [self.grad])
        groups = {g["rule"]: g for g in self.get("/api/v2/jobs/hidden")["groups"]}

        intern = groups["kind_not_chosen:internship"]
        self.assertEqual(intern["reason"], "Paid internships: not a kind of role you chose")
        self.assertEqual((intern["n"], intern["examples"]), (1, ["Summer Intern, Research, Northby"]))
        self.assertEqual(intern["fix"]["label"], "Add paid internships to your goal")

        unpaid = groups["unpaid"]
        self.assertEqual((unpaid["reason"], unpaid["fix"]["label"]), ("Unpaid or expenses only", "Show unpaid roles"))
        self.assertEqual(unpaid["fix"]["set"], {"key": "goal.unpaid_ok", "value": True})

        students = groups["students_only"]
        self.assertEqual(students["reason"], "For current students only")
        self.assertEqual(students["why"], "For current students only. You finished your course in 2026.")
        self.assertEqual(students["fix"], {"label": "Check when your course ends", "route": "settings-goal"})

        shown = self.get(f"/api/v2/jobs/hidden/{quote('kind_not_chosen:internship', safe='')}")["jobs"]
        self.assertEqual([(j["id"], j["why"]) for j in shown],
                         [(self.intern, "Paid internships: not a kind of role you chose")])

        # the fix changes the goal in one press; a re-check then shows the internship
        fix = intern["fix"]["set"]
        self.assertEqual(self.c.request("PUT", f"/api/v2/settings/{fix['key']}", {"value": fix["value"]}).status, 200)
        done = runner.wait(self.post("/api/v2/jobs/rescore")["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done)
        self.assertIn(self.intern, self.matches())
        self.assertEqual(self.get(self.path(self.intern))["job"]["kind_label"], "Paid internships")

    def test_not_for_me_keeps_the_reason_and_hides_the_job(self):
        reply = self.post(self.path(self.grad, "/not-for-me"), {"reason": "Too far"})
        self.assertEqual(reply, {"hidden": True, "suggestion_id": None})
        self.assertNotIn(self.grad, self.matches())
        yours = self.get("/api/v2/jobs/hidden")["yours"]
        self.assertEqual(yours, [{"id": self.grad, "title": "Graduate Research Assistant", "org": "Lanternfield Ltd",
                                  "why": "You chose: Too far"}])
        row = self.con.execute("SELECT verdict, reason FROM job_feedback WHERE job_id = ?", (self.grad,)).fetchone()
        self.assertEqual(tuple(row), ("no", "too_far"))
        self.assertEqual(self.get(self.path(self.grad))["hidden"]["label"], "You chose: Too far")

        # a re-check never brings it back by itself, and the reason survives putting it back
        runner.wait(self.post("/api/v2/jobs/rescore")["action_id"], WAIT_S)
        self.assertNotIn(self.grad, self.matches())
        self.post(self.path(self.grad, "/put-back"))
        self.assertIn(self.grad, self.matches())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM job_feedback WHERE job_id = ? AND reason = 'too_far'",
                                          (self.grad,)).fetchone()[0], 1)

    def test_own_words_are_kept_as_the_reason(self):
        self.post(self.path(self.grad, "/not-for-me"), {"reason": "The team is too small for me"})
        self.assertEqual(self.get("/api/v2/jobs/hidden")["yours"][0]["why"], "You chose: The team is too small for me")

    def test_the_job_page_lists_each_point_and_the_total(self):
        page = self.get(self.path(self.grad))
        points = page["points"]
        self.assertEqual(points["base"], rules.current_weights()["base"])
        self.assertTrue(points["items"])
        self.assertTrue(all(p["points"] for p in points["items"]))
        self.assertEqual(points["total"], max(1, min(100, points["base"] + sum(p["points"] for p in points["items"]))))
        self.assertEqual(points["total"], page["job"]["score"])
        self.assertIn("Graduates are welcome", page["job"]["advert"])
        self.assertEqual(page["job"]["pay"], "\u00a325,000")

    def test_the_job_page_says_why_a_hidden_job_is_hidden(self):
        page = self.get(self.path(self.scheme))
        self.assertEqual(page["hidden"]["label"], "For current students only")
        self.assertEqual(page["eligibility"]["line"], "For current students only. You finished your course in 2026.")
        self.assertEqual(page["job"]["score"], 0)

    def test_does_not_matter_changes_only_this_jobs_score(self):
        other = add(self.con, "grad2", "Graduate Policy Assistant", GRAD_TEXT)
        for job_id in (self.grad, other):
            self.con.execute("""INSERT INTO job_requirements (job_id, n, text, essential, state, quote, mine, tip,
                                updated) VALUES (?, 1, 'Experience with Excel', 1, 'miss', 'Confident with Excel.',
                                '', 'Add an example to your CV.', ?)""", (job_id, NOW))
        self.con.commit()
        before = {j: self.get(self.path(j))["job"]["score"] for j in (self.grad, other)}
        page = self.get(self.path(self.grad))
        self.assertIn(["requirement:1", job_actions.REQUIREMENT_POINTS["miss"]],
                      [[p["rule"], p["points"]] for p in page["points"]["items"]])

        reply = self.post(self.path(self.grad, "/requirements/1/off"), {"off": True})
        self.assertEqual(reply, {"state": "off", "score": before[self.grad] - job_actions.REQUIREMENT_POINTS["miss"]})
        self.assertEqual(self.get(self.path(self.grad))["job"]["score"], reply["score"])
        self.assertEqual(self.get(self.path(other))["job"]["score"], before[other])
        stored = self.con.execute("SELECT score FROM jobs WHERE id = ?", (self.grad,)).fetchone()[0]
        self.assertEqual(round(stored), before[self.grad] - job_actions.REQUIREMENT_POINTS["miss"])

        back = self.post(self.path(self.grad, "/requirements/1/off"), {"off": False})
        self.assertEqual(back, {"state": "miss", "score": before[self.grad]})

    def test_the_kind_filter_appears_when_the_goal_has_several_kinds(self):
        self.assertEqual([k["id"] for k in self.get("/api/v2/jobs")["filters"]["kinds"]],
                         ["graduate_job", "graduate_scheme"])
        settings.set("goal.kinds", ["graduate_job", "fixed_term"], by="you")
        store.drop_cache()
        self.assertEqual(self.get("/api/v2/jobs")["filters"]["kinds"], [])
        settings.set("goal.kinds", ["graduate_job", "internship"], by="you")
        runner.wait(self.post("/api/v2/jobs/rescore")["action_id"], WAIT_S)
        kinds = {k["id"]: k for k in self.get("/api/v2/jobs")["filters"]["kinds"]}
        self.assertEqual((kinds["internship"]["label"], kinds["internship"]["n"]), ("Paid internships", 1))
        self.assertEqual(self.matches("?kind=internship"), [self.intern])
        self.assertEqual(self.matches("?kind=graduate_job"), [self.grad])

    def test_a_saved_job_gets_a_reminder_two_days_before_it_closes(self):
        self.con.execute("UPDATE jobs SET closes_at = '2026-10-30' WHERE id = ?", (self.grad,))
        self.con.commit()
        self.assertEqual(self.post(self.path(self.grad, "/save"), {"saved": True}),
                         {"saved": True, "remind_on": "2026-10-28"})
        self.assertEqual(self.matches(), [])
        self.assertEqual(self.matches("?tab=saved"), [self.grad])
        todo = self.con.execute("SELECT title, due, done FROM todos WHERE job_id = ?", (self.grad,)).fetchone()
        self.assertEqual((todo["due"], todo["done"]), ("2026-10-28", None))
        self.assertIn("Graduate Research Assistant", todo["title"])

        self.post(self.path(self.grad, "/save"), {"saved": False})
        self.assertIsNotNone(self.con.execute("SELECT done FROM todos WHERE job_id = ?", (self.grad,)).fetchone()[0])
        self.assertEqual(self.matches(), [self.grad])

    def test_sorting_and_place_filters(self):
        cheap = add(self.con, "cheap", "Graduate Archive Assistant", GRAD_TEXT.replace("25,000", "22,000"),
                    closes_at="2026-10-25")
        self.assertEqual(self.matches("?sort=pay"), [self.grad, cheap])
        self.assertEqual(self.matches("?sort=closing")[0], cheap)
        self.assertEqual(sorted(self.matches("?place=Northby")), sorted([self.grad, cheap]))
        self.assertEqual(self.matches("?place=Elsewhere"), [])
        self.assertEqual(self.c.get("/api/v2/jobs?sort=sideways").status, 400)

    def test_an_advert_can_be_added_by_its_address(self):
        with mock.patch.object(job_actions, "_fetch", return_value=ADVERT_PAGE):
            started = self.post("/api/v2/jobs/add-advert", {"url": "https://careers.example.org/archivist"})
            done = runner.wait(started["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done)
        job_id = done["result"]["job_id"]
        page = self.get(self.path(job_id))["job"]
        self.assertEqual((page["title"], page["org"], page["closes"]),
                         ("Junior Archivist", "Wellbrook Records Trust", "2026-11-20"))
        self.assertIn(job_id, self.matches())
        self.assertEqual(self.c.post("/api/v2/jobs/add-advert", {"url": "not an address"}).status, 400)

    def test_an_old_goal_version_can_be_restored(self):
        settings.set("goal.min_pay", 24000, by="you")
        settings.set("goal.min_pay", 30000, by="you")
        versions = self.get("/api/v2/goal/versions")
        self.assertGreaterEqual(len(versions), 2)
        self.assertEqual(self.get("/api/v2/goal")["values"]["goal.min_pay"], 30000)
        older = versions[1]
        self.assertEqual(set(older), {"id", "at", "by", "summary"})
        restored = self.post(f"/api/v2/goal/versions/{older['id']}/restore")
        self.assertGreater(restored["version"], versions[0]["id"])
        store.drop_cache()
        goal = self.get("/api/v2/goal")
        self.assertEqual(goal["values"]["goal.min_pay"], 24000)
        self.assertEqual(goal["version"], restored["version"])
        self.assertEqual(goal_mod.current().min_pay, 24000)
        self.assertEqual(self.c.post("/api/v2/goal/versions/999999/restore").status, 404)

    def test_a_job_that_is_not_there_is_a_plain_not_found(self):
        reply = self.c.get(self.path("test:missing"))
        self.assertEqual(reply.status, 404)
        self.assertEqual(reply.json()["error"]["message"], "That job is not in Hal's list.")

    def test_search_finds_jobs_and_the_add_advert_action(self):
        found = self.get("/api/v2/search?q=Graduate%20Research")
        self.assertIn({"label": "Graduate Research Assistant, Lanternfield Ltd", "kind": "Job",
                       "route": "job-" + quote(self.grad, safe="")}, found)
        self.assertIn({"label": "Add one advert", "kind": "Action", "action": "dialog:addjob"},
                      self.get("/api/v2/search?q=advert"))


if __name__ == "__main__":
    unittest.main()

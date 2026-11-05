"""The setup guide's goal routes: career-area starters, the quick questions ending in "Here is
what I understood", and saving it as one change of the goal.

The people here are made up: a history graduate who wants museum work, and a student after internships.
"""
import contextlib
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer

from jobhunt import db
from jobhunt.core import settings
from jobhunt.core.settings import store


class GoalSteps(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        store.drop_cache()

    def post(self, path: str, body: dict, status: int = 200) -> dict:
        reply = self.c.post(path, body)
        self.assertEqual(reply.status, status, reply.text)
        return reply.json()

    def test_starters_cover_every_career_area(self):
        reply = self.c.get("/api/v2/goal/starters")
        self.assertEqual(reply.status, 200, reply.text)
        areas = {a["id"]: a for a in reply.json()}
        self.assertEqual(set(areas), {"technology", "policy_and_research", "heritage_and_museums",
                                      "charities_and_campaigning", "education", "health_and_care",
                                      "finance_and_business", "creative_and_media"})
        self.assertEqual(areas["heritage_and_museums"]["name"], "Heritage and museums")
        self.assertIn("collections assistant", areas["heritage_and_museums"]["roles"])

    def test_the_quiz_ends_with_what_hal_understood(self):
        before = settings.get_many("goal.")
        found = self.post("/api/v2/goal/quiz", {"answers": {
            "stage": "new_grad", "starter": "heritage_and_museums", "roles": "Research, archives, museums",
            "avoid": "sales", "places": "Hollybridge, Fernvale and Saltmarsh", "pattern": "A mix",
            "min_pay": "24k", "study_status": "finished", "study_end": "2026"}})
        u = found["understood"]
        self.assertEqual(u["roles"], ["Research", "archives", "museums"])
        self.assertEqual(u["avoid"], ["sales"])
        self.assertEqual(u["places"], ["Hollybridge", "Fernvale", "Saltmarsh"])
        self.assertEqual((u["pattern"], u["min_pay"], u["stage"]), ("hybrid", 24000, "new_grad"))
        self.assertEqual(u["interests"], ["museums", "archives", "local history", "conservation", "galleries"])
        self.assertEqual(u["preset"], "humanities_grad")
        rows = dict(map(tuple, found["rows"]))
        self.assertEqual(rows["Roles"], "Research, archives, museums")
        self.assertEqual(rows["Avoid"], "sales")
        self.assertEqual(rows["Places"], "Hollybridge first, then Fernvale and Saltmarsh")
        self.assertEqual(rows["Pattern"], "Hybrid")
        self.assertEqual(rows["Stage"], "New graduate")
        self.assertEqual(rows["Lowest pay"], "24,000 pounds a year")
        # nothing is saved until the person says it is right
        self.assertEqual(settings.get_many("goal."), before)

    def test_several_career_areas_join_their_roles_and_interests(self):
        # testers could press only one area, so heritage and creative work could not both be chosen
        u = self.post("/api/v2/goal/quiz", {"answers": {
            "stage": "new_grad", "starters": ["heritage_and_museums", "creative_and_media"]}})["understood"]
        self.assertIn("collections assistant", u["roles"])
        self.assertIn("museums", u["interests"])
        areas = {a["id"]: a for a in self.c.get("/api/v2/goal/starters").json()}
        self.assertTrue(set(areas["creative_and_media"]["roles"]) <= set(u["roles"]))
        self.assertEqual(len(u["roles"]), len(set(u["roles"])))
        self.assertTrue(set(u["avoid"]).isdisjoint(u["roles"]))
        self.assertEqual(u["preset"], "humanities_grad")

    def test_an_unknown_career_area_among_several_names_the_field(self):
        reply = self.c.post("/api/v2/goal/quiz", {"answers": {"starters": ["heritage_and_museums", "astrology"]}})
        self.assertEqual(reply.status, 400, reply.text)
        self.assertEqual(reply.json()["error"]["field"], "starters")

    def test_the_technical_preset_needs_technology_alone_or_technical_roles(self):
        # a new graduate who typed museum roles was scored with the programmers' preset
        def quiz(answers: dict) -> dict:
            return self.post("/api/v2/goal/quiz", {"answers": answers})["understood"]
        self.assertEqual(quiz({"stage": "new_grad", "starters": ["technology"]})["preset"], "technical_grad")
        self.assertEqual(quiz({"stage": "new_grad", "starters": ["technology", "education"]})["preset"],
                         "humanities_grad")
        self.assertEqual(quiz({"stage": "new_grad", "roles": "museum assistant, archivist"})["preset"],
                         "humanities_grad")
        self.assertEqual(quiz({"stage": "new_grad", "roles": "software developer, data engineer"})["preset"],
                         "technical_grad")

    def test_saving_what_hal_understood_is_one_goal_version(self):
        found = self.post("/api/v2/goal/quiz", {"answers": {
            "stage": "student", "study_status": "at_uni", "study_end": "2027-06", "roles": ["marketing"],
            "places": ["Fernvale"], "pattern": "any"}})
        u = found["understood"]
        self.assertEqual(u["kinds"], ["internship", "placement", "insight", "graduate_scheme", "part_time",
                                      "fixed_term"])
        self.assertEqual(u["preset"], "internship_seeker")
        self.assertIn(["Studying", "Yes, until 2027-06"], found["rows"])
        u["kinds"] = ["internship", "placement"]        # the person corrects one line
        with contextlib.closing(db.connect()) as con:
            versions_before = con.execute("SELECT count(*) FROM goal_versions").fetchone()[0]
        saved = self.post("/api/v2/goal/understood", {"understood": u})
        store.drop_cache()
        self.assertEqual(settings.get("goal.kinds"), ["internship", "placement"])
        self.assertEqual(settings.get("goal.stage"), "student")
        self.assertEqual(settings.get("scoring.preset"), "internship_seeker")
        self.assertEqual(settings.get("goal.study_end"), "2027-06")
        with contextlib.closing(db.connect()) as con:
            versions = con.execute("SELECT id FROM goal_versions ORDER BY id").fetchall()
        self.assertEqual(saved["version"], versions[-1]["id"])
        self.assertGreater(len(versions), versions_before)

    def test_a_quiz_answer_the_settings_would_refuse_names_its_field(self):
        reply = self.c.post("/api/v2/goal/quiz", {"answers": {"min_pay": "a lot"}})
        self.assertEqual(reply.status, 400, reply.text)
        self.assertEqual(reply.json()["error"]["field"], "min_pay")
        reply = self.c.post("/api/v2/goal/quiz", {"answers": {"kinds": ["unpaid_volunteering"]}})
        self.assertEqual(reply.status, 400, reply.text)
        self.assertEqual(reply.json()["error"]["field"], "kinds")
        reply = self.c.post("/api/v2/goal/understood", {"understood": {"salary": 1}})
        self.assertEqual(reply.status, 400, reply.text)


if __name__ == "__main__":
    unittest.main()

"""Activity: every row has a why, private answers never reach it, the page groups by day, Undo works
through its handler, and the routes answer in their documented shapes.
"""
import json
import sqlite3
import sys
import threading
import types
import unittest
from unittest import mock

from support.api import HalServer
from test_runner import WAIT_S, Kinds, fresh_home, gate

from jobhunt.core import activity, runner

CANARY = "canary-private-answer-1234"


class Rows(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())

    def test_an_activity_row_without_a_why_is_refused(self):
        with self.assertRaisesRegex(ValueError, "needs a why"):
            activity.record("find", "find.discover", "Searched 3 job sites", "  ")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO activity (at, area, kind, text, why) VALUES ('2026-10-22T09:00:00Z', "
                             "'find', 'find.discover', 'Searched', '')")
        self.con.rollback()
        new = activity.record("find", "find.discover", "Searched 3 job sites", "Scheduled search at 07:30")
        with self.assertRaisesRegex(ValueError, "needs a why"):
            activity.update(new, why="")
        with self.assertRaisesRegex(ValueError, "needs a why"):
            runner.action("test.nowhy", area="find", label="Searching", why="")

    def test_a_private_answer_is_left_out_of_every_text_field(self):
        guard = types.ModuleType("jobhunt.ai.guard")
        guard.contains_private = lambda text: CANARY in text
        self.enterContext(mock.patch.dict(sys.modules, {"jobhunt.ai.guard": guard}))
        self.enterContext(mock.patch.object(activity, "_guard_looked", True))
        self.enterContext(mock.patch.object(activity, "_guard", guard.contains_private))
        new = activity.record("applying", "apply.fill", f"Typed {CANARY} into the form", f"Because {CANARY}",
                              changed=f"Saved {CANARY}", used=[{"kind": "saved", "label": CANARY}])
        row = self.con.execute("SELECT * FROM activity WHERE id = ?", (new,)).fetchone()
        for field in ("text", "why", "changed", "used"):
            self.assertNotIn(CANARY, row[field], field)
        self.assertEqual(row["text"], activity.LEFT_OUT)

    def test_the_page_groups_by_day_newest_first_and_pages_on(self):
        days = ("2026-10-20T10:00:00Z", "2026-10-21T10:00:00Z", "2026-10-22T08:00:00Z", "2026-10-22T09:00:00Z")
        for n, at in enumerate(days):
            self.con.execute("INSERT INTO activity (at, area, kind, text, why) VALUES (?, 'email', 'mail.check', ?, ?)",
                             (at, f"Sorted {n} emails", "Email check, every 30 minutes"))
        self.con.execute("""INSERT INTO activity (at, area, kind, text, why)
                            VALUES (?, 'cv', 'cv.fresh', 'New CV', 'You asked')""", (days[0],))
        self.con.commit()
        with mock.patch.dict("os.environ", {"HAL_EMPLOYMENT_FAKE_NOW": "2026-10-22T12:00:00Z"}):
            first = activity.page(self.con, area="email", limit=3)
            self.assertEqual([d["day"] for d in first["days"]], ["Today", "Yesterday"])
            self.assertEqual([i["text"] for i in first["days"][0]["items"]], ["Sorted 3 emails", "Sorted 2 emails"])
            self.assertEqual(first["days"][0]["items"][0]["area_label"], "Email")
            rest = activity.page(self.con, area="email", before=first["more"])
            self.assertEqual([d["day"] for d in rest["days"]], ["Tuesday 20 October"])
            self.assertIsNone(rest["more"])
        with self.assertRaisesRegex(ValueError, "no Activity area"):
            activity.page(self.con, area="kitchen")

    def test_an_item_says_what_ai_was_used_and_how_long_it_took(self):
        new = activity.record("documents", "docs.prepare", "Wrote the cover letter", "You pressed Apply with Hal",
                              ai={"provider": "ollama", "model": "writer-model", "role": "writer", "calls": 2,
                                  "ms": 9000},
                              took_ms=185000, undo={"type": "cv_version", "id": 4})
        it = activity.one(self.con, new)
        self.assertEqual(it["ai_words"], "writer-model, on this computer (2 requests)")
        self.assertIn("role writer", it["ai_detail"])
        self.assertEqual(it["took"], "3 minutes")
        self.assertTrue(it["undo"])
        self.assertEqual(set(it) >= {"id", "t", "day", "area", "area_label", "icon", "tone", "text", "running", "why",
                                     "used", "ai_words", "ai_detail", "took", "changed", "undo", "remote"}, True)

    def test_undo_runs_the_handler_once_and_says_so_in_activity(self):
        undone = []

        @activity.undo_handler("test_thing")
        def put_back(con, spec, row):
            undone.append(spec["value"])
            return f"Put back {spec['value']}"
        self.addCleanup(activity.UNDO.pop, "test_thing", None)
        new = activity.record("settings", "setting.changed", "Changed the distance", "You changed this setting.",
                              undo={"type": "test_thing", "value": "20 miles"})
        said = activity.undo(new)
        self.assertEqual(undone, ["20 miles"])
        rows = {r["id"]: r for r in self.con.execute("SELECT * FROM activity").fetchall()}
        self.assertTrue(rows[new]["undone"])
        self.assertEqual((rows[said]["text"], rows[said]["why"]), ("Put back 20 miles", activity.UNDO_WHY))
        with self.assertRaisesRegex(RuntimeError, "undone already"):
            activity.undo(new)
        plain = activity.record("find", "find.discover", "Searched", "Scheduled search at 07:30")
        with self.assertRaisesRegex(RuntimeError, "cannot be undone"):
            activity.undo(plain)


class Routes(unittest.TestCase):
    """The Activity and actions routes through a real server."""

    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.web = self.hal.signed_in()
        self.kinds = Kinds(self)

    def test_the_activity_page_and_one_item_answer_in_the_api_shapes(self):
        new = activity.record("find", "find.discover", "Searched 9 job sites", "You pressed Search now",
                              used=[{"kind": "source", "label": "Made-up Jobs Board", "ref": "s1"}])
        page = self.web.get("/api/v2/activity?limit=10").json()
        self.assertEqual(page["days"][0]["items"][0]["id"], new)
        one = self.web.get(f"/api/v2/activity/{new}").json()
        self.assertEqual((one["why"], one["used"]), ("You pressed Search now",
                                                     [{"kind": "source", "label": "Made-up Jobs Board"}]))
        self.assertEqual(self.web.get("/api/v2/activity/99999").status, 404)
        self.assertEqual(self.web.get("/api/v2/activity?limit=ten").status, 400)
        self.assertEqual(self.web.post(f"/api/v2/activity/{new}/undo").status, 409)
        self.assertEqual(self.hal.client().get("/api/v2/activity").status, 401)

    def test_an_action_is_listed_stopped_and_carried_on_through_the_routes(self):
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.kinds.add("test.route", lambda ctx: gate(ctx, reached, release), area="email", label="Checking email",
                       why="Email check, every 30 minutes")
        action_id = runner.start("test.route", subject="_mail")
        self.assertTrue(reached.wait(WAIT_S))
        listed = self.web.get("/api/v2/actions").json()
        self.assertEqual([a["id"] for a in listed], [action_id])
        one = self.web.get(f"/api/v2/actions/{action_id}").json()
        self.assertEqual((one["state"], one["steps"][0]["text"]), ("running", "Reading the advert"))
        self.assertEqual(self.web.post(f"/api/v2/actions/{action_id}/stop").json(), {"ok": True})
        release.set()
        self.assertEqual(runner.wait(action_id)["state"], "stopped")
        self.assertEqual(self.web.get("/api/v2/actions?state=stopped").json()[0]["id"], action_id)
        self.assertEqual(self.web.get("/api/v2/actions?state=asleep").status, 409)
        again = self.web.post(f"/api/v2/actions/{action_id}/carry-on").json()["action_id"]
        self.assertEqual(runner.wait(again)["state"], "done")
        self.assertEqual(self.web.post(f"/api/v2/actions/{again}/carry-on").status, 409)
        self.assertEqual(self.web.get("/api/v2/actions/nothing-here").status, 404)
        self.assertEqual(self.web.post("/api/v2/actions/nothing-here/stop").status, 404)
        rows = self.web.get("/api/v2/activity?area=email").json()["days"][0]["items"]
        self.assertEqual([r["action_id"] for r in rows], [again, action_id])
        self.assertTrue(all(r["why"] == "Email check, every 30 minutes" for r in rows))
        self.assertEqual(json.loads(json.dumps(rows))[1]["tone"], "warn")


if __name__ == "__main__":
    unittest.main()

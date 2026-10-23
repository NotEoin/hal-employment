"""How Hal learns: patterns become suggestions that wait for a yes, and everything learned says where
it came from, can be switched off, deleted and undone.

Each test runs a real server in a temporary home. The people, employers and adverts are made up; the towns are
real UK towns so that distances have something to measure.
"""
import os
import unittest
from contextlib import closing
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer
from support.jobs import advert

from jobhunt import db
from jobhunt.core import activity, events, plugins, runner, settings
from jobhunt.core.settings import store
from jobhunt.find import feedback
from jobhunt.learn import actions, home, items, patterns, places

NOW = "2026-10-22T09:00:00Z"
LATER_THIS_WEEK = "2026-10-24T09:00:00Z"
NEXT_WEEK = "2026-10-29T10:00:00Z"
GOAL = {"you.city": "Leeds", "goal.max_distance": 35, "goal.roles": ["research", "policy"],
        "goal.places": ["Leeds"]}
# Too far from Leeds: Selby is about 20 miles, Barnsley 18, Rotherham 27, Sheffield 29.
FAR = [("Researcher", "Barnsley"), ("Policy Officer", "Rotherham, South Yorkshire"), ("Archivist", "Sheffield"),
       ("Assistant", "Selby")]
TEXT = "A made-up role for a recent graduate. Salary 24,000 a year."


def add_job(con, key: str, title: str, location: str = "Leeds", company: str = "Lanternfield Trust") -> str:
    job = advert(title, TEXT, id=f"test:{key}", company=company, location=location,
                 url=f"https://jobs.example.org/{key}")
    db.upsert(con, job)
    con.commit()
    return job["id"]


def hide_too_far(con, jobs=FAR) -> list[str]:
    ids = []
    for title, place in jobs:
        job_id = add_job(con, "far-" + place.split(",")[0].lower(), title, place)
        feedback.not_for_me(con, job_id, "Too far")
        ids.append(job_id)
    return ids


def save_heritage(con, n: int = 5) -> list[str]:
    ids = []
    for i in range(n):
        job_id = add_job(con, f"heritage{i}", f"Heritage Officer {i}", company=f"Fernvale Heritage Trust {i}")
        feedback.save(con, job_id, True)
        ids.append(job_id)
    return ids


def keep_line_twice(con, target: str = "entries.1.bullets.0") -> None:
    for n, day in enumerate(("2026-10-19T10:00:00Z", "2026-10-21T10:00:00Z")):
        con.execute("""INSERT INTO cv_suggestions (id, doc_id, based_on, target, op, kind, title, why, trigger,
                                                    evidence, before, afters, status, reason, fingerprint, created,
                                                    decided)
                       VALUES (?, 'main', 'v1', ?, 'replace', 'Wording', 'Shorter line', 'A made-up why', '{}',
                               '[]', ?, '[]', 'left', 'Keep my wording', ?, ?, ?)""",
                    (f"s{n}", target, "Wrote a dissertation on made-up river trade in the 1800s", f"fp{n}", day, day))
    con.commit()


def state(con) -> tuple:
    """Everything a suggestion could change: stored settings, CV preferences and site notes."""
    store.drop_cache()
    prefs = con.execute("SELECT kind, target, active FROM cv_preferences ORDER BY id").fetchall()
    return settings.changed(), [tuple(r) for r in prefs]


class Learning(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW, "HAL_EMPLOYMENT_TZ": "UTC"}))
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        settings.set_many(GOAL, by="setup")
        self.con = self.enterContext(closing(db.connect()))

    def call(self, method: str, path: str, body=None, status: int = 200):
        reply = self.c.request(method.upper(), "/api/v2/memory" + path, body)
        self.assertEqual(reply.status, status, reply.text)
        return reply.json()

    def waiting(self) -> list[dict]:
        return self.call("get", "/waiting")

    def one(self, title_part: str) -> dict:
        found = [w for w in self.waiting() if title_part in w["title"]]
        self.assertEqual(len(found), 1, self.waiting())
        return found[0]

    def test_nothing_changes_without_your_yes(self):
        hide_too_far(self.con)
        save_heritage(self.con)
        keep_line_twice(self.con)
        before = state(self.con)
        found = patterns.run(self.con)
        self.assertEqual(len(found["new"]), 3)
        self.assertEqual(state(self.con), before)
        # running again, and after "Not now", still changes nothing
        patterns.run(self.con)
        self.call("post", "/waiting/" + self.one("distance")["id"], {"decision": "later"})
        self.call("post", "/waiting/" + self.one("roles")["id"], {"decision": "never"})
        self.assertEqual(state(self.con), before)
        self.assertEqual(settings.get("goal.max_distance"), 35)

    def test_four_too_far_reasons_suggest_a_smaller_distance_with_the_jobs_as_evidence(self):
        ids = hide_too_far(self.con, FAR[:3])
        patterns.run(self.con)
        self.assertEqual(self.waiting(), [], "three is not yet a pattern")
        ids += hide_too_far(self.con, FAR[3:])
        add_job(self.con, "nowhere", "Archivist", "Hollybridge")       # a made-up town has no distance
        feedback.not_for_me(self.con, "test:nowhere", "Too far")
        patterns.run(self.con)
        s = self.one("distance")
        self.assertEqual(s["title"], "Lower your distance from 35 to 15 miles?")
        self.assertEqual(s["why"], 'You chose "Too far" for 4 jobs this week.')
        self.assertEqual(sorted(e["route"] for e in s["evidence"]), sorted("job-" + i for i in ids))
        self.assertIn("Researcher, Barnsley (18 miles)", [e["label"] for e in s["evidence"]])
        self.assertIn("Searches would look within 15 miles of Leeds instead of 35.", s["effect"])

    def test_a_too_far_job_put_back_withdraws_the_suggestion(self):
        ids = hide_too_far(self.con)
        patterns.run(self.con)
        self.assertEqual(len(self.waiting()), 1)
        feedback.put_back(self.con, ids[0])
        patterns.run(self.con)
        self.assertEqual(self.waiting(), [])

    def test_yes_changes_the_setting_and_can_be_undone(self):
        hide_too_far(self.con)
        patterns.run(self.con)
        s = self.one("distance")
        done = self.call("post", "/waiting/" + s["id"], {"decision": "yes"})
        store.drop_cache()
        self.assertEqual(settings.get("goal.max_distance"), 15)
        self.assertEqual(settings.history("goal.max_distance", 1)[0]["by"], "learned")
        self.assertEqual(self.waiting(), [])
        [item] = self.call("get", "/learned")
        self.assertEqual((item["what"], item["on"]), ("Travel up to 15 miles, not 35", True))
        reply = self.c.post(f"/api/v2/activity/{done['activity_id']}/undo")
        self.assertEqual(reply.status, 200, reply.text)
        store.drop_cache()
        self.assertEqual(settings.get("goal.max_distance"), 35)
        self.assertFalse(self.call("get", "/learned")[0]["on"])
        # an answered suggestion cannot be answered twice
        self.call("post", "/waiting/" + s["id"], {"decision": "yes"}, status=409)

    def test_never_stops_that_suggestion_for_good(self):
        hide_too_far(self.con)
        patterns.run(self.con)
        self.call("post", "/waiting/" + self.one("distance")["id"], {"decision": "never"})
        with mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": LATER_THIS_WEEK}):
            hide_too_far(self.con, [("Curator", "Harrogate")])
            patterns.run(self.con)
            self.assertEqual(self.waiting(), [])
        self.assertEqual(self.call("get", "/learned"), [])

    def test_not_now_asks_again_after_a_week(self):
        hide_too_far(self.con)
        patterns.run(self.con)
        self.call("post", "/waiting/" + self.one("distance")["id"], {"decision": "later"})
        patterns.run(self.con)
        self.assertEqual(self.waiting(), [])
        with mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": LATER_THIS_WEEK}):
            hide_too_far(self.con, [("Curator", "York")])
            patterns.run(self.con)
            self.assertEqual(self.waiting(), [], "not before the week is up")
        with mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NEXT_WEEK}):
            hide_too_far(self.con)          # the same four, hidden again: the pattern still holds
            patterns.run(self.con)
            self.assertEqual(self.one("distance")["title"], "Lower your distance from 35 to 15 miles?")

    def test_learned_items_can_be_switched_off_and_deleted(self):
        save_heritage(self.con)
        keep_line_twice(self.con)
        patterns.run(self.con)
        self.call("post", "/waiting/" + self.one("heritage")["id"], {"decision": "yes"})
        self.call("post", "/waiting/" + self.one("exactly as written")["id"], {"decision": "yes"})
        store.drop_cache()
        self.assertEqual(settings.get("goal.roles"), ["research", "policy", "heritage"])
        lock = self.con.execute("SELECT * FROM cv_preferences WHERE kind = 'lock_line'").fetchone()
        self.assertEqual((lock["target"], lock["active"]), ("entries.1.bullets.0", 1))
        learned = {i["area"]: i for i in self.call("get", "/learned")}
        role, line = learned["Finding jobs"], learned["CV"]

        self.call("put", "/learned/" + role["id"], {"on": False})
        store.drop_cache()
        self.assertEqual(settings.get("goal.roles"), ["research", "policy"])
        self.call("put", "/learned/" + role["id"], {"on": True})
        store.drop_cache()
        self.assertEqual(settings.get("goal.roles"), ["research", "policy", "heritage"])
        self.call("put", "/learned/" + line["id"], {"on": False})
        self.assertEqual(self.con.execute("SELECT active FROM cv_preferences WHERE id = ?", (lock["id"],))
                         .fetchone()[0], 0)
        self.assertFalse({i["id"]: i for i in self.call("get", "/learned")}[line["id"]]["on"])

        self.call("delete", "/learned/" + role["id"])
        store.drop_cache()
        self.assertEqual(settings.get("goal.roles"), ["research", "policy"])
        self.assertEqual([i["id"] for i in self.call("get", "/learned")], [line["id"]])
        self.call("delete", "/learned/" + role["id"], status=404)

    def test_switching_off_keeps_a_value_you_changed_by_hand(self):
        hide_too_far(self.con)
        patterns.run(self.con)
        self.call("post", "/waiting/" + self.one("distance")["id"], {"decision": "yes"})
        settings.set("goal.max_distance", 10)
        [item] = self.call("get", "/learned")
        self.assertFalse(item["on"], "the setting no longer says what Hal learned")
        self.call("put", "/learned/" + item["id"], {"on": False})
        store.drop_cache()
        self.assertEqual(settings.get("goal.max_distance"), 10)

    def test_every_learned_item_says_where_it_came_from(self):
        hide_too_far(self.con)
        save_heritage(self.con)
        keep_line_twice(self.con)
        patterns.run(self.con)
        for s in self.waiting():
            with self.subTest(s["title"]):
                self.assertTrue(s["why"] and s["effect"] and s["evidence"])
                self.call("post", "/waiting/" + s["id"], {"decision": "yes"})
        learned = self.call("get", "/learned")
        self.assertEqual(len(learned), 3)
        for item in learned:
            with self.subTest(item["what"]):
                self.assertTrue(item["from"] and item["what"])
                self.assertEqual((item["when"], item["on"]), ("Today", True))
        self.assertEqual({i["from"] for i in learned},
                         {'You chose "Too far" for 4 jobs this week', "You saved 5 heritage jobs",
                          "You turned down 2 rewrites of it"})
        rows = self.con.execute("SELECT why FROM activity WHERE area = 'learning' AND kind = 'learn.yes'").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("Hal suggested it because:" in r["why"] for r in rows))

    def test_the_roles_suggestion_says_how_the_jobs_would_score(self):
        ids = save_heritage(self.con, 4)
        patterns.run(self.con)
        self.assertEqual(self.waiting(), [])
        applied = add_job(self.con, "heritage-sent", "Heritage Collections Assistant", company="Aire Museum Trust")
        db.set_status(self.con, applied, "applied", "sent by the person")
        self.con.commit()
        patterns.run(self.con)
        s = self.one("heritage")
        self.assertEqual(s["title"], 'Add "heritage" to the roles you want?')
        self.assertEqual(s["why"], "You saved 4 heritage jobs and applied to 1.")
        self.assertEqual(len(s["evidence"]), 5)
        # the technical preset hides jobs outside the roles you want
        self.assertEqual(s["effect"], "Heritage jobs that Hal hides today would show in your matches.")
        settings.set("scoring.preset", "humanities_grad")
        store.drop_cache()
        self.assertEqual(patterns._role_effect(self.con, "heritage", ids),
                         "Heritage jobs would score 20 points higher.")

    def test_switching_an_area_off_stops_its_suggestions(self):
        hide_too_far(self.con)
        keep_line_twice(self.con)
        settings.set("learn.enabled", ["cv"])
        store.drop_cache()
        patterns.run(self.con)
        self.assertEqual([w["area"] for w in self.waiting()], ["CV"])

    def test_home_says_how_many_suggestions_wait(self):
        hide_too_far(self.con)
        self.assertEqual(home.needs(self.con), [])
        patterns.run(self.con)
        [need] = home.needs(self.con)
        self.assertEqual(need["title"], "Hal has 1 suggestion for you")
        self.assertEqual(need["detail"], "For example: lower your distance from 35 to 15 miles. "
                                         "Nothing changes until you say yes.")
        self.assertEqual(need["act"], {"label": "Review", "route": "memory"})
        self.assertEqual(home.sections(self.con), {"counts": {"memory": 1}})
        self.assertEqual(self.c.get("/api/v2/home").json()["counts"]["memory"], 1)

    def test_the_action_finds_suggestions_and_says_why_it_ran(self):
        hide_too_far(self.con)
        why, values = actions.why_for(self.con, "feedback")
        self.assertEqual(why, 'You chose "Too far" for 4 jobs this week')
        done = runner.run(actions.KIND, subject=actions.SUBJECT, params={"trigger": "feedback", **values}, why=why)
        self.assertEqual(done["state"], "done", done)
        self.assertEqual(len(done["result"]["new"]), 1)
        row = self.con.execute("SELECT why, changed FROM activity WHERE action_id = ?", (done["id"],)).fetchone()
        self.assertEqual(row["why"], why)
        self.assertIn("Nothing changes until you say yes", row["changed"])

    def test_after_feedback_names_the_waiting_suggestion(self):
        hide_too_far(self.con, FAR[:3])
        self.assertIsNone(patterns.after_feedback(self.con))
        hide_too_far(self.con, FAR[3:])
        self.assertEqual(patterns.after_feedback(self.con), self.one("distance")["id"])

    def test_an_unknown_decision_is_refused(self):
        hide_too_far(self.con)
        patterns.run(self.con)
        self.call("post", "/waiting/" + self.one("distance")["id"], {"decision": "maybe"}, status=409)
        self.call("post", "/waiting/nothing-here", {"decision": "yes"}, status=404)


class Listening(unittest.TestCase):
    def test_choices_start_one_run_after_a_quiet_spell(self):
        clock = [0.0]
        started = []
        listener = actions.Listener(lambda trigger: started.append(trigger) or "a1", delay=20, now=lambda: clock[0])
        for _ in range(5):
            listener.see(events.Event(1, "job", {"id": "test:1"}))
            clock[0] += 5
            self.assertIsNone(listener.tick())
        clock[0] += 20
        with mock.patch.object(patterns, "enabled", return_value=True):
            self.assertEqual(listener.tick(), "a1")
        self.assertEqual(started, ["feedback"])

    def test_learnings_own_events_and_other_actions_start_nothing(self):
        own = events.Event(1, "suggestions", {"source": items.EVENT_SOURCE})
        self.assertIsNone(actions.trigger_of(own))
        self.assertEqual(actions.trigger_of(events.Event(2, "suggestions", {})), "cv")
        self.assertIsNone(actions.trigger_of(events.Event(3, "action", {"kind": "find.discover", "state": "running"})))
        self.assertEqual(actions.trigger_of(events.Event(4, "action", {"kind": "find.discover", "state": "done"})),
                         "search")
        self.assertIsNone(actions.trigger_of(events.Event(5, "action", {"kind": "cv.render_all", "state": "done"})))

    def test_a_busy_run_is_tried_again_later(self):
        clock = [0.0]
        listener = actions.Listener(lambda trigger: None, delay=1, now=lambda: clock[0])
        listener.see(events.Event(1, "job", {}))
        clock[0] = 2
        with mock.patch.object(patterns, "enabled", return_value=True):
            self.assertIsNone(listener.tick())
        self.assertEqual(listener.pending, "feedback")


class Distances(unittest.TestCase):
    def test_towns_are_found_in_job_locations(self):
        self.assertEqual(places.town_in("Rotherham, South Yorkshire"), "rotherham")
        self.assertEqual(places.town_in("Kingston upon Hull"), "hull")
        self.assertEqual(places.town_in("Chester-le-Street, Region"), "chester-le-street")
        self.assertIsNone(places.town_in("Remote (UK)"))
        self.assertIsNone(places.town_in("Hollybridge"))

    def test_distances_are_straight_line_miles(self):
        self.assertAlmostEqual(places.distance("Leeds", "York"), 22, delta=1)
        self.assertAlmostEqual(places.distance("London", "Edinburgh"), 332, delta=3)
        self.assertIsNone(places.distance("Leeds", "Remote"))
        self.assertEqual(places.display_name("stoke-on-trent"), "Stoke-on-Trent")
        self.assertEqual(places.display_name("burton upon trent"), "Burton upon Trent")


class Wording(unittest.TestCase):
    def test_when_words_are_today_yesterday_or_a_date(self):
        with mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW, "HAL_EMPLOYMENT_TZ": "UTC"}):
            self.assertEqual(items.when_words("2026-10-22T08:00:00Z"), "Today")
            self.assertEqual(items.when_words("2026-10-21T08:00:00Z"), "Yesterday")
            self.assertEqual(items.when_words("2026-10-16T08:00:00Z"), "16 Oct")

    def test_activity_finds_the_undo_for_a_yes_by_itself(self):
        plugins.modules("undo")     # what `activity.undo` loads before it looks for a handler
        self.assertIn("learned_item", activity.UNDO)


if __name__ == "__main__":
    unittest.main()

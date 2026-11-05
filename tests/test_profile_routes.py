"""The profile's API: the overview and lists, the person's changes, and the made-up replies the page tests use.

Every test runs a real server in its own temporary home. The person is the made-up humanities graduate of
support/people.py, and the files are made up.
"""
import unittest
from contextlib import closing

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import fake_api
from support.api import HalServer
from support.people import HUMANITIES

from jobhunt import db
from jobhunt.core import events
from jobhunt.evidence.routes import file_id
from jobhunt.profile import store

CV = "Sources/CVs and letters/Marnie Quillfeather CV.md"
LETTER = "Sources/CVs and letters/Museum letter.md"
TRANSCRIPT = "Sources/Education/Nowhere Fen transcript.md"
EVENT_WAIT_S = 0.2         # the events were published before the replies came back; this only drains them


def shape(value):
    """The keys of every object in `value`, all the way down; a list is the shape of its first element."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


def same_keys(test: unittest.TestCase, made_up, real, where: str = "") -> None:
    """Every object the real reply holds has the made-up reply's keys, wherever both have one."""
    if isinstance(made_up, dict) and isinstance(real, dict):
        test.assertEqual(sorted(made_up), sorted(real), where or "top")
        for key in made_up:
            same_keys(test, made_up[key], real[key], f"{where}.{key}")
    elif isinstance(made_up, list) and isinstance(real, list) and made_up and real:
        same_keys(test, made_up[0], real[0], f"{where}[0]")


class ProfileRoutes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()

    def found(self, kind: str, fields: dict, source: str = CV, doc_type: str = "cv", quote: str = "") -> int:
        with closing(db.connect()) as con:
            return store.add_found(con, kind, fields, {"source_rel": source, "quote": quote, "doc_type": doc_type,
                                                       "field": ""})

    def seed(self) -> dict:
        """The made-up humanities graduate's profile, as the ingest would leave it."""
        ids = {
            "job": self.found("job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust",
                                      "period": "Sept 2023 - present", "duties": ["Catalogued donated letters"]},
                              quote="Collections volunteer, Sept 2023 - present"),
            "education": self.found("education", {
                "qualification": "BA (Hons)", "subject": HUMANITIES.you["education.subject"],
                "institution": HUMANITIES.you["education.university"], "period": "2022-25", "grade": "2:1",
                "modules": [{"name": "Early Modern Letters", "mark": "72"}]},
                TRANSCRIPT, "transcript", "Early Modern Letters 72"),
            "skill": self.found("skill", {"name": "Archival research", "category": "research"},
                                quote="archival research in parish records"),
            "basic": self.found("basic", {"key": "name", "value": HUMANITIES.you["name"]},
                                quote=HUMANITIES.you["name"]),
        }
        self.found("job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust",
                           "start": "October 2023", "current": True}, LETTER, "letter", "since October 2023")
        ids["project"] = self.c.post("/api/v2/profile/items", {"kind": "project", "fields": {
            "name": "Dissertation: letters of a made-up mill town", "summary": "Read 300 letters in a record office.",
            "period": "2024-25"}}).json()["id"]
        return ids

    def test_the_overview_counts_each_kind_and_lists_basics_and_conflicts(self):
        self.seed()
        page = self.c.get("/api/v2/profile").json()
        self.assertEqual(page["counts"]["job"]["total"], 1)
        self.assertEqual({k["kind"]: k["n"] for k in page["kinds"]}["skill"], 1)
        self.assertEqual(page["to_check"], 4)
        self.assertEqual([b["fields"]["value"] for b in page["basics"]], [HUMANITIES.you["name"]])
        (conflict,) = page["conflicts"]
        self.assertEqual((conflict["field"], [v["words"] for v in conflict["values"]]),
                         ("start", ["September 2023", "October 2023"]))

    def test_a_kind_lists_its_items_with_sources_that_open_their_files(self):
        self.seed()
        page = self.c.get("/api/v2/profile/job").json()
        (job,) = page["items"]
        self.assertEqual([e["source_rel"] for e in job["evidence"]], [CV, LETTER])
        self.assertEqual(job["evidence"][0]["file_id"], file_id(CV))
        self.assertEqual(len(job["conflicts"]), 1)
        self.assertEqual(page["conflicts"], job["conflicts"])
        self.assertEqual(self.c.get("/api/v2/profile/pets").status, 404)

    def test_edit_confirm_hide_and_delete_round_trip(self):
        ids = self.seed()
        sub = events.subscribe()
        self.addCleanup(sub.close)
        r = self.c.request("PUT", f"/api/v2/profile/items/{ids['job']}", {"fields": {"start": "Sept 2023"}})
        self.assertEqual((r.status, r.json()["state"], r.json()["fields"]["start"]), (200, "edited", "2023-09"))
        self.assertEqual(r.json()["conflicts"], [], "the person's value settles the start date")
        self.assertEqual(self.c.post(f"/api/v2/profile/items/{ids['skill']}/confirm").json()["state"], "confirmed")
        self.assertEqual(self.c.post(f"/api/v2/profile/items/{ids['basic']}/hide").json()["state"], "hidden")
        self.assertEqual(self.c.get("/api/v2/profile").json()["basics"], [])
        self.assertEqual(self.c.request("DELETE", f"/api/v2/profile/items/{ids['skill']}").status, 403)
        self.assertEqual(self.c.request("DELETE", f"/api/v2/profile/items/{ids['project']}").status, 200)
        self.assertEqual(self.c.get("/api/v2/profile/project").json()["items"], [])
        self.assertEqual(self.c.post("/api/v2/profile/items/999/confirm").status, 404)
        seen = iter(lambda: sub.get(timeout=EVENT_WAIT_S), None)
        self.assertIn("profile", [e.type for e in seen])

    def test_a_bad_field_or_kind_is_a_plain_400(self):
        ids = self.seed()
        r = self.c.request("PUT", f"/api/v2/profile/items/{ids['job']}", {"fields": {"company": "Fenwick"}})
        self.assertEqual((r.status, r.json()["error"]["field"]), (400, "fields"))
        self.assertIn("company", r.json()["error"]["message"])
        r = self.c.post("/api/v2/profile/items", {"kind": "pet", "fields": {"name": "Biscuit"}})
        self.assertEqual((r.status, r.json()["error"]["field"]), (400, "kind"))

    def test_resolving_a_conflict_with_a_value_edits_the_item(self):
        ids = self.seed()
        (conflict,) = self.c.get("/api/v2/profile").json()["conflicts"]
        self.assertEqual(self.c.post(f"/api/v2/profile/conflicts/{conflict['id']}/resolve", {}).status, 400)
        r = self.c.post(f"/api/v2/profile/conflicts/{conflict['id']}/resolve", {"value": "October 2023"})
        self.assertEqual((r.status, r.json()["state"]), (200, "resolved"))
        job = self.c.get("/api/v2/profile/job").json()["items"][0]
        self.assertEqual((job["id"], job["state"], job["fields"]["start"]), (ids["job"], "edited", "2023-10"))
        self.assertEqual(self.c.get("/api/v2/profile").json()["conflicts"], [])

    def test_the_overview_names_each_conflicts_items_and_keeps_the_settled_ones(self):
        ids = self.seed()
        (conflict,) = self.c.get("/api/v2/profile").json()["conflicts"]
        self.assertEqual([(i["id"], i["title"]) for i in conflict["items"]], [(ids["job"], "Collections volunteer")])
        self.assertEqual(self.c.get("/api/v2/profile").json()["resolved"], [])
        self.c.post(f"/api/v2/profile/conflicts/{conflict['id']}/resolve", {"keep_all": True})
        (settled,) = self.c.get("/api/v2/profile").json()["resolved"]
        self.assertEqual((settled["id"], settled["resolution"], settled["items"][0]["id"]),
                         (conflict["id"], {"keep_all": True}, ids["job"]))

    def test_the_check_queue_asks_the_conflict_first_then_the_name_then_the_job(self):
        ids = self.seed()
        page = self.c.get("/api/v2/profile/check").json()
        self.assertEqual([c["item_ids"] for c in page["conflicts"]], [[ids["job"]]])
        self.assertEqual([c["items"][0]["id"] for c in page["cards"]], [ids["basic"], ids["job"], ids["education"]])
        self.assertEqual(page["cards"][0]["file_id"], None)
        (more,) = page["more"]
        self.assertEqual((more["items"][0]["id"], more["file_id"]), (ids["skill"], file_id(CV)),
                         "a skill one file names waits under Show more")
        self.assertEqual((page["done"], page["total"]), (0, 3), "the project was added by hand, so it is checked")

    def test_keeping_and_hiding_many_publishes_one_event_each(self):
        ids = self.seed()
        sub = events.subscribe()
        self.addCleanup(sub.close)
        r = self.c.post("/api/v2/profile/confirm-many", {"ids": [ids["skill"], ids["education"]]})
        self.assertEqual((r.status, r.json()), (200, {"ids": [ids["skill"], ids["education"]]}))
        self.assertEqual(self.c.post("/api/v2/profile/hide-many", {"ids": [ids["basic"]]}).status, 200)
        seen = [e for e in iter(lambda: sub.get(timeout=EVENT_WAIT_S), None) if e.type == "profile"]
        self.assertEqual(len(seen), 2)
        page = self.c.get("/api/v2/profile/check").json()
        self.assertEqual([c["items"][0]["id"] for c in page["cards"]], [ids["job"]])
        self.assertEqual((page["done"], page["total"]), (3, 4))

    def test_keeping_many_with_no_ids_or_an_unknown_id_changes_nothing(self):
        ids = self.seed()
        r = self.c.post("/api/v2/profile/confirm-many", {"ids": []})
        self.assertEqual((r.status, r.json()["error"]["field"]), (400, "ids"))
        self.assertEqual(self.c.post("/api/v2/profile/hide-many", {"ids": [ids["skill"], 999]}).status, 404)
        self.assertEqual(self.c.get("/api/v2/profile/skill").json()["items"][0]["state"], "hal")

    def test_the_made_up_replies_have_the_real_shapes(self):
        self.seed()
        same_keys(self, fake_api.MADE_UP_PAGES["/profile"], self.c.get("/api/v2/profile").json())
        same_keys(self, fake_api.MADE_UP_PAGES["/profile/check"], self.c.get("/api/v2/profile/check").json())
        for kind in store.KINDS:
            with self.subTest(kind):
                real = self.c.get(f"/api/v2/profile/{kind}").json()
                same_keys(self, fake_api.MADE_UP_PAGES[f"/profile/{kind}"], real)
        made_up = fake_api.MADE_UP_PAGES["/profile/education"]["items"][0]
        real = self.c.get("/api/v2/profile/education").json()["items"][0]
        self.assertEqual(sorted(made_up["fields"]), sorted(real["fields"]))
        self.assertEqual(shape(made_up["fields"]["modules"]), shape(real["fields"]["modules"]))


if __name__ == "__main__":
    unittest.main()

"""The sources registry: Hal's own sites follow the person's career, searches are built from
the goal, internship searches appear only for kinds the person chose, sites without a key are skipped with
words that say what is missed, a failing site never stops the others, and the old board list moves in.

Every employer, place, person and site address here is made up; no test touches the network.
"""
import contextlib
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import cli
from support.api import HalServer
from support.jobs import advert
from support.world import World

from jobhunt import db
from jobhunt.core import settings
from jobhunt.core.settings import secrets, store
from jobhunt.find import sources_registry as registry
from jobhunt.sources import ats

HUMANITIES = {"goal.stage": "new_grad", "goal.kinds": ["graduate_job", "graduate_scheme", "fixed_term"],
              "goal.roles": ["museum assistant", "policy officer"], "goal.places": ["Hollybridge"],
              "scoring.preset": "humanities_grad", "scoring.min_show": 1}


def goal(roles=("archivist",), kinds=("graduate_job",), stage="new_grad", study_status="finished"):
    """A stand-in for `find.goal.Goal` with only what the query builder reads."""
    return SimpleNamespace(roles=tuple(roles), kinds=frozenset(kinds), stage=stage, study_status=study_status,
                           max_distance_miles=20, is_student=lambda: study_status == "at_uni")


def source(source_id: str = "adzuna", **config) -> dict:
    return {"id": source_id, "kind": "aggregator", "name": source_id, "config": config}


class Queries(unittest.TestCase):
    def test_searches_are_built_from_the_goals_roles_and_kinds(self):
        q = registry.queries(source(), goal(roles=["archivist", "museum assistant"],
                                            kinds=["graduate_job", "graduate_scheme"]))
        # each role in turn with every chosen kind, so a long list of roles still asks for every kind
        self.assertEqual(q[:4], ["graduate archivist", "graduate scheme archivist", "graduate museum assistant",
                                 "graduate scheme museum assistant"])
        self.assertLessEqual(len(q), registry.MAX_QUERIES)
        self.assertFalse([p for p in q if "intern" in p or "placement" in p or "apprentice" in p])

    def test_internship_searches_are_added_only_for_kinds_you_chose(self):
        without = registry.queries(source(), goal(kinds=["graduate_job"]))
        self.assertFalse([p for p in without if "intern" in p])
        chosen = registry.queries(source(), goal(kinds=["graduate_job", "internship"]))
        self.assertIn("archivist internship", chosen)
        self.assertIn("graduate internship", chosen)
        placements = registry.queries(source(), goal(kinds=["placement"]))
        self.assertIn("archivist placement year", placements)
        self.assertFalse([p for p in placements if "intern" in p])

        # fixed internship words on sites that add their own follow the same rule
        self.assertEqual(registry.early_terms(goal(kinds=["graduate_job"]), {"internship": "internship"}), ())
        self.assertEqual(registry.early_terms(goal(kinds=["internship"]), {"internship": "internship"}),
                         ("internship",))

    def test_fixed_terms_are_kept_within_the_limit_of_twelve(self):
        many = goal(roles=[f"role {n}" for n in range(10)], kinds=["graduate_job", "internship"])
        q = registry.queries(source(), many, extra=("intern", "internship"))
        self.assertEqual(len(q), registry.MAX_QUERIES)
        self.assertEqual(q[-2:], ["intern", "internship"])

    def test_a_sites_own_search_words_win(self):
        self.assertEqual(registry.queries(source(queries=["heritage officer"]), goal()), ["heritage officer"])


class Registry(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        settings.set_many(HUMANITIES, by="setup")
        store.drop_cache()
        self.con = self.enterContext(contextlib.closing(db.connect()))

    def listed(self) -> dict[str, dict]:
        reply = self.c.get("/api/v2/sources")
        self.assertEqual(reply.status, 200, reply.text)
        return {s["id"]: s for s in reply.json()["sources"]}

    def test_hal_works_without_job_board_keys_and_says_what_is_missed(self):
        listed = self.listed()
        self.assertTrue(listed["reed"]["on"])
        self.assertFalse(listed["reed"]["ok"])
        self.assertEqual(listed["reed"]["missed"], "Reed has no key, so Hal skips it.")
        self.assertEqual(listed["reed"]["fix"]["label"], "Get a free key")
        self.assertEqual(len(listed["reed"]["fix"]["steps"]), 2)
        self.assertEqual(listed["alerts"]["fix"]["label"], "Set up email")
        missed = self.c.get("/api/v2/sources").json()["missed"]
        self.assertIn("Adzuna has no key, so Hal skips it.", missed)
        self.assertIn("Reed has no key, so Hal skips it.", missed)

        with self.assertRaises(registry.Skipped) as skipped:
            registry.fetch(registry.get(self.con, "reed"), goal())
        self.assertEqual(str(skipped.exception), "Reed has no key, so Hal skips it.")

        secrets.put("sites.reed_key", "made-up-reed-key")
        self.assertTrue(self.listed()["reed"]["ok"])
        self.assertNotIn("Reed has no key, so Hal skips it.", self.c.get("/api/v2/sources").json()["missed"])

    def test_a_humanities_graduate_gets_boards_for_their_field_and_no_programmer_boards(self):
        listed = self.listed()
        for on in ("adzuna", "reed", "workable-search", "jobsacuk", "guardian", "80k", "alerts"):
            self.assertTrue(listed[on]["on"], on)
        for off in ("hn", "yc", "ktp"):
            self.assertFalse(listed[off]["on"], off)
        settings.set("scoring.preset", "technical_grad", by="you")
        listed = self.listed()
        self.assertTrue(listed["hn"]["on"])
        self.assertFalse(listed["guardian"]["on"])

    def test_your_own_on_off_choice_survives_a_change_of_preset(self):
        reply = self.c.request("PUT", "/api/v2/sources/guardian", {"on": False})
        self.assertEqual(reply.status, 200, reply.text)
        self.assertFalse(reply.json()["on"])
        settings.set("scoring.preset", "career_changer", by="you")
        self.assertFalse(self.listed()["guardian"]["on"])

    def test_a_site_you_added_can_be_removed_but_hals_own_cannot(self):
        registry.add_board(self.con, "Harbour Arts Trust", "greenhouse", "harbourartstrust", added_by="you")
        self.con.commit()
        self.assertEqual(self.c.request("DELETE", "/api/v2/sources/adzuna").status, 403)
        self.assertEqual(self.c.request("DELETE", "/api/v2/sources/ats:greenhouse:harbourartstrust").status, 200)
        self.assertNotIn("ats:greenhouse:harbourartstrust", self.listed())

    def test_search_now_starts_the_scheduled_search(self):
        with mock.patch("jobhunt.core.scheduler.run_now", return_value="act-1") as run_now:
            reply = self.c.post("/api/v2/sources/search-now")
        self.assertEqual(reply.status, 200, reply.text)
        self.assertEqual(reply.json(), {"action_id": "act-1"})
        run_now.assert_called_once_with("find.discover")

    def test_one_site_searched_on_its_own_records_its_run(self):
        registry.add_board(self.con, "Harbour Arts Trust", "greenhouse", "harbourartstrust", added_by="you")
        job = advert("Graduate Learning Officer", "A graduate role in Hollybridge. Graduates welcome.",
                     id="greenhouse:harbour:1", company="Harbour Arts Trust", location="Hollybridge")
        with mock.patch.dict(ats.FETCHERS, {"greenhouse": lambda slug, name: ([dict(job)], 1)}):
            row = registry.run(self.con, registry.get(self.con, "ats:greenhouse:harbourartstrust"), goal())
        self.assertEqual((row["ok"], row["seen"]), (1, 1))
        found = self.con.execute("SELECT source_id FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        self.assertEqual(found["source_id"], "ats:greenhouse:harbourartstrust")


class Discover(unittest.TestCase):
    def test_a_failing_site_shows_its_error_and_the_others_still_run(self):
        with World() as w:
            hn_job = advert("Junior Analyst", "Entry level analyst role in Cambridge.", id="hn:made-up-1",
                            source="hn", company="Willowmere Bio", location="Cambridge", board_size=None)

            def broken():
                raise ConnectionError("the site did not answer")

            with mock.patch.dict(registry.BUILTIN_FETCHERS, {"hn": lambda: [dict(hn_job)], "ktp": broken},
                                 clear=True):
                out = cli.run(["discover", "-q"], w)
            self.assertIn("1 postings from 1 sources", out)
            self.assertIn("Knowledge Transfer Partnerships: ConnectionError the site did not answer", out)
            con = db.connect()
            try:
                ktp = registry.get(con, "ktp")
                self.assertEqual(ktp["last_error"], "ConnectionError the site did not answer")
                self.assertIsNotNone(registry.get(con, "hn")["last_ok"])
                listed = {s["id"]: s for s in registry.listing(con)}
                self.assertFalse(listed["ktp"]["ok"])
                self.assertEqual(listed["ktp"]["fix"]["label"], "Search it again")
                self.assertTrue(listed["hn"]["ok"])
                runs = {r["source_id"]: r for r in con.execute("SELECT * FROM source_runs")}
                self.assertEqual((runs["ktp"]["ok"], runs["hn"]["ok"], runs["hn"]["seen"]), (0, 1, 1))
                # a site with no key is recorded as skipped, not as failing
                self.assertIsNone(runs["reed"]["ok"])
                self.assertEqual(runs["reed"]["error"], "Reed has no key, so Hal skips it.")
                row = con.execute("SELECT text, detail FROM activity WHERE kind = 'find.source_error'").fetchone()
                self.assertEqual(row["text"], "Hal could not search Knowledge Transfer Partnerships")
            finally:
                con.close()

    def test_boards_toml_becomes_sources(self):
        with World() as w:
            w.root.joinpath("boards.toml").write_text(
                '[[board]]\nname = "Harbour Arts Trust"\nats = "greenhouse"\nslug = "harbourartstrust"\n\n'
                '[[board]]\nname = "Kestrel Lane Studio"\nats = "workable"\nslug = "kestrel-lane"\n',
                encoding="utf-8")
            w.root.joinpath("companies.txt").write_text(
                "# companies to watch\nHarbour Arts Trust\nSaltmarsh Heritage\n", encoding="utf-8")
            con = db.connect()
            try:
                registry.sync(con)
                rows = {r["id"]: dict(r) for r in con.execute("SELECT * FROM sources WHERE added_by = 'import'")}
                self.assertEqual(set(rows), {"ats:greenhouse:harbourartstrust", "ats:workable:kestrel-lane",
                                             "company:saltmarshheritage"})
                self.assertEqual(json.loads(rows["ats:workable:kestrel-lane"]["config"]),
                                 {"ats": "workable", "slug": "kestrel-lane"})
                self.assertEqual(rows["company:saltmarshheritage"]["kind"], "company_watch")
                # removing one keeps it removed: the import runs once
                registry.remove(con, "ats:workable:kestrel-lane")
                registry.sync(con)
                self.assertIsNone(con.execute("SELECT 1 FROM sources WHERE id = 'ats:workable:kestrel-lane'")
                                  .fetchone())
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()

"""Hal's base CV through the old cv.json-shaped functions, now over the CV store: edits,
rearranged skills, renames, the classic screens' saves and each application's own copy.

Moved by class from tests/test_apply.py; the people and places are made up.
"""
import json
import unittest
from unittest import mock

from support import legacy
from support.api import HalServer
from test_cv_import import lay_out
from test_cv_store import cv_home

from jobhunt import cv, documents
from jobhunt.core import paths, settings
from jobhunt.cvstudio import import_v1, model, store
from jobhunt.cvstudio.model import Change
from jobhunt.web import server

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule

SITE = {"title": "Graduate", "summary": "Builds things.", "moreProjects": "", "closing": "", "contact": {},
        "education": [{"qualification": "BSc", "meta": "Made-up University", "detail": "New dissertation wording.",
                       "modules": [{"year": "Final", "rows": [["Security", 80]]}]}],
        "projects": [{"name": "Hal", "stack": "Python", "body": "An assistant."},
                     {"name": "Hatch", "stack": "C#", "body": "A mood tracker."}],
        "experience": [], "skills": [["Languages", ["Python", "C++"]]], "additional": [["Interests", "Running"]]}


class BaseCv(unittest.TestCase):
    """Hal's base CV: imported once from the website's, then changed only by the person."""

    def setUp(self):
        self.enterContext(cv_home())
        lay_out(import_v1.folder(), site=SITE, overrides=[], snapshots=False)

    def test_an_edit_reaches_hals_cv_as_a_new_version(self):
        mine = cv.for_editing()
        mine["education"][0]["detail"] = "Old dissertation wording."
        mine["projects"][1]["hidden"] = True
        mine["skills"][0][1] = ["Python"]
        self.assertEqual(len(cv.save_base(mine)), 3)
        base = cv.load()
        self.assertEqual(base["education"][0]["detail"], "Old dissertation wording.")
        self.assertEqual(base["education"][0]["modules"], SITE["education"][0]["modules"])
        self.assertEqual([p["name"] for p in base["projects"]], ["Hal"])
        self.assertEqual(base["skills"][0][1], ["Python"])
        self.assertNotIn("_key", base["projects"][0])
        self.assertEqual(len(store.versions()), 2)

    def test_the_website_changing_later_changes_nothing(self):
        cv.load()
        (import_v1.folder() / "site-cv.json").write_text(json.dumps({**SITE, "summary": "Changed on the site."}))
        self.assertEqual(cv.load()["summary"], "Builds things.")

    def test_skill_groups_can_be_rearranged(self):
        mine = cv.for_editing()
        mine["skills"] = [["Languages", ["Python"]], ["Tools", ["Git"]]]
        cv.save_base(mine)
        self.assertEqual(cv.load()["skills"], [["Languages", ["Python"]], ["Tools", ["Git"]]])

    def test_a_renamed_entry_keeps_its_id_and_its_place(self):
        mine = cv.for_editing()
        before = store.current()[1].sections[2].entries[0].id
        mine["projects"][0]["name"] = "Hal, a local assistant"
        cv.save_base(mine)
        self.assertEqual([p["name"] for p in cv.load()["projects"]], ["Hal, a local assistant", "Hatch"])
        self.assertEqual(store.current()[1].sections[2].entries[0].id, before)

    def test_saving_with_no_change_makes_no_version(self):
        self.assertEqual(cv.save_base(cv.for_editing()), [])
        self.assertEqual(len(store.versions()), 1)

    def test_an_application_keeps_its_own_edit_of_the_cv(self):
        job = {"title": "Engineer", "company": "Acme", "location": "", "salary": "", "description": "Python"}
        self.assertEqual(documents.current_cv(job, {})["education"][0]["detail"], "New dissertation wording.")
        mine = {"cv": {**cv.load(), "summary": "For Acme."}}
        self.assertEqual(documents.current_cv(job, mine)["summary"], "For Acme.")

    def test_choosing_the_built_cv_and_going_back_are_versions(self):
        cv.load()
        cv.BUILT.parent.mkdir(parents=True, exist_ok=True)
        cv.BUILT.write_text(json.dumps({"cv": {**SITE, "title": "Built from your material"}, "flags": []}))
        cv.set_origin("built")
        self.assertEqual((cv.origin_name(), cv.load()["title"]), ("built", "Built from your material"))
        cv.set_origin("site")
        self.assertEqual((cv.origin_name(), cv.load()["title"]), ("site", "Graduate"))
        self.assertEqual(len(store.versions()), 3)

    def test_hals_changes_to_an_applications_cv_are_listed_against_the_base(self):
        base = cv.load()
        variant = json.loads(json.dumps(base))
        variant["title"] = "Graduate - security"
        variant["projects"].reverse()
        variant["projects"][0]["body"] = "A careful assistant."
        variant["skills"] = []
        found = {(c["path"], c["kind"]) for c in cv.changes(base, variant)}
        self.assertEqual(found, {("title", "text"), ("projects", "order"), ("projects.0.body", "text"),
                                 ("skills", "left out")})
        self.assertEqual(next(c for c in cv.changes(base, variant) if c["path"] == "title")["before"], "Graduate")
        self.assertEqual(cv.changes(base, base), [])


class OtherSections(unittest.TestCase):
    """Sections cv.json has no place for survive a save from the classic screens."""

    def test_a_volunteering_section_comes_back_from_the_classic_screens(self):
        self.enterContext(cv_home())
        _, doc = store.current()
        doc = model.apply(doc, Change("insert", "", {"id": "volunteering", "kind": "volunteering",
                                                     "title": "Volunteering"}, position=2))
        doc = model.apply(doc, Change("insert", "s:volunteering", {"heading": "Reading club host", "org": "Library"}))
        doc = model.apply(doc, Change("insert", "", {"id": "interests", "kind": "interests", "title": "Interests",
                                                     "text": "Fell running"}))
        store.new_version(doc, origin="manual", what="Added volunteering", by="you")
        mine = cv.for_editing()
        self.assertEqual(mine["experience"][0]["role"], "Reading club host")
        mine["experience"][0]["org"] = "Made-up Library"
        cv.save_base(mine)
        now = store.current()[1]
        self.assertEqual([s.id for s in now.sections][:3], ["summary", "education", "volunteering"])
        self.assertEqual(model.find(now, "s:volunteering").entries[0].org, "Made-up Library")
        self.assertEqual(model.find(now, "s:interests").text, "Fell running")
        self.assertEqual(model.find(now, "s:experience").entries, [])


class ClassicScreens(unittest.TestCase):
    """The classic CV screen's save, through the new server."""

    def test_classic_cv_edits_become_versions(self):
        hal = self.enterContext(HalServer())
        lay_out(import_v1.folder(), site=SITE, overrides=[], snapshots=False)
        c = hal.signed_in()
        self.enterContext(mock.patch.object(server.cv, "render"))
        self.enterContext(mock.patch.object(server, "base_cv", lambda: {"cv": cv.for_editing()}))
        self.enterContext(mock.patch.object(server, "act_rerender_cvs", lambda *a, **k: None))
        first, _ = store.current()
        mine = cv.for_editing()
        mine["summary"] = "Builds careful, tested things."
        saved = c.post("/api/cv", {"cv": mine}).json()
        self.assertEqual(saved["saved"], 1)
        versions = store.versions()
        self.assertEqual((versions[0]["n"], versions[0]["what"], versions[0]["by"], versions[0]["current"]),
                         (2, cv.CLASSIC_EDIT, "you", True))
        self.assertEqual(cv.load()["summary"], "Builds careful, tested things.")
        self.assertEqual(store.get(first).sections[0].text, "Builds things.")

    def test_a_source_added_in_the_classic_screens_is_an_action(self):
        from jobhunt import db

        c = self.enterContext(HalServer()).signed_in()
        added = c.post("/api/cv/source", {"name": "club notes", "text": "Made-up notes: ran a reading club."}).json()
        self.assertIn(added["name"], added["sources"][0]["name"] if isinstance(added["sources"][0], dict)
                      else added["sources"][0])
        con = db.connect()
        try:
            kinds = [r[0] for r in con.execute("SELECT kind FROM actions WHERE state = 'done'")]
        finally:
            con.close()
        self.assertIn("cv.source", kinds)


class StartingCv(unittest.TestCase):
    """Where Hal's main CV starts: the importer's copy, else a starter made from the settings."""

    def setUp(self):
        self.copy = paths.data_dir().joinpath(*cv.SITE_COPY)
        self.addCleanup(self.copy.unlink, missing_ok=True)

    def test_the_imported_copy_of_the_website_cv_is_where_it_starts(self):
        self.copy.parent.mkdir(parents=True, exist_ok=True)
        self.copy.write_text(json.dumps(SITE), encoding="utf-8")
        self.assertEqual(cv.site(), SITE)

    def test_without_one_the_starter_comes_from_your_settings(self):
        values = {"you.education.degree": "BA (Hons) History", "you.education.university": "Made-up University",
                  "you.city": "York", "you.links.linkedin": "https://www.linkedin.com/in/zoe-example/",
                  "goal.study_end": "2025"}
        with mock.patch.object(settings, "get", lambda key: values.get(key, "")):
            first = cv.site()
        self.assertEqual(first["education"][0]["meta"], "Made-up University | 2025")
        self.assertEqual(first["contact"], {"location": "York", "linkedin": "linkedin.com/in/zoe-example"})
        self.assertNotIn("github", first["contact"])

    def test_the_cv_facts_say_nothing_the_settings_do_not(self):
        text = cv.facts_text_of(cv.printable(SITE))
        self.assertNotIn("Graduated", text)
        self.assertNotIn("citizen", text)


if __name__ == "__main__":
    unittest.main()

"""CV types: the built-ins, copies edited as text, the Writing card, suggesting a type and comparing two.

Every CV and person here is made up (`support.people` and the CVs below, which `test_cv_checks` shares); the
AI is the fake one.
"""
from __future__ import annotations

import unittest

from support.api import HalServer
from support.fake_ai import fake_ai
from support.people import CHANGER, HUMANITIES, TECHNICAL, as_person
from support.stubs import cv_model, cv_store
from support.world import World

model = cv_model.install()
store = cv_store.install()

from jobhunt import db  # noqa: E402 - after the same-wave stubs are in place
from jobhunt.cvstudio import types as cvtypes  # noqa: E402

B, E, M, S = model.Bullet, model.Entry, model.Module, model.Section
# The fields every career needs a type for (and the owner's wish that any career is covered).
CAREERS = ("humanities_grad", "stem_grad", "technical_grad", "business_grad", "creative", "general_grad",
           "student", "career_changer", "experienced", "school_leaver", "academic")
FOR_STARTERS = ("humanities_grad", "stem_grad", "technical_grad", "business_grad", "general_grad", "student")
STARTER_WORDS = ("internship", "placement", "spring week", "insight week")


def profile_of(person) -> dict:
    """The contact block of a made-up person, as `checks.profile_now` builds it."""
    return {"name": person.you["name"], "email": person.you["email"], "phone": person.you["phone"],
            "city": person.you["city"], "links": {}}


def humanities_cv() -> model.CVDoc:
    """Marnie's CV: a history graduate with a dissertation, archive volunteering and bookshop work."""
    return model.CVDoc(sections=[
        S("summary", "summary", "Profile", text="History graduate who catalogued 400 parish letters for a village "
          "archive and wrote its visitor guide."),
        S("education", "education", "Education", entries=[E(
            "ed-1", heading="BA (Hons) History and Imaginary Studies", org="University of Nowhere Fen",
            start="Sep 2023", end="Jun 2026", grade="2:1",
            bullets=[B("b1", "Dissertation: canal trade in the 1840s, read through 60 letters in the county "
                             "record office")],
            modules=[M("Archives and Memory", "68"), M("Early Modern Europe", "66"),
                     M("Statistics for Historians", "58")])]),
        S("volunteering", "volunteering", "Volunteering", entries=[E(
            "vo-1", heading="Archive volunteer", org="Fenwick Village Archive", start="Sep 2024", end="Present",
            bullets=[B("b2", "Catalogued 400 letters so that researchers can search them online"),
                     B("b3", "Wrote a short guide to the collection that visitors now take home")])]),
        S("experience", "experience", "Work experience", entries=[E(
            "ex-1", heading="Bookseller", org="The Lantern Bookshop", start="Jun 2023", end="Present",
            bullets=[B("b4", "Chose stock for the local history table and wrote weekly recommendations"),
                     B("b5", "Served about 40 customers each Saturday and balanced the till")])]),
        S("skills", "skills", "Skills", entries=[E("sk-1", heading="Research", items=[
            "archival research", "report writing", "Excel"])]),
        S("interests", "interests", "Interests", text="Fell walking and the village choir."),
    ])


def technical_cv() -> model.CVDoc:
    """Toby's CV: a software graduate with one project and a part-time job."""
    return model.CVDoc(sections=[
        S("summary", "summary", "Profile",
          text="Software graduate who built a library book finder used by 30 students."),
        S("education", "education", "Education", entries=[E(
            "ed-1", heading="BSc (Hons) Software Studies", org="Made-up Institute of Technology", start="Sep 2023",
            end="Jul 2026", grade="First", modules=[M("Algorithms", "74"), M("Databases", "66")])]),
        S("projects", "projects", "Projects", entries=[E(
            "pr-1", heading="Shelf Finder", meta="Python, Flask, SQL",
            bullets=[B("b1", "Built a Flask app that finds library books, used by 30 students each week")])]),
        S("experience", "experience", "Experience", entries=[E(
            "ex-1", heading="Shop assistant", org="Lowmere Hardware", start="Jun 2022", end="Present",
            bullets=[B("b2", "Kept stock records in a spreadsheet for 2,000 items")])]),
        S("skills", "skills", "Skills", entries=[E("sk-1", heading="Tools", items=["Python", "SQL", "Flask"])]),
    ])


class BuiltIns(unittest.TestCase):
    def test_every_built_in_type_loads(self):
        ids = cvtypes.builtin_ids()
        self.assertEqual(sorted(ids), sorted(CAREERS))
        for type_id in ids:
            with self.subTest(type_id=type_id):
                t = cvtypes.load(type_id)
                self.assertTrue(t.builtin)
                self.assertTrue(t.name and t.fits and t.note and t.section_order)
                self.assertTrue(set(t.checks) <= set(cvtypes.CHECK_IDS))
                self.assertTrue(t.keywords["skills"])
                self.assertTrue(t.documents["hiring_titles"] and t.documents["evidence"])
                self.assertEqual(cvtypes.parse(cvtypes.dump(t), builtin=True).checks, t.checks)
                card = t.summary()
                self.assertTrue(card["order"] and card["pages"])

    def test_students_and_graduates_get_guidance_on_internships_and_placements(self):
        for type_id in FOR_STARTERS:
            with self.subTest(type_id=type_id):
                tips = " ".join(cvtypes.load(type_id).tips).lower()
                self.assertTrue(any(w in tips for w in STARTER_WORDS), tips)

    def test_only_the_technical_type_keeps_the_ktp_special_case(self):
        keeps = [t.id for t in map(cvtypes.load, cvtypes.builtin_ids()) if "ktp" in t.documents["special_cases"]]
        self.assertEqual(keeps, ["technical_grad"])


class Suggesting(unittest.TestCase):
    def test_hal_suggests_a_type_with_a_reason(self):
        cases = [(HUMANITIES, "humanities_grad", "You are a History and Imaginary Studies graduate applying to museum "
                  "assistant and archive trainee roles."),
                 (TECHNICAL, "technical_grad", "You are a Software Studies graduate applying to software developer "
                  "and data analyst roles."),
                 (CHANGER, "career_changer", "You are changing career into project coordinator roles.")]
        for person, want, why in cases:
            with self.subTest(person=person.key):
                got = cvtypes.suggest(person.goal, None, subject=person.you.get("education.subject", ""))
                self.assertEqual(got, (want, why))

    def test_a_student_is_offered_the_type_for_paid_internships_and_placements(self):
        goal = {"stage": "student", "roles": [], "kinds": ["internship", "placement"]}
        self.assertEqual(cvtypes.suggest(goal, None, subject="BSc Marine Chemistry"),
                         ("student", "You are studying Marine Chemistry and looking for paid internships and "
                                     "placement years."))

    def test_the_subject_comes_from_the_cv_when_the_profile_has_none(self):
        type_id, why = cvtypes.suggest({"stage": "new_grad", "roles": []}, humanities_cv())
        self.assertEqual(type_id, "humanities_grad")
        self.assertIn("History", why)

    def test_with_nothing_to_go_on_hal_suggests_the_any_subject_type(self):
        self.assertEqual(cvtypes.suggest(None, None)[0], "general_grad")


class Comparing(unittest.TestCase):
    def test_comparing_two_types_needs_no_ai(self):
        with fake_ai() as backend:
            a = cvtypes.outline(humanities_cv(), cvtypes.load("humanities_grad"))
            b = cvtypes.outline(humanities_cv(), cvtypes.load("career_changer"))
        self.assertEqual(backend.log, [])
        self.assertEqual([s["title"] for s in a["sections"]][:3], ["Profile", "Education", "Volunteering"])
        self.assertEqual([s["title"] for s in b["sections"]][:2], ["Profile", "Key skills"])
        self.assertEqual(b["pages"], "Up to 2 pages")

    def test_a_type_sets_order_titles_and_limits_when_shown_and_keeps_the_cv(self):
        cv = technical_cv()
        cv.sections[3].entries[0].bullets *= 5
        shown = cvtypes.arrange(cv, cvtypes.load("creative"))
        self.assertEqual([s.kind for s in shown.sections], ["summary", "experience", "education", "skills", "projects"])
        self.assertEqual(len(shown.sections[1].entries[0].bullets), 3)
        self.assertEqual(len(cv.sections[3].entries[0].bullets), 5)
        cv.sections[3].locked, cv.sections[3].title = True, "Jobs I did"
        self.assertIn("Jobs I did", [s.title for s in cvtypes.arrange(cv, cvtypes.load("creative")).sections])


class Copies(unittest.TestCase):
    def test_editing_a_built_in_type_makes_a_copy(self):
        with World():
            t, note = cvtypes.edit_writing("humanities_grad", {"tone": "formal", "never_use": ["synergy"]})
            self.assertFalse(t.builtin)
            self.assertEqual(t.name, "Humanities and social science graduate (yours)")
            self.assertIn("made a copy", note)
            self.assertEqual(t.writing["tone"], "formal")
            self.assertEqual(t.writing["banned"], ["synergy"])
            self.assertEqual(cvtypes.load("humanities_grad").writing["tone"], "plain")
            again, note = cvtypes.edit_writing(t.id, {"pages": 1})
            self.assertEqual((again.id, again.pages, note), (t.id, 1, ""))
            self.assertEqual(again.based_on, "humanities_grad")
            with self.assertRaises(PermissionError):
                cvtypes.save_source("humanities_grad", cvtypes.source("humanities_grad"))

    def test_a_copied_type_with_a_mistake_reports_its_line(self):
        with World():
            t = cvtypes.copy("humanities_grad", "My humanities CV")
            self.assertEqual(t.name, "My humanities CV")
            lines = t.source.splitlines()
            broken = "\n".join([*lines[:5], 'note = "no closing quote', *lines[6:]])
            with self.assertRaises(cvtypes.TypeSourceError) as bad:
                cvtypes.save_source(t.id, broken)
            self.assertEqual(bad.exception.line, 6)
            n = next(i for i, line in enumerate(lines, 1) if line.startswith("one_spelling"))
            unknown = "\n".join([*lines[:n - 1], "one_speling = 1", *lines[n:]])
            with self.assertRaises(cvtypes.TypeSourceError) as bad:
                cvtypes.save_source(t.id, unknown)
            self.assertEqual(bad.exception.line, n)
            self.assertIn("one_speling", str(bad.exception))
            self.assertEqual(cvtypes.load(t.id).source, t.source)


class TypeRoutes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.enterContext(as_person(HUMANITIES))
        store.new_version(humanities_cv(), origin="imported", what="Your CV, made up for a test", by="baseline")
        self.client = self.hal.signed_in()

    def type_id(self) -> str:
        con = db.connect()
        try:
            return con.execute("SELECT type_id FROM cv_documents WHERE id = 'main'").fetchone()[0]
        finally:
            con.close()

    def test_the_picker_lists_every_type_with_the_suggestion(self):
        out = self.client.get("/api/v2/cv/types").json()
        self.assertEqual(out["suggested"]["id"], "humanities_grad")
        self.assertIn("History and Imaginary Studies", out["suggested"]["why"])
        self.assertEqual(len(out["types"]), len(CAREERS))
        hum = next(t for t in out["types"] if t["id"] == "humanities_grad")
        self.assertEqual(hum["order"], "Profile, Education, Volunteering or experience, Skills")

    def test_choosing_a_type_makes_a_version(self):
        out = self.client.request("PUT", "/api/v2/cv/type", {"id": "humanities_grad"}).json()
        self.assertEqual(self.type_id(), "humanities_grad")
        con = db.connect()
        try:
            row = con.execute("SELECT origin, what FROM cv_versions WHERE id = ?", (out["version_id"],)).fetchone()
        finally:
            con.close()
        self.assertEqual(tuple(row), ("type_change", "Changed the CV type to Humanities and social science graduate"))
        self.assertEqual(self.client.request("PUT", "/api/v2/cv/type", {"id": "nothing"}).status, 404)

    def test_a_mistake_in_a_copy_answers_with_its_line(self):
        copy = self.client.post("/api/v2/cv/types/humanities_grad/copy", {"name": "My humanities CV"}).json()
        text = self.client.get(f"/api/v2/cv/types/{copy['id']}/source").json()["toml"]
        bad = self.client.request("PUT", f"/api/v2/cv/types/{copy['id']}/source", {"toml": text + "\n[checks\n"})
        self.assertEqual(bad.status, 400)
        self.assertTrue(bad.json()["error"]["message"].startswith(f"Line {len(text.splitlines()) + 2}:"))
        built_in = self.client.request("PUT", "/api/v2/cv/types/humanities_grad/source", {"toml": text})
        self.assertEqual(built_in.status, 403)

    def test_the_writing_card_on_a_built_in_switches_to_its_copy(self):
        self.client.request("PUT", "/api/v2/cv/type", {"id": "humanities_grad"})
        out = self.client.request("PUT", "/api/v2/cv/writing", {"tone": "confident"}).json()
        self.assertIn("made a copy", out["note"])
        self.assertEqual(self.type_id(), out["type"]["id"])
        self.assertEqual(self.client.get("/api/v2/cv/types").json()["writing"]["tone"], "confident")

    def test_compare_answers_two_previews_and_outlines(self):
        out = self.client.get("/api/v2/cv/types/compare?a=humanities_grad&b=career_changer").json()
        self.assertTrue(out["a_url"].endswith("preview.html?type=humanities_grad"))
        self.assertEqual(out["b"]["name"], "Career changer")


if __name__ == "__main__":
    unittest.main()

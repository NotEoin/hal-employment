"""Edit your CV: a session's changes saved as one version on the version they were made on, the 409 when the
CV changed meanwhile, lines in your own words, and record cards made into CV entries with no AI."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - the temporary home, before any jobhunt import
from support.api import HalServer
from test_cv_store import cv_home

from jobhunt import db
from jobhunt.cvstudio import edit, model, store
from jobhunt.cvstudio.model import Change, CVError
from jobhunt.profile import store as profile_store

JOB = {"title": "Ticket clerk", "employer": "Made-up Rail", "location": "Exampleton", "start": "2023-09",
       "current": True, "duties": ["Sold tickets at the busy morning window", "Counted the evening float"]}
DEGREE = {"qualification": "BSc", "subject": "Imaginary Studies", "institution": "Made-up University",
          "start": "2022-09", "end": "2025-06", "grade": "2:1",
          "modules": [{"name": "Maps of nowhere", "mark": "71"}, {"name": "Fen history", "mark": ""}]}
PROJECT = {"name": "Rota bot", "summary": "Plans a cafe rota from staff wishes.", "tools": ["Python", "SQLite"],
           "link": "github.com/made-up/rota"}
CERT = {"name": "First aid at work", "issuer": "Made-up Aid", "date": "2024-03", "grade": "Pass"}


def with_job(doc: model.CVDoc) -> model.CVDoc:
    """The document with a made-up job of two lines in its experience section."""
    sid = next(s.id for s in doc.sections if s.kind == "experience")
    return model.apply(doc, Change("insert", f"s:{sid}", {"id": "ex-shop", "heading": "Shop assistant", "bullets": [
        {"id": "b-till", "text": "Ran the till"}, {"id": "b-stock", "text": "Stocked the shelves"}]}))


class Save(unittest.TestCase):
    def setUp(self):
        self.enterContext(cv_home())
        self.con = db.connect()
        self.addCleanup(self.con.close)
        store.new_version(with_job(store.current(self.con)[1]), origin="manual", what="A job", by="you")
        self.base, self.doc = store.current(self.con)
        self.sid = next(s.id for s in self.doc.sections if s.kind == "experience")
        self.job = f"s:{self.sid}/e:ex-shop"

    def test_a_sessions_changes_make_one_new_version_on_their_base(self):
        changes = [Change("replace", f"{self.job}/b:b-till", "Ran the till on Saturdays"),
                   Change("insert", self.job, {"id": "b-new", "text": "Opened the shop at eight"}),
                   Change("move", f"{self.job}/b:b-stock", position=0),
                   Change("replace", f"{self.job}/locked", True),
                   Change("hide", self.job)]
        n = len(store.versions(self.con))
        vid, _, after = edit.save(self.con, self.base, changes, "You edited your CV")
        self.assertEqual(len(store.versions(self.con)), n + 1)
        self.assertEqual(store.current(self.con)[0], vid)
        self.assertEqual(store.version(vid, con=self.con)["parent_id"], self.base)
        job = model.find(after, self.job)
        self.assertEqual([b.text for b in job.bullets],
                         ["Stocked the shelves", "Ran the till on Saturdays", "Opened the shop at eight"])
        self.assertTrue(job.locked and job.hidden)

    def test_a_line_you_type_or_reword_is_marked_as_your_words_and_the_others_are_not(self):
        changes = [Change("replace", f"{self.job}/b:b-till", "Ran the till on Saturdays"),
                   Change("insert", self.job, {"id": "b-new", "text": "Opened the shop at eight"}),
                   Change("insert", self.job, {"id": "b-card", "text": "From a card", "sources": ["card:4"]})]
        _, _, after = edit.save(self.con, self.base, changes, "You edited your CV")
        mine = model.your_words(after)
        self.assertEqual(mine, {f"{self.job}/b:b-till", f"{self.job}/b:b-new"})
        self.assertEqual(model.find(after, f"{self.job}/b:b-card").sources, ["card:4"])

    def test_a_save_on_a_version_no_longer_in_use_is_refused_and_saves_nothing(self):
        store.new_version(model.apply(self.doc, Change("replace", "s:summary/text", "Changed elsewhere.")),
                          origin="manual", what="Elsewhere", by="you")
        n = len(store.versions(self.con))
        with self.assertRaises(store.StaleVersion):
            edit.save(self.con, self.base, [Change("delete", f"{self.job}/b:b-till")], "You edited your CV")
        self.assertEqual(len(store.versions(self.con)), n)

    def test_changes_that_leave_the_cv_as_it_was_or_do_not_fit_are_refused(self):
        with self.assertRaises(CVError):
            edit.save(self.con, self.base, [Change("show", self.job)], "Nothing")
        with self.assertRaises(CVError):
            edit.save(self.con, self.base, [Change("delete", f"{self.job}/b:b-nothere")], "Nothing")
        for raw in ([], [{"op": "explode", "target": ""}], [{"op": "move", "target": "", "position": "1"}]):
            with self.subTest(raw=raw), self.assertRaises(CVError):
                edit.changes_from(raw)


class Cards(unittest.TestCase):
    def setUp(self):
        self.enterContext(cv_home())
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def card(self, kind: str, fields: dict) -> dict:
        return profile_store.add_by_hand(self.con, kind, fields)

    def test_a_job_gives_its_title_employer_dates_and_duties_each_line_back_to_its_card(self):
        card = self.card("job", JOB)
        e = edit.entry_from(card)
        self.assertEqual((e.heading, e.org, e.location, e.start, e.end),
                         ("Ticket clerk", "Made-up Rail", "Exampleton", "September 2023", "Present"))
        self.assertEqual([b.text for b in e.bullets], JOB["duties"])
        self.assertTrue(all(b.sources == [f"card:{card['id']}"] for b in e.bullets))
        self.assertTrue(e.id.startswith("ex-"))

    def test_education_a_project_and_a_certificate_keep_their_own_fields(self):
        degree = edit.entry_from(self.card("education", DEGREE))
        self.assertEqual((degree.heading, degree.org, degree.grade, degree.start, degree.end),
                         ("BSc Imaginary Studies", "Made-up University", "2:1", "September 2022", "June 2025"))
        self.assertEqual([(m.name, m.mark) for m in degree.modules], [("Maps of nowhere", "71"), ("Fen history", "")])
        card = self.card("project", PROJECT)
        project = edit.entry_from(card)
        self.assertEqual((project.heading, project.link, project.meta),
                         ("Rota bot", "github.com/made-up/rota", "Python, SQLite"))
        self.assertEqual([(b.text, b.sources) for b in project.bullets], [(PROJECT["summary"], [f"card:{card['id']}"])])
        cert = edit.entry_from(self.card("certificate", CERT))
        self.assertEqual((cert.heading, cert.org, cert.start, cert.grade), ("First aid at work", "Made-up Aid",
                                                                           "March 2024", "Pass"))

    def test_the_cards_list_leaves_out_hidden_cards_and_says_which_sections_fit(self):
        self.card("job", JOB)
        hidden = self.card("project", PROJECT)
        profile_store.hide(self.con, hidden["id"])
        self.card("skill", {"name": "Python"})
        out = edit.cards(self.con)
        self.assertEqual([(c["kind"], c["title"], c["sections"]) for c in out],
                         [("job", "Ticket clerk", ["experience", "volunteering"])])
        self.assertEqual(out[0]["entry"]["bullets"][0]["text"], JOB["duties"][0])


class Routes(unittest.TestCase):
    """The editor's routes through the real server."""

    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()

    def test_a_save_answers_with_the_new_version_and_a_stale_one_with_409(self):
        doc = self.c.get("/api/v2/cv/document").json()
        sid = next(s["id"] for s in doc["sections"] if s["kind"] == "experience")
        job = {"id": "ex-made", "heading": "Made-up job",
               "bullets": [{"id": "b-mine", "text": "A line in my own words"}]}
        target = f"s:{sid}/e:ex-made/b:b-mine"
        body = {"base": doc["version_id"], "changes": [{"op": "insert", "target": f"s:{sid}", "value": job}]}
        saved = self.c.request("PATCH", "/api/v2/cv/document", body)
        self.assertEqual(saved.status, 200)
        self.assertEqual(saved.json()["n"], doc["n"] + 1)
        again = self.c.request("PATCH", "/api/v2/cv/document", body)
        self.assertEqual(again.status, 409)
        self.assertIn("Your CV changed since you started editing", again.json()["error"]["message"])
        line = model.find(store.current()[1], target)
        self.assertEqual((line.text, line.sources[-1]), ("A line in my own words", model.YOUR_WORDS))

    def test_a_bad_change_is_a_400_and_no_base_is_a_400(self):
        base = self.c.get("/api/v2/cv/document").json()["version_id"]
        bad = self.c.request("PATCH", "/api/v2/cv/document",
                             {"base": base, "changes": [{"op": "delete", "target": "s:nothere"}]})
        self.assertEqual(bad.status, 400)
        self.assertEqual(self.c.request("PATCH", "/api/v2/cv/document", {"changes": []}).status, 400)

    def test_the_record_cards_route_lists_cards_as_entries(self):
        con = db.connect()
        try:
            profile_store.add_by_hand(con, "certificate", CERT)
        finally:
            con.close()
        cards = self.c.get("/api/v2/cv/record-cards").json()
        self.assertEqual([(c["title"], c["sections"], c["entry"]["heading"]) for c in cards],
                         [("First aid at work", ["certifications"], "First aid at work")])


if __name__ == "__main__":
    unittest.main()

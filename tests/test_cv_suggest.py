"""Suggestions on Hal's copy of the CV: the reviewers, the guards, and Accept, Edit, Undo, Leave it as
it is, Try another way and Accept all, on a made-up humanities graduate's CV.

The CV is held to the humanities graduate's CV type; the AI is the fake one, or none at all.
"""
from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from support.api import HalServer
from support.fake_ai import fake_ai

from jobhunt import db
from jobhunt.core import paths, runner
from jobhunt.core.settings import store as settings_store
from jobhunt.cvstudio import model, store, suggest

PROFILE = "s:summary/text"
TABLE = "s:experience/e:ex-shop/b:b-table"
STAR = "s:volunteering/e:vo-archive/b:b-letters"
LETTERS = "Catalogued parish letters for the public search"
VISITORS = "s:volunteering/e:vo-archive/b:b-visitors"


def made_up_cv() -> model.CVDoc:
    """A humanities graduate's CV with one of each problem the reviewers find without an AI."""
    m = model
    return m.CVDoc(headline="History graduate", sections=[
        m.Section(id="summary", kind="summary", title="Profile",
                  text="I am a hard-working History graduate who catalogued parish letters and organized a heritage "
                       "open day."),
        m.Section(id="education", kind="education", title="Education", entries=[
            m.Entry(id="ed-ba", heading="BA (Hons) History and Imaginary Studies", org="University of Nowhere Fen",
                    start="09/2023", end="Jun 2026", grade="2:1",
                    bullets=[m.Bullet(id="b-diss", text="Dissertation: parish records of Fenmoor, 1850 to 1900")])]),
        m.Section(id="experience", kind="experience", title="Work experience", entries=[
            m.Entry(id="ex-shop", heading="Bookseller", org="Lantern Books", start="Sep 2024", end="Present",
                    bullets=[m.Bullet(id="b-table", text="Ran the local history table and wrote weekly staff "
                                                         "recommendations"),
                             m.Bullet(id="b-events", text="Organized author events for the shop")])]),
        m.Section(id="volunteering", kind="volunteering", title="Volunteering", entries=[
            m.Entry(id="vo-archive", heading="Archive Volunteer", org="Fenmoor Records Office", start="Jan 2024",
                    end="Present",
                    bullets=[m.Bullet(id="b-letters", text=LETTERS + " \u2605"),
                             m.Bullet(id="b-visitors", text="Helped visitors find family records")])]),
        m.Section(id="skills", kind="skills", title="Skills", entries=[
            m.Entry(id="sk-writing", heading="Writing", items=["reports", "exhibition text"]),
            m.Entry(id="sk-research", heading="Research", items=["archival research", "oral history"]),
            m.Entry(id="sk-tools", heading="Tools", items=["Excel", "NVivo"])]),
        m.Section(id="interests", kind="interests", title="Interests", text="Local history walks, reading, travel."),
    ])


def ai_suggests(target: str, before: str, *afters: str, evidence=("Your record: archive notes (2025)",)):
    """A `cv_suggest` reply that rewords `target` when its section is the one being read, else none."""
    def reply(req) -> str:
        if target not in json.loads(req.user)["lines"]:
            return json.dumps({"none": True})
        return json.dumps({"target": target, "op": "replace", "kind": "Wording", "title": "Show what this says",
                           "why": "Recruiters skim for evidence.", "before": before, "afters": list(afters),
                           "trigger": {"type": "check", "id": "entry_says_what",
                                       "label": "Each entry says what you did"},
                           "evidence": list(evidence)})
    return reply


class CvCase(unittest.TestCase):
    """A temporary home holding the made-up CV as version 1."""

    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-cv-")))
        stack.enter_context(paths.set_override(tmp / "home"))
        settings_store.drop_cache()
        self.addCleanup(settings_store.drop_cache)
        self.con = db.connect()
        self.addCleanup(self.con.close)
        self.first = store.new_version(made_up_cv(), origin="imported", what="Your made-up CV", by="baseline")
        store.set_type("humanities_grad")   # the store starts on the technical type; this CV is a history one

    def pending(self, kind: str | None = None) -> list[dict]:
        return [s for s in suggest.listing(self.con)["pending"] if kind is None or s["kind"] == kind]

    def one(self, kind: str, where_: str = "") -> dict:
        found = [s for s in self.pending(kind) if where_ in s["where"]]
        self.assertTrue(found, f"no {kind} suggestion at {where_!r}: {self.pending()}")
        return found[0]

    def text_at(self, target: str) -> str:
        return suggest.shown_at(store.current()[1], "replace", target)


class Reviewers(CvCase):
    def test_deterministic_suggestions_appear_with_no_ai(self):
        out = suggest.look_again(self.con)
        kinds = {s["kind"] for s in self.pending()}
        self.assertGreaterEqual(out["found"], 5)
        self.assertLessEqual({"Consistency", "Symbols", "Dates", "Order"}, kinds)
        dates = self.one("Dates")
        self.assertEqual((dates["before"], dates["afters"]), ("09/2023", ["Sep 2023"]))
        order = self.one("Order")
        self.assertEqual(order["afters"], ["Profile, Education, Volunteering, Work experience, Skills, Interests"])
        for s in self.pending():
            self.assertTrue(s["why"] and s["trigger"], s)
            self.assertFalse(s["ai"])

    def test_accepting_the_order_suggestion_reorders_the_sections(self):
        # Regression: the whole-CV target "cv" is not a CV model target, so the Order suggestion never appeared
        suggest.look_again(self.con)
        suggest.accept(self.con, self.one("Order")["id"])
        self.assertEqual([s.id for s in store.current()[1].sections],
                         ["summary", "education", "volunteering", "experience", "skills", "interests"])

    def test_look_again_stops_at_six_waiting(self):
        settings_store.drop_cache()
        doc = made_up_cv()
        for i in range(8):
            doc.sections[2].entries[0].bullets.append(model.Bullet(id=f"x{i}", text=f"Organized stall {i} a day"))
        store.new_version(doc, origin="edited", what="More lines", by="you")
        suggest.look_again(self.con)
        self.assertEqual(len(self.pending()), suggest.MAX_PENDING)
        self.assertEqual(suggest.listing(self.con)["state"], "ready")


class Accepting(CvCase):
    def test_accept_makes_a_version_the_next_suggestion_is_worked_out_from(self):
        suggest.look_again(self.con)
        spelling = self.one("Consistency", "Profile")
        out = suggest.accept(self.con, spelling["id"])
        vid, _ = store.current()
        self.assertEqual(out["version_id"], vid)
        self.assertEqual(out["n"], 2)
        self.assertFalse(out["ask_send_copy"])
        self.assertIn("organised a heritage open day", self.text_at(PROFILE))
        what = self.con.execute("SELECT what, origin FROM cv_versions WHERE id = ?", (vid,)).fetchone()
        self.assertEqual(tuple(what), (f"You accepted: {spelling['title']}", "improved"))
        suggest.look_again(self.con)
        wording = self.one("Wording", "Profile")
        self.assertEqual(wording["before"], self.text_at(PROFILE))
        self.assertNotIn("hard-working", wording["afters"][0])
        based = {r[0] for r in self.con.execute("SELECT based_on FROM cv_suggestions WHERE status = 'pending'")}
        self.assertEqual(based, {vid})

    def test_accepting_while_the_original_is_sent_asks_to_send_hals_copy(self):
        suggest.look_again(self.con)
        self.con.execute("UPDATE cv_documents SET send = 'original'")
        self.con.commit()
        self.assertTrue(suggest.accept(self.con, self.one("Dates")["id"])["ask_send_copy"])

    def test_a_stale_suggestion_is_superseded_and_says_so(self):
        suggest.look_again(self.con)
        star = self.one("Symbols")
        doc = model.apply(store.current()[1], model.Change("replace", STAR, "Catalogued letters"))
        store.new_version(doc, origin="edited", what="You edited your CV", by="you")
        with self.assertRaisesRegex(RuntimeError, "This has changed since"):
            suggest.accept(self.con, star["id"])
        status = self.con.execute("SELECT status FROM cv_suggestions WHERE id = ?", (star["id"],)).fetchone()[0]
        self.assertEqual(status, "superseded")

    def test_undo_and_accept_all_consistency_fixes_work(self):
        suggest.look_again(self.con)
        before = len(self.con.execute("SELECT id FROM cv_versions").fetchall())
        out = suggest.accept_kind(self.con, "Consistency")
        self.assertEqual(out["accepted"], 2)
        self.assertEqual(len(self.con.execute("SELECT id FROM cv_versions").fetchall()), before + 1)
        self.assertEqual(self.text_at("s:experience/e:ex-shop/b:b-events"), "Organised author events for the shop")
        self.assertIn("organised", self.text_at(PROFILE))
        with self.assertRaises(ValueError):
            suggest.accept_kind(self.con, "Wording")
        events_fix = self.con.execute("SELECT id FROM cv_suggestions WHERE target = ?",
                                      ("s:experience/e:ex-shop/b:b-events",)).fetchone()[0]
        undone = suggest.undo(self.con, events_fix)
        self.assertEqual(store.current()[0], undone["version_id"])
        self.assertEqual(self.text_at("s:experience/e:ex-shop/b:b-events"), "Organized author events for the shop")
        self.assertIn(events_fix, [s["id"] for s in self.pending("Consistency")])
        profile_fix = self.con.execute("SELECT id FROM cv_suggestions WHERE target = ? AND kind = 'Consistency'",
                                       (PROFILE,)).fetchone()[0]
        doc = model.apply(store.current()[1], model.Change("replace", PROFILE, "History graduate, rewritten."))
        store.new_version(doc, origin="edited", what="You edited your CV", by="you")
        with self.assertRaisesRegex(RuntimeError, "This has changed since. Edit it instead?"):
            suggest.undo(self.con, profile_fix)

    def test_nothing_more_to_suggest_appears_when_done(self):
        for _ in range(10):
            suggest.look_again(self.con)
            waiting = self.pending()
            if not waiting:
                break
            for s in waiting:
                suggest.leave(self.con, s["id"], reason="Not relevant") if s["kind"] == "Order" else \
                    suggest.accept(self.con, s["id"])
        shown = suggest.listing(self.con)
        self.assertEqual(shown["state"], "nothing_more")
        self.assertEqual(shown["message"], suggest.ALL_REVIEWED)
        self.assertEqual(suggest.look_again(self.con)["found"], 0)


class Guards(CvCase):
    def test_a_reason_stops_the_same_suggestion_coming_back(self):
        suggest.look_again(self.con)
        star = self.one("Symbols")
        out = suggest.leave(self.con, star["id"], reason="This is not true")
        self.assertEqual(out, {"ok": True, "remembered": False})
        suggest.look_again(self.con)
        self.assertNotIn(STAR, [s["target"] for s in self.pending()])
        reply = ai_suggests(TABLE, "Ran the local history table and wrote weekly staff recommendations",
                            "Chose stock for the local history table and wrote weekly recommendations")
        with fake_ai({"cv_suggest": reply}):
            suggest.look_again(self.con)
            table = self.one("Wording", "Bookseller")
            out = suggest.leave(self.con, table["id"], reason="Keep my wording", remember=True)
            self.assertTrue(out["remembered"])
            other = ai_suggests(TABLE, "Ran the local history table and wrote weekly staff recommendations",
                                "Looked after the local history table")
            with fake_ai({"cv_suggest": other}):
                suggest.look_again(self.con)
        self.assertNotIn(TABLE, [s["target"] for s in self.pending()])
        pref = self.con.execute("SELECT kind, target FROM cv_preferences").fetchone()
        self.assertEqual(tuple(pref), ("keep_wording", TABLE))

    def test_numbers_hal_cannot_find_become_gaps(self):
        reply = ai_suggests(VISITORS, "Helped visitors find family records",
                            "Catalogued 340 parish letters in 2019 with a Distinction, written up for the public "
                            "search in 1850 terms")
        with fake_ai({"cv_suggest": reply}):
            suggest.look_again(self.con)
        mine = [s for s in self.pending("Wording") if s["where"] == "Volunteering, Archive Volunteer"]
        self.assertEqual(len(mine), 1, self.pending())
        after = mine[0]["afters"][0]
        self.assertIn("Catalogued [how many?] parish letters in [when?] with a [which?]", after)
        self.assertIn("1850", after)                        # in the CV already, so it stays
        with self.assertRaisesRegex(ValueError, r"Fill in the gap first"):
            suggest.accept(self.con, mine[0]["id"])
        mine_text = "Helped 340 visitors find family records"
        out = suggest.accept(self.con, mine[0]["id"], text=mine_text)
        self.assertEqual(self.text_at(VISITORS), mine_text)
        row = self.con.execute("SELECT status, edited FROM cv_suggestions WHERE id = ?", (mine[0]["id"],)).fetchone()
        self.assertEqual(tuple(row), ("edited", mine_text))
        self.assertTrue(out["version_id"])

    def test_a_locked_line_is_never_touched(self):
        doc = made_up_cv()
        doc.sections[3].entries[0].bullets[0].locked = True
        store.new_version(doc, origin="edited", what="You locked a line", by="you")
        self.con.execute("UPDATE cv_documents SET layout = ?", (json.dumps({"locked_lines": [TABLE]}),))
        self.con.commit()
        replies = {"cv_suggest": lambda req: (ai_suggests(STAR, LETTERS + " \u2605", "Listed letters")(req)
                                              if STAR in json.loads(req.user)["lines"] else
                                              ai_suggests(TABLE, "Ran the local history table and wrote weekly "
                                                          "staff recommendations", "Ran a table")(req))}
        with fake_ai(replies) as backend:
            suggest.look_again(self.con)
        self.assertTrue(backend.requests("cv_suggest"))
        touched = {s["target"] for s in self.pending()}
        self.assertNotIn(STAR, touched)
        self.assertNotIn(TABLE, touched)

    def test_a_banned_phrase_never_reaches_a_card(self):
        reply = ai_suggests(TABLE, "Ran the local history table and wrote weekly staff recommendations",
                            "A passionate team player who ran the history table",
                            "Ran the history table and wrote weekly recommendations")
        with fake_ai({"cv_suggest": reply}):
            suggest.look_again(self.con)
        self.assertEqual(self.one("Wording", "Bookseller")["afters"],
                         ["Ran the history table and wrote weekly recommendations"])


class TryAnother(CvCase):
    def test_try_another_way_gives_new_wording(self):
        first = "Chose stock for the local history table and wrote weekly recommendations"
        reply = ai_suggests(TABLE, "Ran the local history table and wrote weekly staff recommendations", first)
        newer = "Curated the shop's local history table and wrote short weekly reviews"
        with fake_ai({"cv_suggest": reply, "cv_another": json.dumps({"afters": [newer]})}) as backend:
            suggest.look_again(self.con)
            table = self.one("Wording", "Bookseller")
            out = suggest.another(self.con, table["id"])
            self.assertEqual(out, {"new": True, "alt": 1, "after": newer})
            shown = json.loads(backend.requests("cv_another")[0].user)["already_shown"]
            self.assertEqual(shown, [first])
        again = json.dumps({"afters": [first, newer]})
        with fake_ai({"cv_another": again}):
            out = suggest.another(self.con, table["id"])
        self.assertEqual(out["message"], "This is the only sensible option here. Edit it instead?")
        self.assertFalse(out["new"])
        self.assertEqual(self.one("Wording", "Bookseller")["afters"], [first, newer])
        out = suggest.accept(self.con, table["id"], alt=1)
        self.assertEqual(self.text_at(TABLE), newer)

    def test_try_another_way_with_no_ai_says_so(self):
        suggest.look_again(self.con)
        out = suggest.another(self.con, self.one("Dates")["id"])
        self.assertEqual((out["new"], out["message"]), (False, suggest.ONLY_OPTION))


class Api(unittest.TestCase):
    def test_the_suggestions_api_looks_again_accepts_leaves_and_undoes(self):
        with HalServer() as hal:
            hal_client = hal.signed_in()
            store.new_version(made_up_cv(), origin="imported", what="Your made-up CV", by="baseline")
            started = hal_client.post("/api/v2/cv/suggestions/look-again").json()["action_id"]
            self.assertEqual(runner.wait(started)["state"], "done")
            shown = hal_client.get("/api/v2/cv/suggestions").json()
            self.assertEqual(shown["state"], "ready")
            self.assertIn("Consistency", shown["kinds"])
            dates = next(s for s in shown["pending"] if s["kind"] == "Dates")
            out = hal_client.post(f"/api/v2/cv/suggestions/{dates['id']}/accept", {"alt": 0})
            self.assertEqual(out.status, 200, out.text)
            self.assertEqual(set(out.json()), {"version_id", "n", "ask_send_copy"})
            runner.flush()
            only = hal_client.get("/api/v2/cv/suggestions?kind=Symbols").json()["pending"]
            self.assertEqual({s["kind"] for s in only}, {"Symbols"})
            left = hal_client.post(f"/api/v2/cv/suggestions/{only[0]['id']}/leave",
                                   {"reason": "Too long", "remember": True}).json()
            self.assertEqual(left, {"ok": True, "remembered": True})
            bad = hal_client.post(f"/api/v2/cv/suggestions/{only[0]['id']}/leave", {"reason": "Too long"})
            self.assertEqual(bad.status, 409)
            self.assertEqual(hal_client.post("/api/v2/cv/suggestions/accept-kind", {"kind": "Order"}).status, 400)
            fixed = hal_client.post("/api/v2/cv/suggestions/accept-kind", {"kind": "Consistency"}).json()
            self.assertEqual(fixed["accepted"], 2)
            undone = hal_client.post(f"/api/v2/cv/suggestions/{dates['id']}/undo")
            self.assertEqual(undone.status, 200, undone.text)
            card = hal_client.get("/api/v2/cv/adjust").json()
            self.assertEqual((card["level"], card["forced_no"]), ("little", ""))
            asked = hal_client.post("/api/v2/cv/fresh", {"answers": "no"})
            self.assertEqual(asked.status, 400)
            for action in self._actions():
                runner.wait(action)

    def _actions(self) -> list[str]:
        con = db.connect()
        try:
            return [r[0] for r in con.execute("SELECT id FROM actions WHERE state IN ('queued', 'running')")]
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()

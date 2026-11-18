"""Choosing the CV an application is built from (`cvstudio/choose.py`): skills covered blended with how close
the wording reads, the lead CV on a near tie, skills alone when the AI on this computer gives no vectors, and the
lead CV with a reason when comparing fails. Every CV and advert here is made up.
"""
import threading
import unittest
from unittest import mock

import support  # noqa: F401 - the temporary home, before any jobhunt import
from test_cv_store import cv_home

from jobhunt import ai, db
from jobhunt.ai.fake import FakeBackend
from jobhunt.cvstudio import choose, model, score, store, tailor

WAIT_S = 20          # bounds a thread that would otherwise hang the test
SETTLE_S = 0.5       # long enough for an unlocked write to store its choice

ADVERT = {"id": "made-up:1", "title": "Backend engineer",
          "description": "Requirements:\nPython, SQL, Docker, AWS and Linux for our services. Git and testing."}
BACKEND = {"title": "Backend developer", "summary": "Builds services for a made-up shop.",
           "skills": [["Tools", ["Python", "SQL", "Docker", "AWS", "Linux", "Git"]]],
           "projects": [], "experience": [], "education": [], "additional": [], "moreProjects": "", "closing": ""}
APPLIED_AI = {"title": "Applied AI engineer", "summary": "Trains models for a made-up lab.",
              "skills": [["Tools", ["Python", "machine learning"]]],
              "projects": [], "experience": [], "education": [], "additional": [], "moreProjects": "", "closing": ""}


def make_cv(name: str, v1: dict) -> str:
    return store.create(name, model.from_v1(v1), origin="uploaded", what=f"Your file: {name}.pdf", by="baseline")


def vectors_by_word(word_vectors: dict[str, list[float]]):
    """A fake embed: each text gets the vector of the first listed word it holds, else the last one."""
    def embed(texts, model_name):
        out = []
        for t in texts:
            out.append(next((v for w, v in word_vectors.items() if w in t), list(word_vectors.values())[-1]))
        return out
    return embed


class Rank(unittest.TestCase):
    """The pure ranking: no store, no AI."""

    def test_a_cv_that_covers_more_skills_wins_and_the_reason_counts_them(self):
        best, why = choose.rank([{"name": "Applied AI", "have": 2, "of": 7, "skills": 0.3, "similarity": None},
                                 {"name": "Backend CV", "have": 6, "of": 7, "skills": 0.9, "similarity": None}])
        self.assertEqual(best, 1)
        self.assertEqual(why, "Covers 6 of the 7 skills this advert asks for; your Applied AI CV covers 2.")

    def test_within_the_margin_the_first_cv_in_your_order_wins(self):
        best, why = choose.rank([{"name": "Backend", "have": 5, "of": 7, "skills": 0.70, "similarity": None},
                                 {"name": "Applied AI", "have": 5, "of": 7, "skills": 0.73, "similarity": None}])
        self.assertEqual(best, 0)
        self.assertIn("about as well", why)
        self.assertIn("first one in your list", why)

    def test_wording_decides_when_both_cover_the_same_skills(self):
        best, why = choose.rank([{"name": "Backend", "have": 3, "of": 7, "skills": 0.4, "similarity": 0.6},
                                 {"name": "Applied AI", "have": 3, "of": 7, "skills": 0.4, "similarity": 0.85}])
        self.assertEqual(best, 1)
        self.assertEqual(why, "Its wording is closest to this advert's, closer than your Backend CV's. "
                              "Both cover 3 of the 7 skills it asks for.")

    def test_an_advert_naming_no_skill_and_no_vectors_keeps_the_lead(self):
        best, why = choose.rank([{"name": "A", "have": 0, "of": 0, "skills": None, "similarity": None},
                                 {"name": "B", "have": 0, "of": 0, "skills": None, "similarity": None}])
        self.assertEqual((best, why), (0, choose.NO_SKILLS))

    def test_cv_names_read_naturally(self):
        self.assertEqual([choose.your(n) for n in ("Backend CV", "Applied AI", "My CV", "")],
                         ["your Backend CV", "your Applied AI CV", "your CV", "your CV"])


class Choose(unittest.TestCase):
    """Against the store, with the made-up AI."""

    def setUp(self):
        self.enterContext(cv_home())
        self.enterContext(mock.patch.object(store, "type_in_use", return_value="technical_grad"))
        choose._VECTORS.clear()
        self.ai_cv = make_cv("Applied AI", APPLIED_AI)
        self.backend = make_cv("Backend", BACKEND)
        store.reorder([self.ai_cv, self.backend])     # the person put the Applied AI CV first
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def test_with_one_cv_in_use_it_is_used_with_no_ai_call_and_no_reason(self):
        store.archive(self.backend)
        fake = FakeBackend()
        with ai.use(fake), mock.patch.object(fake, "embed") as embed, \
                mock.patch.object(score, "keywords") as keywords:
            got = choose.choose(self.con, ADVERT)
        embed.assert_not_called()
        keywords.assert_not_called()
        self.assertEqual((got.doc_id, got.reason, got.scores), (self.ai_cv, "", []))

    def test_without_vectors_the_skills_decide_alone(self):
        got = choose.choose(self.con, ADVERT)     # the tests' own AI is switched off, so no vectors
        self.assertEqual(got.doc_id, self.backend)
        self.assertEqual(got.version_id, store.current(doc_id=self.backend)[0])
        self.assertTrue(got.reason.startswith("Covers 6 of the "), got.reason)
        self.assertIn("your Applied AI CV covers 1", got.reason)
        self.assertEqual({s["similarity"] for s in got.scores}, {None})

    def test_skills_and_wording_are_blended(self):
        fake = FakeBackend()
        # The advert reads like the Applied AI CV, but the Backend CV covers far more of its skills.
        fake.embed = vectors_by_word({"lab": [1.0, 0.0], "shop": [0.8, 0.6], "Backend engineer": [1.0, 0.0]})
        with ai.use(fake):
            got = choose.choose(self.con, ADVERT)
        by_id = {s["doc_id"]: s for s in got.scores}
        self.assertEqual(by_id[self.ai_cv]["similarity"], 1.0)
        self.assertEqual(by_id[self.backend]["similarity"], 0.8)
        self.assertEqual(got.doc_id, self.backend, "six skills of seven outweigh closer wording")
        self.assertGreater(by_id[self.ai_cv]["fit"], 50, "the wording still counts for the Applied AI CV")

    def test_each_cv_version_is_embedded_once(self):
        fake = FakeBackend()
        seen = []

        def embed(texts, model_name):
            seen.extend(texts)
            return [[1.0, float(len(t) % 7)] for t in texts]
        fake.embed = embed
        with ai.use(fake):
            choose.choose(self.con, ADVERT)
            choose.choose(self.con, {**ADVERT, "id": "made-up:2"})
        self.assertEqual(len(seen), 4, "two adverts, and each CV only the first time")

    def test_an_online_ai_is_never_sent_a_cv_for_vectors(self):
        fake = FakeBackend()
        fake.online = True
        with ai.use(fake), mock.patch.object(fake, "embed") as embed:
            got = choose.choose(self.con, ADVERT)
            with self.assertRaises(ai.LLMUnavailable):
                ai.embed(["a"])
        embed.assert_not_called()
        self.assertEqual(got.doc_id, self.backend)

    def test_when_comparing_fails_the_lead_cv_is_used_and_the_reason_says_so(self):
        with mock.patch.object(score, "keywords", side_effect=RuntimeError("made-up failure")), \
                self.assertLogs("jobhunt.cvstudio.choose", "ERROR"):
            got = choose.choose(self.con, ADVERT)
        self.assertEqual((got.doc_id, got.reason), (self.ai_cv, choose.FALLBACK))


class OneChoicePerJob(unittest.TestCase):
    """Two document writes for one job at once each choose and store in turn (review of 2.3.0): Hal adjusting
    the job's CV on the CV it chose, while the person picks another for it with Change CV."""

    def setUp(self):
        self.enterContext(cv_home())
        self.enterContext(mock.patch.object(store, "type_in_use", return_value="technical_grad"))
        self.ai_cv = make_cv("Applied AI", APPLIED_AI)
        self.backend = make_cv("Backend", BACKEND)
        con = db.connect()
        try:
            con.execute("INSERT INTO jobs (id, source, company, title, first_seen, last_seen) VALUES "
                        "(?, 'manual', 'Made-up Shop', ?, '2026-11-18T09:00:00Z', '2026-11-18T09:00:00Z')",
                        (ADVERT["id"], ADVERT["title"]))
            con.commit()
        finally:
            con.close()

    def test_the_persons_pick_made_during_hals_write_is_the_one_kept(self):
        entered, release = threading.Event(), threading.Event()
        real = tailor.for_job

        def slow(job, *, level=None, priorities=None, base=None):
            if base.doc_id == self.ai_cv:      # Hal's write, which chose before the person picked
                entered.set()
                release.wait(WAIT_S)
            return real(job, level="no", base=base)

        hals = choose.Choice(self.ai_cv, store.current(doc_id=self.ai_cv)[0])
        with mock.patch.object(tailor, "for_job", slow), mock.patch.object(choose, "choose", return_value=hals):
            first = threading.Thread(target=tailor.prepare, args=(ADVERT,))
            first.start()
            self.assertTrue(entered.wait(WAIT_S))
            con = db.connect()
            self.addCleanup(con.close)
            picked = choose.by_you(con, ADVERT, self.backend)
            second = threading.Thread(target=tailor.prepare, args=(ADVERT,), kwargs={"base": picked})
            read = []
            reader = threading.Thread(target=lambda: read.append(choose.base_doc(ADVERT["id"])))
            second.start()
            reader.start()
            second.join(SETTLE_S)         # without the lock the person's pick is stored here, then overwritten
            release.set()
            for t in (first, second, reader):
                t.join(WAIT_S)
        self.assertEqual(choose.base_doc(ADVERT["id"]), self.backend)
        self.assertEqual(choose.kept(con, ADVERT["id"]).by, choose.YOU)
        self.assertIn(read[0], (self.ai_cv, self.backend), "a read waits for a stored choice, never None mid-write")


if __name__ == "__main__":
    unittest.main()

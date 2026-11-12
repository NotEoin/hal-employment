"""Learning from the person's edits: each change to Hal's draft is kept as a pair, and the rules the pairs show
wait as suggestions in What Hal remembers until the person says yes.

The made-up humanities graduate of `support.people` edits made-up drafts in the made-up world; nothing here
asks an AI.
"""
import unittest

from support import people
from test_pack import add_job, as_applicant

from jobhunt import autofill, documents, pack
from jobhunt.apply import actions as apply_actions
from jobhunt.core import settings
from jobhunt.learn import items, patterns
from jobhunt.writing import edits

DRAFT = ("I would endeavour to bring the care I gave the made-up heritage centre's visitors to your "
         "collections, and I would endeavour to learn quickly.")
FINAL = "I would bring the care I gave the made-up heritage centre's visitors to your collections."


def pairs(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT kind, ref, draft, final FROM writing_edits ORDER BY id")]


class Pairs(unittest.TestCase):
    def test_your_edit_of_a_letter_is_kept_and_hals_own_rewrite_is_not(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", status="applying")
            documents.save(a.con, job, {**documents.load(a.con, job), "letter": DRAFT})
            apply_actions.save_documents(a.con, job["id"], {"letter": "A made-up rewrite by Hal."})
            self.assertEqual(pairs(a.con), [], "Hal's own rewrite teaches nothing")
            apply_actions.save_documents(a.con, job["id"], {"letter": FINAL}, by_you=True)
            kept = pairs(a.con)
        self.assertEqual(kept, [{"kind": "letter", "ref": job["id"], "draft": "A made-up rewrite by Hal.",
                                 "final": FINAL}])

    def test_repeated_edits_keep_hals_first_draft_and_your_latest_text(self):
        with as_applicant(people.HUMANITIES) as a:
            edits.record(a.con, "letter", "job:1", DRAFT, "A first edit of the made-up letter.")
            edits.record(a.con, "letter", "job:1", "A first edit of the made-up letter.", FINAL)
            kept = pairs(a.con)
        self.assertEqual([(p["draft"], p["final"]) for p in kept], [(DRAFT, FINAL)])

    def test_an_answer_to_a_private_question_is_never_kept(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", status="applying")
            before = {"answers": [{"question": "Do you consider yourself to have a disability?", "answer": "A."},
                                  {"question": "Why this role?", "answer": "Hal's made-up answer."}]}
            after = {"answers": [{"question": "Do you consider yourself to have a disability?", "answer": "B."},
                                 {"question": "Why this role?", "answer": "My own made-up answer."}]}
            edits.from_documents(a.con, job, before, after)
            kept = pairs(a.con)
        self.assertEqual([p["ref"] for p in kept], [f"{job['id']}:Why this role?"])

    def test_a_written_form_answer_you_changed_is_kept_with_hals_answer(self):
        hal = "I would bring the care I gave visitors at a made-up heritage centre, where I guided every week."
        field = {"hid": "q1", "kind": "textarea", "label": "Why do you want this role?", "value": hal,
                 "source": "model"}
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", status="applying")
            autofill.by_you(field, "I guided visitors every week at a made-up heritage centre.")
            autofill.remember(a.con, [field], job["company"], job=job)
            kept = pairs(a.con)
        self.assertEqual([(p["kind"], p["draft"]) for p in kept], [("answer", hal)])

    def test_approving_with_your_edits_keeps_them(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", status="applying")
            documents.save(a.con, job, {**documents.load(a.con, job), "message": "Hal's made-up message."})
            from unittest import mock

            with mock.patch.object(apply_actions, "start", lambda *a, **k: "made-up-action"), \
                    mock.patch.object(documents, "render", lambda job, docs: {}):
                apply_actions.approve_documents(a.con, job["id"], {"message": "My own made-up message."})
            kept = pairs(a.con)
        self.assertEqual([(p["kind"], p["final"]) for p in kept], [("message", "My own made-up message.")])


class Rules(unittest.TestCase):
    def test_a_word_you_take_out_three_times_becomes_a_never_use_suggestion(self):
        with as_applicant(people.HUMANITIES) as a:
            for n in range(edits.DROP_MIN):
                edits.record(a.con, "letter", f"job:{n}", DRAFT, FINAL, job_id=None)
            found = patterns.run(a.con)
            waiting = items.waiting(a.con)
            self.assertEqual(len(found["new"]), 1)
            offer = waiting[0]
            self.assertEqual(offer["title"], 'Never use "endeavour"?')
            self.assertEqual(offer["area"], "Writing")
            self.assertIn("3 of Hal's drafts", offer["why"])
            self.assertNotIn("endeavour", settings.get("cvdocs.never_use"), "nothing changes without your yes")
            items.decide(a.con, offer["id"], "yes")
            self.assertIn("endeavour", settings.get("cvdocs.never_use"))
            self.assertIn('"endeavour"', pack.person()["avoid_phrases"], "the next letter is told")

    def test_two_edits_are_not_enough_and_a_word_you_write_yourself_is_never_offered(self):
        with as_applicant(people.HUMANITIES) as a:
            for n in range(edits.DROP_MIN - 1):
                edits.record(a.con, "letter", f"job:{n}", DRAFT, FINAL)
            self.assertEqual(edits.learned(a.con), [])
            edits.record(a.con, "letter", "job:9", DRAFT, FINAL + " I endeavour to learn.")
            self.assertEqual(edits.learned(a.con), [])

    def test_letters_you_cut_offer_a_shorter_length(self):
        long_letter = " ".join(["word"] * 250)
        short_letter = " ".join(["word"] * 160)
        with as_applicant(people.HUMANITIES) as a:
            settings.set("cvdocs.letter_words", 250, by="you")
            for n in range(edits.LENGTH_MIN):
                edits.record(a.con, "letter", f"job:{n}", long_letter, short_letter)
            offers = edits.learned(a.con)
        self.assertEqual([o["title"] for o in offers], ["Write letters of about 150 words?"])
        self.assertEqual(offers[0]["change"], {"setting": "cvdocs.letter_words", "value": 150,
                                               "learned": "Letters of about 150 words"})

    def test_switching_writing_off_stops_its_suggestions(self):
        with as_applicant(people.HUMANITIES) as a:
            settings.set("learn.enabled", ["finding", "cv"], by="you")
            for n in range(edits.DROP_MIN):
                edits.record(a.con, "letter", f"job:{n}", DRAFT, FINAL)
            self.assertEqual(patterns.run(a.con)["new"], [])

    def test_a_kept_edit_starts_a_look_for_patterns(self):
        from jobhunt.core import events
        from jobhunt.learn import actions as learn_actions

        self.assertEqual(learn_actions.trigger_of(events.Event(1, edits.EVENT, {"what": "edit"})), "writing")

    def test_the_word_by_word_diff_finds_swaps(self):
        dropped, added, swaps = edits.changes("We utilised the archive", "We used the archive")
        self.assertEqual((dict(dropped), dict(added), swaps), ({"utilised": 1}, {"used": 1}, [("utilised", "used")]))


if __name__ == "__main__":
    unittest.main()

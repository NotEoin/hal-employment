"""Thinking effort: every prompt has a level with a reason, record reads keep theirs, and the cap bounds all."""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.ai import effort, prompts

# What one application's documents ask with no redraft: prompt id -> (prompt tokens, answer tokens without the
# thinking), from the gateway's replies for three real adverts on a copy of real data (2026-11-19).
DOCUMENT_CALLS = {"analyse": (1950, 300), "brief": (2900, 120), "fit": (4000, 400), "letter": (5000, 400),
                  "message": (4300, 120), "answers": (4600, 300), "verify": (6100, 130)}
REDRAFTED = ("letter", "message", "answers")    # each may be written once more, from about the same request
# About three minutes when nothing needs a redraft (owner's aim: about two): 188 s at most, of which the check at
# `medium` takes 38 s, 14 s more than at `low` (768 more thinking tokens at 55 a second, 2026-11-19).
FIRST_DRAFTS_S = 190
WORST_S = 300                  # five minutes, the owner's most for one application (2026-11-19)
READ_TOKENS_S, WRITE_TOKENS_S = 500, 55     # the owner's card (2026-11-19)


class Levels(unittest.TestCase):
    def test_every_prompt_has_a_level_and_a_reason(self):
        ids = set(prompts.all_prompts())
        self.assertEqual(sorted(ids - set(effort.LEVELS)), [], "a prompt with no effort level")
        self.assertEqual(sorted(set(effort.LEVELS) - ids - {"ai_test"}), [], "a level for no prompt")
        for pid, found in effort.LEVELS.items():
            with self.subTest(pid):
                self.assertIn(found.level, effort.EFFORTS)
                self.assertTrue(found.why.strip())

    def test_record_reads_keep_the_thinking_they_had(self):
        """The owner's test of thinking off for reads kept 55 facts against 77 (2026-11-12)."""
        for pid in ("ingest_doc", "record_cv", "project_read", "coursework_read", "lecture_topics", "lecture_note",
                    "cv_extract", "cv_parse"):
            with self.subTest(pid):
                self.assertEqual(effort.budget(pid, effort.CAP_DEFAULT), (effort.HIGH, 8192))

    def test_the_letter_the_fit_map_and_the_check_think_at_medium_and_the_rest_little(self):
        """The letter is written in one call that chooses and weaves the evidence; the one check let an invented
        span of time and a miscounted number through at low (2026-11-19)."""
        self.assertEqual(effort.of("brief").level, effort.NONE)
        for pid in ("message", "answers", "analyse"):
            self.assertEqual(effort.of(pid).level, effort.LOW, pid)
        for pid in ("fit", "letter", "verify"):
            self.assertEqual(effort.of(pid).level, effort.MEDIUM, pid)

    def test_an_applications_documents_fit_their_budget_even_when_every_call_thinks_its_whole_budget(self):
        """Owner, 2026-11-19: about 5 minutes of model time for each application at most, ideally about 2; it was
        about 9, then 5 with 24 to 27 calls."""
        def seconds(pid: str) -> float:
            read, said = DOCUMENT_CALLS[pid]
            return read / READ_TOKENS_S + (effort.budget(pid, effort.CAP_DEFAULT)[1] + said) / WRITE_TOKENS_S

        first = sum(seconds(pid) for pid in DOCUMENT_CALLS)
        self.assertLessEqual(first, FIRST_DRAFTS_S)
        self.assertLessEqual(first + sum(seconds(pid) for pid in REDRAFTED), WORST_S, "with a redraft of each")

    def test_the_cap_bounds_every_budget_and_zero_turns_thinking_off(self):
        self.assertEqual(effort.budget("fit", 1000), (effort.MEDIUM, 1000))
        self.assertEqual(effort.budget("fit", 0), (effort.NONE, 0))
        self.assertEqual(effort.budget("fit", -5), (effort.NONE, 0))

    def test_a_prompt_the_table_does_not_name_gets_the_cap_and_no_level(self):
        self.assertEqual(effort.budget("made_up_prompt", 4000), (None, 4000))
        self.assertEqual(effort.budget("made_up_prompt", 0), (effort.NONE, 0))

    def test_an_unknown_level_is_refused(self):
        with self.assertRaises(ValueError):
            effort.Effort("lots", "made up")


if __name__ == "__main__":
    unittest.main()

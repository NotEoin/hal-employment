"""Thinking effort: every prompt has a level with a reason, record reads keep theirs, and the cap bounds all."""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.ai import effort, prompts

# What one application's documents ask: (calls, prompt tokens, answer tokens) for each prompt, from `ai_usage` on
# a copy of real data (sizes in characters / 4) and the gateway's request log, 2026-11-19.
DOCUMENT_CALLS = {"analyse": (1, 930, 180), "brief": (1, 1130, 140), "fit": (1, 3260, 320),
                  "evidence_plan": (1, 3420, 610), "story_rank": (1, 2000, 150), "motive_pair": (1, 1580, 60),
                  "letter_frame": (1, 730, 100), "story_paragraph": (7, 1430, 105), "paragraph_verify": (4, 500, 25)}
FIVE_MINUTES_S = 300
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

    def test_short_checks_think_little_and_planning_thinks_more(self):
        self.assertEqual(effort.of("brief").level, effort.NONE)
        self.assertEqual(effort.of("paragraph_verify").level, effort.LOW)
        self.assertEqual(effort.of("story_paragraph").level, effort.LOW)
        for pid in ("fit", "evidence_plan", "story_rank"):
            self.assertEqual(effort.of(pid).level, effort.MEDIUM)

    def test_an_applications_documents_fit_five_minutes_even_when_every_call_thinks_its_whole_budget(self):
        """Owner, 2026-11-19: about 5 minutes of model time for each application; it was about 9."""
        seconds = sum(n * (read / READ_TOKENS_S + (effort.budget(pid, effort.CAP_DEFAULT)[1] + said) / WRITE_TOKENS_S)
                      for pid, (n, read, said) in DOCUMENT_CALLS.items())
        self.assertLessEqual(seconds, FIVE_MINUTES_S)

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

"""The ranking of the person's work for one job: one AI call, its names checked against the cards, the letter's
three best with facts, the rest for the message and answers, and the word count when the call fails."""
from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import plan, rank


def card(name: str, summary: str, tools: list[str], quotes: list[str], tier: str = "supporting",
         kind: str = "project (personal project)") -> dict:
    """A made-up project card with the fields `profile.writing.cards` gives."""
    return {"kind": kind, "name": name, "tools": tools, "summary": summary, "context": "personal", "tier": tier,
            "aliases": [], "role": "", "planned": [], "files": [], "quotes": quotes, "tag": "", "similar": []}


# Look-alikes of a trading-firm pack (2026-11-08): the letter led with a voice assistant and a job-search app while
# the packet sniffer and the real-time autopilot were left to the fit table.
VOICE = card("Parrot", "A voice assistant for the kitchen", ["Python"], ["Parrot answers spoken questions about "
             "recipes"], tier="core")
JOBS = card("Quill Employment", "A job-search helper for graduates", ["Preact", "SQLite"],
            ["Quill Employment drafts letters for graduates"], tier="core")
SNIFFER = card("Netferret", "A packet sniffer that reads PCAP captures", ["C++", "Python"],
               ["Netferret has a 113-test suite over recorded PCAP captures"])
PILOT = card("Gullwing", "A real-time pathfinding autopilot for a model boat", ["C++"],
             ["Gullwing replans its route twenty times a second"])
EMPTY = card("Old blog", "A static blog", ["Hugo"], [])
SHELF = card("Shelf planner", "A planner for shop shelves", ["Lua"], ["Shelf planner prints shelf plans"],
             tier="archive")
CARDS = [VOICE, JOBS, SNIFFER, PILOT, EMPTY, SHELF]
JOB = rank.Job("Graduate Software Engineer", "Made-up Markets", "Low-latency trading systems in C++ and Python",
               ("Networking knowledge", "Performance-minded C++"), "Made-up Markets trades on made-up exchanges.")
WANTED = "Networking knowledge\nPerformance-minded C++"


def reply(*names: str) -> dict:
    return {"ranking": [{"name": n, "reason": f"{n} fits"} for n in names]}


class Ranking(unittest.TestCase):
    def ask(self, answer) -> tuple[list[rank.Ranked], list[str]]:
        asked = []

        def chat_json(system: str, user: str, **kw) -> dict:
            asked.append(user)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        with mock.patch.object(llm, "chat_json", chat_json):
            return rank.make(JOB, CARDS, []), asked

    def test_one_call_offers_every_visible_card_with_its_tier_and_the_jobs_own_words(self):
        _, asked = self.ask(reply("Netferret"))
        self.assertEqual(len(asked), 1)
        request = asked[0]
        self.assertIn("Job: Graduate Software Engineer at Made-up Markets\nWhat the job is: Low-latency trading",
                      request)
        self.assertIn("Requirements:\n- Networking knowledge\n- Performance-minded C++\n", request)
        self.assertIn("What the employer does:\nMade-up Markets trades", request)
        self.assertIn("- Parrot (personal project; core): A voice assistant for the kitchen. Tools: Python", request)
        self.assertIn("- Netferret (personal project): A packet sniffer that reads PCAP captures. Tools: C++, Python",
                      request)
        self.assertNotIn("Shelf planner", request, "archived work is never offered")

    def test_only_names_of_the_cards_are_kept_each_once_with_their_reasons(self):
        ranked, _ = self.ask({"ranking": [{"name": "netferret ", "reason": "packet  capture work"},
                                          {"name": "Gullwing (real-time autopilot)", "reason": "real-time code"},
                                          {"name": "Netferret", "reason": "again"},
                                          {"name": "A trading bot", "reason": "invented"}, "Parrot"]})
        self.assertEqual(ranked, [rank.Ranked("Netferret", "packet capture work"),
                                  rank.Ranked("Gullwing", "real-time code"), rank.Ranked("Parrot", "")])

    def test_a_reply_with_no_ranking_or_no_ai_gives_none(self):
        self.assertEqual(self.ask({})[0], [])
        with self.assertLogs("jobhunt.writing.rank", "WARNING") as logged:
            self.assertEqual(self.ask(llm.LLMUnavailable("the AI is busy"))[0], [])
        self.assertIn("the AI could not rank the work (the AI is busy)", logged.output[0])


class Order(unittest.TestCase):
    def test_the_letter_tells_the_three_best_with_facts_and_the_rest_follow_in_rank_order(self):
        ranked = [rank.Ranked(n) for n in ("Netferret", "Old blog", "Gullwing", "Quill Employment", "Parrot")]
        told = rank.order([], CARDS, ranked, WANTED)
        self.assertEqual([s.name for s in told.letter], ["Netferret", "Gullwing", "Quill Employment"],
                         "a card with no facts gives a paragraph nothing to rest on")
        self.assertEqual([s.name for s in told.others],
                         ["Netferret", "Old blog", "Gullwing", "Quill Employment", "Parrot"])
        self.assertIn("Netferret has a 113-test suite over recorded PCAP captures", told.letter[0].facts)

    def test_the_plans_own_story_of_a_ranked_card_is_told_and_its_unranked_stories_come_last(self):
        items = [plan.Item("E1", "Parrot answers spoken questions about recipes", "CV", "Parrot"),
                 plan.Item("E2", "Volunteer guide at a made-up heritage centre", "CV")]
        found = plan.stories(items, CARDS)
        told = rank.order(found, CARDS, [rank.Ranked("Gullwing"), rank.Ranked("Parrot")], WANTED)
        self.assertEqual([s.name for s in told.letter], ["Gullwing", "Parrot"])
        self.assertIs(told.others[1], found[0], "the plan's story keeps its plan items")
        self.assertEqual(told.others[2].facts, ("Volunteer guide at a made-up heritage centre",))

    def test_with_no_ranking_the_job_words_choose_as_before(self):
        told = rank.order([], CARDS, [], WANTED)
        self.assertEqual([s.name for s in told.letter],
                         [s.name for s in plan.letter_stories([], CARDS, WANTED)])
        self.assertEqual(told.ranked, ())

    def test_at_an_equal_fit_the_word_count_puts_core_work_first(self):
        # the tier is a tie-break, never an override (2026-11-08)
        alike = [card("Tern", "Charts river levels", ["Lua"], ["Tern charts river levels"]),
                 card("Skua", "Charts river levels", ["Lua"], ["Skua charts river levels"], tier="core"),
                 card("Auk", "Charts river levels and tides daily", ["Lua"], ["Auk charts river levels and tides"])]
        fits = plan.fitting([], alike, "Chart river levels and tides")
        self.assertEqual([s.name for s in fits], ["Auk", "Skua", "Tern"])

    def test_a_ranking_reads_as_one_log_line(self):
        self.assertEqual(rank.said([rank.Ranked("Netferret", "packet work"), rank.Ranked("Gullwing")]),
                         "1. Netferret (packet work); 2. Gullwing")


if __name__ == "__main__":
    unittest.main()

"""The evidence plan: numbered sources, picks kept only when they quote a source, contexts from the cards."""
from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import plan

TIDE = {"kind": "project (personal project)", "name": "Tide gauge", "tools": ["Lua"], "summary": "Logs river levels.",
        "context": "personal", "aliases": [], "role": "", "files": ["Sources/Code/Tide gauge.md"]}
FERRY = {"kind": "project (university team project)", "name": "Ferry planner", "tools": ["Python", "Flask"],
         "summary": "A timetable app built by a team of 7.", "context": "team", "aliases": [],
         "role": "built the statistics features; team of 7", "files": []}
CARDS = [TIDE, FERRY]
FACTS = ("Avery Quillon. BSc Imaginary Studies.\n"
         "Project, Ferry planner (Python, Flask): wrote the statistics features of a timetable app in a team of 7\n"
         "Volunteer guide at a made-up heritage centre, 2024 to 2026")
KEPT = ("Profile (facts about the applicant from their files, each checked by them):\n"
        "- Project (personal project): Tide gauge. Logs river levels. Tools: Lua\n"
        "- Skill: Lua. Shown by: \"the Tide gauge logger\"\n\n"
        "[Sources/Code/Tide gauge.md > Readme]\n(Kind: personal project)\nA logger in Lua that records river levels "
        "every ten minutes, written in my own time.\n\n"
        "[Notes/river.md > Field trip]\nWe measured the river at three sites.")


class Sources(unittest.TestCase):
    def setUp(self):
        self.found = plan.sources(FACTS, KEPT, CARDS)

    def by_text(self, start: str) -> plan.Source:
        return next(s for s in self.found if s.text.startswith(start))

    def test_every_cv_line_profile_line_and_passage_is_a_numbered_source(self):
        self.assertEqual([s.id for s in self.found], [f"S{n}" for n in range(1, 8)])
        self.assertEqual([s.label for s in self.found],
                         ["CV", "CV", "CV", "Profile", "Profile", "Sources/Code/Tide gauge.md > Readme",
                          "Notes/river.md > Field trip"])
        self.assertNotIn("(Kind:", self.by_text("A logger").text, "the Kind line is Hal's, not the passage's")

    def test_a_source_is_tied_to_the_card_it_is_about(self):
        self.assertIs(self.by_text("Project, Ferry planner").card, FERRY)
        self.assertIs(self.by_text("Project (personal project): Tide gauge").card, TIDE)
        self.assertIs(self.by_text("A logger").card, TIDE, "a passage of a card's own file")
        self.assertIsNone(self.by_text("Skill: Lua").card, "a skill line quoting a project is not the project")
        self.assertIsNone(self.by_text("We measured").card)


class SkillsLists(unittest.TestCase):
    """A letter said "I used NumPy and pandas to build" a project whose card never names pandas, on the CV's
    skills line (2026-11-07)."""

    def test_a_skills_list_is_told_from_a_line_about_one_piece_of_work(self):
        for label, text in (("CV", "AI and ML: PyTorch, NumPy, pandas, scikit-learn"), ("CV", "Languages: Lua, SQL"),
                            ("CV", "Tools: Git"), ("Profile", 'Skill: Lua. Shown by: "the Tide gauge logger"')):
            self.assertTrue(plan.skills_line(label, text), text)
        for label, text in (("CV", "Project, Ferry planner (Python, Flask): wrote the statistics features"),
                            ("CV", "Volunteer guide at a made-up heritage centre, 2024 to 2026"),
                            ("Profile", "Project (personal project): Tide gauge. Logs river levels. Tools: Lua")):
            self.assertFalse(plan.skills_line(label, text), text)

    def test_a_pick_from_a_skills_list_is_tied_to_no_project_and_says_so(self):
        found = plan.sources("Tools: Lua, Tide gauge, SQL", "", CARDS)
        self.assertIs(found[1].card, TIDE, "the entry names the card")
        item = plan.checked({"evidence": [{"source": "S2", "quote": "Tools: Tide gauge"}]}, found)[0]
        self.assertEqual((item.card, item.context, item.tools, item.skills), ("", "", (), True))
        self.assertIn("a skills list: what the applicant knows, never which project used it", item.words())


MODULES = ("Second year modules (overall 73.3): Ferry Studies 86, River Trade 79, Software Engineering Team "
           "Project 73, Harbour Records 70")


class Entries(unittest.TestCase):
    """A CV line of every module was one source, so "I achieved 86 in <module>, where I contributed to a team
    project" cited a quote holding another module's "Team Project" (2026-11-08)."""

    def test_each_module_of_a_list_is_its_own_source_with_the_lists_heading(self):
        found = plan.sources(MODULES, "", CARDS)
        self.assertEqual([s.text for s in found],
                         ["Second year modules (overall 73.3): Ferry Studies 86",
                          "Second year modules (overall 73.3): River Trade 79",
                          "Second year modules (overall 73.3): Software Engineering Team Project 73",
                          "Second year modules (overall 73.3): Harbour Records 70"])

    def test_a_pick_across_two_modules_is_dropped_and_one_module_is_its_whole_entry(self):
        found = plan.sources(MODULES, "", CARDS)
        items = plan.checked({"evidence": [
            {"source": "S1", "quote": "Ferry Studies 86, River Trade 79, Software Engineering Team Project 73"},
            {"source": "S1", "quote": "Ferry Studies 86"}]}, found)
        self.assertEqual([i.text for i in items], ["Second year modules (overall 73.3): Ferry Studies 86"])
        self.assertNotIn("Team Project", items[0].words())

    def test_a_skills_list_is_one_source_per_skill_and_each_says_it_is_a_skills_list(self):
        found = plan.sources("AI and ML: PyTorch, NumPy, pandas", "", CARDS)
        self.assertEqual([(s.text, s.skills) for s in found],
                         [("AI and ML: PyTorch", True), ("AI and ML: NumPy", True), ("AI and ML: pandas", True)])
        item = plan.checked({"evidence": [{"source": "S3", "quote": "AI and ML: pandas"}]}, found)[0]
        self.assertTrue(item.skills)

    def test_a_line_about_one_piece_of_work_stays_whole(self):
        for line in (FACTS.splitlines()[1], "Volunteering: Guide at a made-up heritage centre, 2024 to 2026",
                     "Barista, Old Mill Inn (2024 to 2025): served customers, ran the till, trained staff"):
            self.assertEqual([s.text for s in plan.sources(line, "", CARDS)], [line])


class SimilarNames(unittest.TestCase):
    def test_an_item_about_a_card_with_a_namesake_says_its_tag_and_its_own_card(self):
        quill = {**TIDE, "name": "Quill", "files": [], "tag": "offline assistant over personal notes",
                 "similar": ["Quill Employment"]}
        jobs = {**FERRY, "name": "Quill Employment", "tag": "job-search helper", "similar": ["Quill"]}
        found = plan.sources("Project, Quill Employment (Python, Flask): drafts letters for graduates", "",
                             [quill, jobs])
        self.assertIs(found[0].card, jobs, "the longer name is not the shorter one too")
        item = plan.checked({"evidence": [{"source": "S1", "quote": "drafts letters for graduates"}]}, found)[0]
        self.assertIn("about Quill Employment (job-search helper), university team project", item.words())


class PlannedNotDone(unittest.TestCase):
    def test_an_item_quoting_a_plan_is_marked_planned_and_says_so(self):
        # a team's design document planned Docker; the built app used SQLite, and every pack said the person
        # used Docker (2026-11-08)
        kept = ("[Sources/Projects/Ferry planner/Design Document.md > Stack]\nThe app uses Docker and PostgreSQL."
                "\n\n[Sources/Code/ferry/README.md > Storage]\nTimetables are stored in SQLite.\n\n"
                "[Notes/ferry.md > Next]\nWe will use Redis for caching.")
        found = plan.sources("", kept, CARDS)
        picks = [{"source": s.id, "quote": s.text} for s in found]
        items = plan.checked({"evidence": picks}, found)
        self.assertEqual([i.planned for i in items], [True, False, True])
        self.assertIn(plan.PLANNED_WORDS, items[0].words())
        self.assertNotIn(plan.PLANNED_WORDS, items[1].words())


class Picks(unittest.TestCase):
    def setUp(self):
        self.found = plan.sources(FACTS, KEPT, CARDS)

    def test_a_pick_is_kept_only_when_it_quotes_its_source_word_for_word(self):
        items = plan.checked({"evidence": [
            {"source": "S2", "quote": "wrote the statistics  features of a timetable app."},
            {"source": "S2", "quote": "built the React frontend and Flask backend"},   # in no source
            {"source": "S99", "quote": "Volunteer guide at a made-up heritage centre"},  # no such source
            {"source": "S3", "quote": "volunteer guide at a made-up heritage centre"},
            {"source": "S3", "quote": "Volunteer guide at a made-up heritage centre"},  # the same fact twice
            {"source": "S1", "quote": "Avery"},                                         # one word is no evidence
            "S4"]}, self.found)
        self.assertEqual([(i.id, i.text) for i in items],
                         [("E1", "wrote the statistics features of a timetable app"),
                          ("E2", "Volunteer guide at a made-up heritage centre")])

    def test_the_context_and_role_come_from_the_card_never_from_the_reply(self):
        # letters framed projects in the wrong context and wrote team work as solo (2026-11-07)
        items = plan.checked({"evidence": [
            {"source": "S2", "quote": "wrote the statistics features", "context": "personal", "card": "Tide gauge"},
            {"source": "S6", "quote": "A logger in Lua that records river levels", "context": "degree"}]}, self.found)
        self.assertEqual([(i.card, i.context, i.role) for i in items],
                         [("Ferry planner", "team", "built the statistics features; team of 7"),
                          ("Tide gauge", "personal", "")])
        self.assertEqual(items[0].tools, ("Python", "Flask"))

    def test_a_plan_holds_at_most_its_named_number_of_items(self):
        reply = {"evidence": [{"source": "S2", "quote": " ".join(FACTS.splitlines()[1].split()[:n])}
                              for n in range(2, 20)]}
        self.assertEqual(len(plan.checked(reply, self.found)), plan.MAX_ITEMS)

    def test_a_reply_that_is_not_a_plan_gives_none(self):
        for reply in ({}, {"evidence": "S2"}, [], "no"):
            self.assertEqual(plan.checked(reply, self.found), [])

    def test_the_plan_reads_as_its_ids_quotes_contexts_and_roles(self):
        items = plan.checked({"evidence": [{"source": "S2", "quote": "wrote the statistics features"}]}, self.found)
        self.assertEqual(items[0].words(),
                         "E1: \"wrote the statistics features\" (from the CV; about Ferry planner, "
                         "university team project; the applicant's part: built the statistics features; team of 7; "
                         "tools: Python, Flask)")

    def test_one_call_asks_for_the_plan_with_the_requirements_and_every_source(self):
        asked = []

        def reply(system, user, **kw):
            asked.append(user)
            return {"evidence": [{"source": "S7", "quote": "We measured the river at three sites"}]}

        with mock.patch.object(llm, "chat_json", reply):
            items = plan.make(["Field work"], ["Why this role?"], FACTS, KEPT, CARDS)
        self.assertEqual(len(asked), 1)
        self.assertIn("Requirements:\n- Field work\n", asked[0])
        self.assertIn("Questions:\n- Why this role?\n", asked[0])
        self.assertIn("S7 (Notes/river.md > Field trip): We measured the river at three sites.", asked[0])
        self.assertIn(f"Pick at most {plan.MAX_ITEMS}.", asked[0])
        self.assertEqual([i.text for i in items], ["We measured the river at three sites"])


class Stories(unittest.TestCase):
    """One piece of work is one paragraph, told once: drafts retold one project in several paragraphs in 10 of
    15 graded packs (2026-11-08)."""

    def setUp(self):
        found = plan.sources(FACTS, KEPT, [{**TIDE, "quotes": ["Tide gauge has logged since 2024"]}, FERRY])
        self.items = plan.checked({"evidence": [
            {"source": "S2", "quote": "wrote the statistics features of a timetable app", "use": "the reports"},
            {"source": "S6", "quote": "A logger in Lua that records river levels", "use": "sensor data"},
            {"source": "S3", "quote": "Volunteer guide at a made-up heritage centre"},
            {"source": "S5", "quote": "Skill: Lua"},
            {"source": "S4", "quote": "Tide gauge. Logs river levels"}]}, found)
        self.stories = plan.stories(self.items, [{**TIDE, "quotes": ["Tide gauge has logged since 2024"]}, FERRY])

    def test_a_cards_items_are_one_story_and_a_skills_list_is_none(self):
        self.assertEqual([s.key for s in self.stories], ["Ferry planner", "Tide gauge", "CV E3"])
        tide = self.stories[1]
        self.assertEqual(tide.facts, ("A logger in Lua that records river levels", "Tide gauge. Logs river levels",
                                      "Tide gauge has logged since 2024"), "its items first, then its own quotes")
        self.assertEqual((tide.where, tide.use, tide.tools), ("personal project", "sensor data", ("Lua",)))

    def test_a_story_reads_as_plain_sentences_under_its_name(self):
        ferry = self.stories[0].block()
        self.assertEqual(ferry, "Project: Ferry planner\nWhere it was done: university team project\nYour part: built "
                                "the statistics features; team of 7\nWhat it is: A timetable app built by a team of "
                                "7.\nTools: Python, Flask\nFacts:\n- wrote the statistics features of a timetable app"
                                "\nWhat it can help with in this job: the reports")
        self.assertEqual(self.stories[2].block(), "Evidence (no name of its own)\nFacts:\n- Volunteer guide at a "
                                                  "made-up heritage centre")

    def test_each_draft_prefers_work_the_pack_has_not_told(self):
        used: dict[str, int] = {}
        letter = plan.choose(self.stories, plan.LETTER_STORIES, used)
        self.assertEqual([s.key for s in letter], ["Ferry planner", "Tide gauge", "CV E3"])
        first = plan.choose(self.stories, 1, used)
        second = plan.choose(self.stories, 1, used)
        self.assertEqual([first[0].key, second[0].key], ["Ferry planner", "Tide gauge"])

    def test_a_question_asking_for_examples_gets_two_pieces_of_work(self):
        self.assertEqual(plan.examples_wanted("Give two examples of times when you solved a problem."), 2)
        self.assertEqual(plan.examples_wanted("Tell us about a project you are proud of."), 1)


QUILL = {"kind": "project (personal project)", "name": "Quill", "tools": ["Lua"], "context": "personal",
         "summary": "An offline assistant that answers questions over notes.", "aliases": [], "role": "",
         "files": [], "quotes": ["Quill answers questions over a folder of notes with a 40-test suite."]}
MODULES_CV = ("BSc Imaginary Studies, Made-up University, First Class Honours\n"
              "Year 3 modules (overall 81): Algorithm Design 86, Computer Systems 96\n"
              "Final-year Imaginary Studies student seeking a role in backend, systems or applied AI.\n"
              "Project, Ferry planner (Python, Flask): wrote the statistics features of a timetable app in a team of 7")


class ProjectsFirst(unittest.TestCase):
    """A software letter told two modules instead of strong projects and came to 118 words against 250, another
    restated the degree, and one said "I am seeking a role in backend, systems or applied AI" (2026-11-08)."""

    def setUp(self):
        self.found = plan.sources(MODULES_CV, "", [TIDE, FERRY, QUILL])
        self.items = plan.checked({"evidence": [
            {"source": "S1", "quote": "BSc Imaginary Studies, Made-up University, First Class Honours"},
            {"source": "S2", "quote": "Year 3 modules (overall 81): Algorithm Design 86"},
            {"source": "S3", "quote": "Year 3 modules (overall 81): Computer Systems 96"},
            {"source": "S4", "quote": "wrote the statistics features of a timetable app"}]}, self.found)
        self.stories = plan.stories(self.items, [TIDE, FERRY, QUILL])

    def test_what_the_person_is_looking_for_is_never_a_source(self):
        self.assertFalse(any("seeking" in s.text for s in self.found))
        self.assertEqual(plan.without_goal("I build tools. I am seeking a graduate role in data."), "I build tools.")

    def test_the_degree_is_never_a_story_and_a_module_is_a_story_of_its_own(self):
        self.assertEqual([(s.kind, s.name) for s in self.stories],
                         [("module", "Algorithm Design"), ("module", "Computer Systems"), ("project", "Ferry planner")])
        self.assertTrue(self.stories[0].block().startswith("Module: Algorithm Design\n"))

    def test_a_letter_tells_projects_and_fills_with_the_cards_the_job_fits(self):
        told = plan.letter_stories(self.stories, [TIDE, FERRY, QUILL], "Answer questions over internal notes")
        self.assertEqual([s.name for s in told], ["Ferry planner", "Quill"],
                         "a card the job fits joins; Tide gauge shares no word of the job's")
        self.assertEqual(told[1].facts, tuple(QUILL["quotes"]))

    def test_a_module_is_a_paragraph_only_when_too_few_projects_fit(self):
        told = plan.letter_stories(self.stories, [TIDE, FERRY], "Answer questions over internal notes")
        self.assertEqual([s.name for s in told], ["Ferry planner", "Algorithm Design", "Computer Systems"])

    def test_the_strongest_modules_the_job_names_that_no_paragraph_shows_are_offered(self):
        modules = [("Network Security", "91"), ("Applied Cryptography", "88"), ("Security Law", "70"),
                   ("Oral History", "95")]
        self.assertEqual(plan.relevant_modules(modules, "Secure networks and cryptography; security reviews",
                                               "Quill answers questions"),
                         ("Network Security (91)", "Applied Cryptography (88)"))


class LetterLength(unittest.TestCase):
    def test_each_paragraph_takes_a_share_of_the_letters_length(self):
        from jobhunt.writing import paragraphs

        self.assertIn("3 to 4 sentences, 70 to 100 words", paragraphs.letter(325, 3).words)
        self.assertIn("60 to 90 words", paragraphs.letter(200, 3).words, "never under the least")
        self.assertIs(paragraphs.letter(None, 3), paragraphs.LETTER)


if __name__ == "__main__":
    unittest.main()

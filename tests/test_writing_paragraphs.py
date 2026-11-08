"""One paragraph about one piece of work: its checks with no AI, scoped to its own story, the one AI check, the one
redraft, and what is cut, dropped or listed."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import frame, motive, paragraphs, plain, purpose, stage
from jobhunt.writing.motive import Pairing
from jobhunt.writing.plan import Item, Story

# Two look-alike projects (2026-11-08): the job-search helper's test suite and tools went to the assistant.
QUILL = {"kind": "project (personal project)", "name": "Quill", "tools": ["Lua"], "context": "personal",
         "summary": "An offline assistant over personal notes", "aliases": [], "role": "owner", "planned": ["Docker"],
         "tag": "offline assistant over personal notes", "similar": ["Quill Employment"]}
EMPLOYMENT = {"kind": "project (personal project)", "name": "Quill Employment", "tools": ["Preact", "SQLite"],
              "context": "personal", "summary": "A job-search helper for graduates", "aliases": [], "role": "",
              "planned": [], "tag": "job-search helper", "similar": ["Quill"]}
FERRY = {"kind": "project (university team project)", "name": "Ferry planner", "tools": ["Python"], "context": "team",
         "summary": "A timetable app built by a team of 7", "aliases": [], "role": "built the statistics features; "
         "team of 7", "planned": [], "tag": "", "similar": []}
SHOP = {"kind": "job", "name": "Shift supervisor at Made-up Stores", "tools": [], "context": "work",
        "summary": "planned rotas", "aliases": [], "role": "", "planned": [], "tag": "", "similar": []}
CARDS = (QUILL, EMPLOYMENT, FERRY, SHOP)
QUILL_STORY = Story("Quill", "Quill", "personal project", "personal", "owner", QUILL["summary"], ("Lua",),
                    ("Quill answers questions over my notes offline",), (), ("Docker",), "answering staff questions",
                    QUILL["tag"], (), QUILL)
EMPLOYMENT_STORY = Story("Quill Employment", "Quill Employment", "personal project", "personal", "",
                         EMPLOYMENT["summary"], ("Preact", "SQLite"),
                         ("Quill Employment has a 113-test suite",), card=EMPLOYMENT)
FERRY_STORY = Story("Ferry planner", "Ferry planner", "university team project", "team", FERRY["role"],
                    FERRY["summary"], ("Python",), ("wrote the statistics features of Ferry planner",), card=FERRY)
SHOP_STORY = Story("Shift supervisor at Made-up Stores", SHOP["name"], "a job", "work", facts=("planned rotas for 30 "
                   "staff",), card=SHOP)
NOW = stage.Stage(date(2026, 11, 8), "BA History", "2026-06", True, "available now", "History",
                  "Made-up University", "First Class Honours")
ADVERT = "Build internal tools in Python with a small team. You will maintain our ticket triage service."
SCOPE = paragraphs.Scope((QUILL_STORY, EMPLOYMENT_STORY, FERRY_STORY, SHOP_STORY), CARDS,
                         ("Lua", "Preact", "SQLite", "Python", "Docker", "Go", "Rust"), ADVERT, ADVERT, "",
                         ("Graduate Developer", "Brightwater Tools"), NOW,
                         values={"name": "Avery Quillon", "tone": "plain", "spelling": "UK English",
                                 "avoid_phrases": "none"})


def said(sentence: str, story: Story = QUILL_STORY) -> list[str]:
    """What the checks with no AI find in one sentence of a paragraph about `story`."""
    return paragraphs.sentence_problems(sentence, story, SCOPE)


class Scoped(unittest.TestCase):
    """Every check reads one story's facts alone, so a fact of another project is a crossed project."""

    def test_another_projects_tool_or_number_is_named_with_whose_it_is(self):
        self.assertEqual(said("I gave Quill a SQLite store."), ["SQLite is Quill Employment's, not Quill's"])
        self.assertEqual(said("Quill has a 113-test suite."), ["the number 113 is not in the facts of Quill"])
        self.assertEqual(said("I gave Quill Employment a SQLite store.", EMPLOYMENT_STORY), [])
        self.assertEqual(said("I wrote Quill in Rust."), ["Rust is not in the facts of Quill"])

    def test_naming_another_project_is_a_crossed_project_but_a_longer_name_is_not_its_namesake(self):
        self.assertEqual(said("Quill shares code with Ferry planner."),
                         ["names Ferry planner, which is another piece of work; this paragraph is about Quill"])
        self.assertEqual(said("Quill Employment checks every letter.", EMPLOYMENT_STORY), [])

    def test_a_tool_only_a_plan_gives_is_said_as_planned_or_not_at_all(self):
        self.assertEqual(said("I deployed Quill with Docker."),
                         ["Docker was only planned for Quill; say it was planned, or leave it out"])
        self.assertEqual(said("Quill was planned to run in Docker."), [])

    def test_a_teams_work_needs_your_part_and_a_place_never_changes(self):
        self.assertEqual(said("I built Ferry planner in Python.", FERRY_STORY),
                         ["says you did Ferry planner alone; it was a team's work; your part: built the statistics "
                          "features"])
        self.assertEqual(said("In a team of seven, I built the statistics features of Ferry planner.", FERRY_STORY),
                         [])
        self.assertEqual(said("My degree included building Quill."),
                         ["calls Quill part of a degree; its facts say a personal project"])

    def test_your_part_is_never_a_label_and_tools_are_never_a_list(self):
        # "As the owner, I built ..." leaked the card's Role field in the prototype (2026-11-08)
        self.assertEqual(said("As the owner, I built Quill in Lua."),
                         ['uses your part as a label ("As the owner"); say what you did'])
        self.assertIn(paragraphs.LIST_SAID, said("My toolkit includes Lua, Preact, SQLite and Python.",
                                                 Story("cv", "", "", facts=("Lua, Preact, SQLite and Python",))))

    def test_a_course_that_has_ended_is_never_spoken_of_as_running(self):
        self.assertEqual(said("I am finishing my course alongside Quill."),
                         [stage.ended_said(NOW)])

    def test_the_stock_links_to_the_job_and_the_adverts_words_are_found(self):
        # the X2 lead-in cut left garbled text, and the model wrote the same move mid-sentence (2026-11-08)
        for link in ("satisfies the requirement for clear notes", "directly supports the requirement for tools",
                     "addresses the need for search", "aligns directly with your work", "resonates with your team",
                     "matters for the role", "demonstrates my ability to build tools", "meets the need for speed"):
            found = said(f"Quill {link}.")
            self.assertTrue(any(f.startswith("ties the work to the job with a stock phrase") for f in found), link)
        self.assertEqual(said("Quill is how I would maintain your ticket triage service."),
                         [paragraphs.ECHO_SAID])

    def test_a_job_is_named_by_its_title_or_its_employer(self):
        shape = paragraphs.LETTER
        good = "At Made-up Stores I planned rotas for 30 staff. It taught me to plan ahead."
        self.assertEqual(paragraphs.problems(good, SHOP_STORY, SCOPE, shape), [])
        bad = paragraphs.problems("I planned rotas for 30 staff. It taught me to plan ahead.", SHOP_STORY, SCOPE,
                                  shape)
        self.assertEqual(bad, [paragraphs.Problem("", "does not name Shift supervisor at Made-up Stores in its first "
                                                      "sentence")])

    def test_a_sentence_said_twice_and_a_one_sentence_letter_paragraph_are_problems(self):
        text = "Quill answers questions over my notes offline. Quill answers questions over all my notes offline."
        found = paragraphs.problems(text, QUILL_STORY, SCOPE, paragraphs.LETTER)
        self.assertEqual([p.reason for p in found], [paragraphs.REPEAT_SAID])
        found = paragraphs.problems("Quill answers questions over my notes offline.", QUILL_STORY, SCOPE,
                                    paragraphs.LETTER)
        self.assertEqual([p.reason for p in found], [paragraphs.TOO_SHORT])


FLAGS = json.loads((Path(__file__).parent / "fixtures" / "paragraph_flags.json").read_text(encoding="utf-8"))
FIXTURE_CARDS = {k: {"kind": "project", "name": v["name"], "tools": v["tools"], "summary": "", "context": v["context"],
                     "aliases": [], "role": v["role"], "tag": "", "similar": []}
                 for k, v in FLAGS["stories"].items() if v["name"]}
FIXTURE_STORIES = {k: Story(k, v["name"], "", v["context"], v["role"], "", tuple(v["tools"]), tuple(v["facts"]),
                            card=FIXTURE_CARDS.get(k)) for k, v in FLAGS["stories"].items()}
FIXTURE_SCOPE = paragraphs.Scope(tuple(FIXTURE_STORIES.values()), tuple(FIXTURE_CARDS.values()),
                                 tuple(FLAGS["known"]), FLAGS["allowed"], modules=("Databases",))


class Fixture(unittest.TestCase):
    """Five graded packs listed about 20 false alarms for 10 real problems (2026-11-07); each kind is in
    `fixtures/paragraph_flags.json`, scoped to the story its paragraph tells."""

    def test_every_sentence_of_the_fixture_is_judged_right_with_no_ai(self):
        wrong = [s["text"] for s in FLAGS["sentences"]
                 if bool(paragraphs.sentence_problems(s["text"], FIXTURE_STORIES[s["story"]], FIXTURE_SCOPE))
                 != s["flagged"]]
        self.assertEqual(wrong, [], f"{len(wrong)} judged wrong of {len(FLAGS['sentences'])}")

    def test_what_only_the_ai_can_see_is_left_to_it_and_found_there(self):
        for s in FLAGS["ai_only"]:
            story = FIXTURE_STORIES[s["story"]]
            self.assertEqual(paragraphs.sentence_problems(s["text"], story, FIXTURE_SCOPE), [], s["text"])
            with mock.patch.object(llm, "chat_json", lambda system, user, reply=s["reply"], **kw: reply):
                self.assertTrue(paragraphs.verify(s["text"], story, FIXTURE_SCOPE), s["text"])


class FakeAI:
    """Answers paragraph requests with `drafts` in turn (the last repeated) and checks with `verdict`."""

    def __init__(self, drafts: list[str], verdict: dict | None = None):
        self.drafts, self.verdict, self.asked = list(drafts), verdict, []

    def chat_json(self, system: str, user: str, **kw) -> dict:
        self.asked.append((system.split()[:3], user))
        if system.startswith("Check one paragraph"):
            return self.verdict or {"supported": True, "unsupported": [], "context_ok": True, "role_ok": True}
        if system.startswith("Write the first"):
            text = self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]
            return {"opening": text, "closing": "Could we talk about the role?"}
        return {"paragraph": self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]}

    def calls(self, first: str) -> list[str]:
        return [u for s, u in self.asked if s[0] == first]


GOOD = "Quill is my offline assistant over personal notes, in Lua. It answers questions over my notes offline."


class Settle(unittest.TestCase):
    def settle(self, fake: FakeAI, story: Story = QUILL_STORY) -> paragraphs.Paragraph:
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            return paragraphs.settle(story, paragraphs.LETTER, SCOPE)

    def test_a_paragraph_saying_only_its_facts_needs_no_check_and_no_redraft(self):
        fake = FakeAI([GOOD])
        done = self.settle(fake)
        self.assertEqual((done.text, done.calls, done.redrafted, done.listed), (GOOD, 1, False, ""))
        request = fake.calls("Write")[0]
        self.assertIn("Project: Quill (offline assistant over personal notes)\nWhere it was done: personal project\n"
                      "Your part: owner", request)
        self.assertNotIn("SQLite", request)

    def test_a_failing_paragraph_is_drafted_again_once_with_its_problems(self):
        fake = FakeAI([f"I gave Quill a SQLite store. {GOOD}", GOOD])
        done = self.settle(fake)
        again = fake.calls("Write")[1]
        self.assertIn('- "I gave Quill a SQLite store.": SQLite is Quill Employment\'s, not Quill\'s', again)
        self.assertEqual((done.text, done.redrafted, done.listed), (GOOD, True, ""))

    def test_a_sentence_still_failing_is_cut_when_the_rest_reads(self):
        fake = FakeAI([f"{GOOD} I gave it a SQLite store."])
        done = self.settle(fake)
        self.assertEqual((done.text, done.cut, done.listed), (GOOD, 1, ""))

    def test_a_paragraph_that_cannot_lose_its_failing_sentence_is_dropped_and_listed(self):
        fake = FakeAI(["I gave Quill a SQLite store. It answers questions over my notes offline."])
        done = self.settle(fake)
        self.assertEqual(done.text, "")
        self.assertEqual(done.listed, "Hal left out the paragraph about Quill: SQLite is Quill Employment's, not "
                                      "Quill's. Add it yourself if it is true")

    def test_words_the_facts_hold_are_dropped_from_the_ai_check_and_the_rest_redrafted(self):
        verdict = {"supported": False, "unsupported": ["answers questions over my notes", "on a Raspberry Pi"],
                   "context_ok": True, "role_ok": True}
        text = "Quill is my offline assistant over personal notes, built in Lua. It runs on a Raspberry Pi."
        fake = FakeAI([text, GOOD], verdict)
        done = self.settle(fake)
        again = fake.calls("Write")[1]
        self.assertIn('- "It runs on a Raspberry Pi.": its facts do not say: "on a Raspberry Pi"', again)
        self.assertNotIn("answers questions over my notes\"", again)
        self.assertEqual(done.calls, 3, "a draft, its check, and the redraft that says only its facts")

    def test_nothing_to_say_is_dropped_with_no_check(self):
        done = self.settle(FakeAI([""]))
        self.assertEqual((done.text, done.calls, done.dropped), ("", 1, paragraphs.NOTHING))


class Frame(unittest.TestCase):
    FACTS = frame.Facts("Avery Quillon", "a first-class History graduate from Made-up University (2026)",
                             "Graduate Developer", "Brightwater Tools",
                             Pairing("ticket triage service", Item("E1", "Quill answers questions", "CV"),
                                     "both sort questions"), ("Quill",), "Made-up University")

    def settle(self, fake: FakeAI) -> frame.Framed:
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            return frame.settle(self.FACTS, SCOPE)

    def test_the_opening_is_drafted_from_who_is_writing_the_role_and_the_reason(self):
        good = ("I am Avery Quillon, a first-class History graduate from Made-up University, and I am applying "
                "for the Graduate Developer role. Sorting questions is what drew me to your triage work.")
        fake = FakeAI([good])
        done = self.settle(fake)
        self.assertEqual((done.opening, done.closing, done.calls), (good, "Could we talk about the role?", 1))
        request = fake.calls("Write")[0]
        self.assertIn('The reason for this employer: the employer\'s "ticket triage service" and the applicant\'s '
                      '"Quill answers questions", because both sort questions', request)
        self.assertIn("The letter's middle tells (never retell them): Quill", request)

    def test_an_opening_without_who_is_writing_falls_back_to_the_built_sentence(self):
        fake = FakeAI(["I am applying for the Graduate Developer role, as I am finishing my degree."])
        done = self.settle(fake)
        self.assertEqual(done.calls, 2, "one redraft, never a loop")
        self.assertIn("does not say who is writing", fake.calls("Write")[1])
        self.assertEqual(done.opening, "I'm Avery Quillon, a first-class History graduate from Made-up University "
                                       "(2026), applying for the Graduate Developer role.")


class Reframed(unittest.TestCase):
    """Facts were reframed into claims the record never makes: Docker "to package the application" when it was
    an agent sandbox, and a module "gave me the specific C++ knowledge" when it never names C++ (2026-11-08)."""

    def test_a_tools_purpose_its_facts_never_say_is_a_problem(self):
        story = Story("Quill", "Quill", "personal project", "personal", "", QUILL["summary"], ("Lua", "Docker"),
                      ("Quill runs each plugin in a Docker sandbox",), card=QUILL)
        found = said("I used Docker to package Quill for consistent deployment.", story)
        self.assertEqual(found, [purpose.SAID.format(tool="Docker", purpose="package Quill")])
        self.assertEqual(said("I used Docker to sandbox each plugin of Quill.", story), [])

    def test_a_module_claim_naming_a_tool_is_found_in_an_answer_and_drafted_again(self):
        scope = replace(SCOPE, modules=("Algorithm Design",), known=(*SCOPE.known, "C++"),
                        sources=("Year 2 modules: Algorithm Design 70, Ferry Studies 64",))
        claim = "Quill answers questions over my notes offline. Algorithm Design gave me the C++ knowledge for it."
        fake = FakeAI([claim, GOOD])
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            done = paragraphs.settle(QUILL_STORY, paragraphs.answer("Tell us about your skills."), scope)
        self.assertIn("nothing in your facts ties Algorithm Design to C++", fake.calls("Write")[1])
        self.assertEqual(done.text, GOOD)


class AdvertPhrases(unittest.TestCase):
    """"low-latency systems" and "AI agents for customer support" went from the advert into the person's claims
    (2026-11-08)."""

    SCOPE = replace(SCOPE, advert="You will build low-latency systems and AI agents for customer support.",
                    allowed="You will build low-latency systems and AI agents for customer support.")

    def reasons(self, sentence: str, shape: paragraphs.Shape) -> list[str]:
        return [p.reason for p in paragraphs.problems(sentence, QUILL_STORY, self.SCOPE, shape)]

    def test_an_advert_phrase_fails_a_sentence_about_your_work_in_every_shape(self):
        claim = "Quill answers questions over my notes offline, like AI agents for customer support."
        lifted = paragraphs.LIFTED_SAID.format(phrase="ai agents for customer support")
        for shape in (paragraphs.LETTER, paragraphs.answer("Tell us about a project."), paragraphs.message("Hi.")):
            self.assertIn(lifted, self.reasons(claim, shape), shape.words[:40])

    def test_the_adverts_words_are_fine_about_the_employer(self):
        said = ("Quill answers questions over my notes offline. How do your AI agents for customer support find "
                "their answers?")
        self.assertFalse([r for r in self.reasons(said, paragraphs.message("Hi.")) if "from the advert" in r])


class Retold(unittest.TestCase):
    """The opening named the pairing's project with its facts, then a paragraph told them again (2026-11-08)."""

    BODY = "Quill answers questions over my notes offline, in Lua. It has 40 saved searches."
    FACTS = frame.Facts("Avery Quillon", "a first-class History graduate from Made-up University (2026)",
                        "Graduate Developer", "Brightwater Tools",
                        Pairing("ticket triage service", Item("E1", "Quill answers questions", "CV", card="Quill"),
                                "both sort questions"), ("Quill",), "Made-up University", middle=(("Quill", BODY),))
    GOOD = ("I am Avery Quillon, a first-class History graduate from Made-up University (2026), applying for the "
            "Graduate Developer role. Building Quill to sort questions drew me to your ticket triage service.")

    def test_an_opening_retelling_a_paragraphs_facts_is_a_problem_and_a_short_clause_is_not(self):
        retold = ("I am applying for the Graduate Developer role, as Quill, which answers questions over my notes "
                  "offline, drew me to your ticket triage service.")
        self.assertEqual(frame.retold(retold, self.FACTS), ["Quill"])
        self.assertEqual(frame.retold("My Quill keeps 40 saved searches for your triage work.", self.FACTS),
                         ["Quill"], "a number the paragraph gives")
        self.assertEqual(frame.retold(self.GOOD.split(". ")[1], self.FACTS), [])
        self.assertEqual(frame.retold("I graduated from Made-up University in 2026.", self.FACTS), [],
                         "who is writing is the opening's own")

    def test_the_frame_is_drafted_once_more_without_the_retelling(self):
        retold = (f"{self.GOOD.split('. ')[0]}. Quill answers questions over my notes offline, which drew me to "
                  "your ticket triage service.")
        fake = FakeAI([retold, self.GOOD])
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            done = frame.settle(self.FACTS, SCOPE)
        asked = fake.calls("Write")
        self.assertIn(frame.TOLD_WORK.format(work="Quill"), asked[0])
        self.assertIn(frame.RETOLD_SAID.format(work="Quill"), asked[1])
        self.assertEqual((done.opening, done.calls), (self.GOOD, 2))


class Named(unittest.TestCase):
    """Passages of a dissertation were drafted as "I designed a pure simulation engine ..." with no project
    named (2026-11-08)."""

    def test_a_story_with_no_card_must_name_its_project_in_the_first_sentence(self):
        story = Story("Sources/Dissertation/Traffic flow simulation.md > Design", "Traffic flow simulation",
                      "degree work", "degree", facts=("I designed a pure simulation engine.",), kind="project")
        unnamed = paragraphs.problems("I designed a pure simulation engine. It runs fast.", story, SCOPE,
                                      paragraphs.LETTER)
        self.assertIn(paragraphs.NOT_NAMED.format(card="Traffic flow simulation"), [p.reason for p in unnamed])
        named = paragraphs.problems("For my Traffic flow simulation I designed a pure simulation engine. It "
                                    "runs on its own.", story, SCOPE, paragraphs.LETTER)
        self.assertNotIn(paragraphs.NOT_NAMED.format(card="Traffic flow simulation"), [p.reason for p in named])

    def test_answers_and_the_message_name_their_work_in_the_first_sentence_too(self):
        # 12 of 15 graded packs had an answer or message opening "I wrote a 113-test suite ..." (2026-11-08)
        unnamed = ("I wrote a 113-test suite for a job-search helper. That was Quill Employment, and the tests "
                   "ran on every change. How does your team test its tools?")
        named = ("Quill Employment has a 113-test suite that I wrote. The tests ran on every change. How does your "
                 "team test its tools?")
        missing = paragraphs.NOT_NAMED.format(card="Quill Employment")
        for shape in (paragraphs.answer("Tell us about testing."), paragraphs.answer("Give two examples.", 1, 2),
                      paragraphs.why("Why do you want to work here?", None), paragraphs.message("I'm Avery.")):
            found = [p.reason for p in paragraphs.problems(unnamed, EMPLOYMENT_STORY, SCOPE, shape)]
            self.assertIn(missing, found, shape.words[:40])
            found = [p.reason for p in paragraphs.problems(named, EMPLOYMENT_STORY, SCOPE, shape)]
            self.assertNotIn(missing, found, shape.words[:40])

    def test_an_answer_not_naming_its_work_first_is_drafted_again_once(self):
        unnamed = "I wrote a 113-test suite. It was for Quill Employment."
        fake = FakeAI([unnamed, "Quill Employment has a 113-test suite."])
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            done = paragraphs.settle(EMPLOYMENT_STORY, paragraphs.answer("Tell us about testing."), SCOPE)
        self.assertIn("does not name Quill Employment in its first sentence", fake.calls("Write")[1])
        self.assertEqual((done.text, done.redrafted), ("Quill Employment has a 113-test suite.", True))


class MessageBody(unittest.TestCase):
    def test_a_message_body_asks_one_question(self):
        # a message had nothing after its introduction (2026-11-08); its body tells the work and asks
        text = "Quill answers questions over my notes in Lua. It runs offline."
        found = [p.reason for p in paragraphs.problems(text, QUILL_STORY, SCOPE, paragraphs.MESSAGE)]
        self.assertIn(paragraphs.NO_QUESTION, found)
        asked = [p.reason for p in paragraphs.problems(f"{text} How does your team search its notes?", QUILL_STORY,
                                                       SCOPE, paragraphs.MESSAGE)]
        self.assertNotIn(paragraphs.NO_QUESTION, asked)


class DegreeAgain(unittest.TestCase):
    """A message's built opening gave the degree and its next sentence gave it again (2026-11-08)."""

    AFTER = ("I'm Avery Quillon, a first-class Software Studies graduate from Made-up University (2026), applying "
             "for the Graduate Developer role.")

    def reasons(self, text: str) -> list[str]:
        return [p.reason for p in paragraphs.problems(text, QUILL_STORY, SCOPE, paragraphs.message(self.AFTER))]

    def test_a_sentence_saying_the_degree_again_is_drafted_again(self):
        self.assertIn(paragraphs.SAID_AGAIN, self.reasons(
            "As a Software Studies graduate I built Quill in Lua. How does your team search its notes?"))

    def test_a_fact_of_the_work_after_the_opening_is_fine(self):
        self.assertNotIn(paragraphs.SAID_AGAIN, self.reasons(
            "I built Quill in Lua as part of my degree. How does your team search its notes?"))


class Listed(unittest.TestCase):
    def test_a_course_story_with_nothing_to_say_is_never_listed(self):
        # "Hal left out the paragraph about First-Class Honours: your facts gave nothing" was most of the false
        # check items (2026-11-08)
        for story in (Story("cv", "First-Class Honours", "", kind="project"),
                      Story("Module Oral History", "Oral History", "", kind="module")):
            self.assertEqual(paragraphs.Paragraph(story, dropped=paragraphs.NOTHING).listed, "", story.name)
        self.assertTrue(paragraphs.Paragraph(QUILL_STORY, dropped=paragraphs.NOTHING).listed)
        self.assertTrue(paragraphs.Paragraph(Story("m", "Oral History", "", kind="module"), dropped="x").listed)


class Stock(unittest.TestCase):
    def test_plain_finds_a_stock_link_but_never_a_plain_sentence(self):
        self.assertTrue(plain.problems("This aligns with your team's work."))
        self.assertEqual(plain.problems("I lined up the shelves with care."), [])

    def test_a_motive_said_as_matching_my_experience_is_stock(self):
        # openings said the role "matches my experience" or "fits my recent work" (2026-11-08)
        for said in ("The role matches my experience.", "Your work fits my recent projects.",
                     "This is in line with my studies.", "The team suits my background well."):
            self.assertTrue(plain.problems(said), said)


class Motive(unittest.TestCase):
    """Openings gave "matches my experience" and reasons such as "because I have built features for a team
    project" (2026-11-08)."""

    FACTS = frame.Facts("Avery Quillon", "a History graduate", "Graduate Developer", "Brightwater Tools",
                        Pairing("ticket triage service", Item("E1", "Quill answers questions", "CV", card="Quill"),
                                "both sort questions"), ("Quill",), "")

    def test_the_reason_names_the_work_and_one_thing_of_the_employers(self):
        self.assertEqual(frame.motive_problems("I am applying for the Graduate Developer role, as I enjoy coding.",
                                               self.FACTS),
                         [frame.MOTIVE_WORK.format(work="Quill"),
                          frame.MOTIVE_EMPLOYER.format(fact="ticket triage service")])
        self.assertEqual(frame.motive_problems("Building Quill to sort questions drew me to your triage work.",
                                               self.FACTS), [])

    def test_a_pairing_rests_on_a_piece_of_work_while_the_plan_has_one(self):
        cv, work = Item("E1", "Final-year student", "CV"), Item("E2", "Quill answers questions", "CV", card="Quill")
        self.assertEqual(motive.offered([cv, work]), [work])
        self.assertEqual(motive.offered([cv]), [cv])


if __name__ == "__main__":
    unittest.main()

"""What a tool was for: a purpose a sentence gives a tool must be in the facts that name that tool.

The projects and facts are made up, look-alikes of a graded pack's (2026-11-08): "used Docker to package the
application ... consistent deployment" when the record's Docker was an agent sandbox, and frontend work called
backend work.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import purpose

KNOWN = ["Docker", "React", "FastAPI", "Flask", "Python", "C++", "Lua"]
SANDBOX = ["Wrenbot runs each agent's code in a Docker sandbox", "Wrenbot has a React dashboard for the frontend"]
ABOUT = "Wrenbot\nA helper that runs coding agents safely"


def found(sentence: str, tools: list[str], facts: list[str] = SANDBOX, about: str = ABOUT) -> list[str]:
    return purpose.unbacked(sentence, tools, facts, about, KNOWN)


class Purposes(unittest.TestCase):
    def test_a_purpose_no_fact_naming_the_tool_holds_is_found(self):
        said = "I used Docker to package the application for consistent deployment."
        self.assertEqual(found(said, ["Docker"]), [purpose.SAID.format(tool="Docker", purpose="package the "
                                                                         "application")])
        self.assertTrue(found("I wrote the backend in React.", ["React"]), "frontend work called backend work")
        self.assertTrue(found("I used React for the backend API.", ["React"]))

    def test_a_purpose_the_tools_own_facts_or_the_work_itself_hold_is_fine(self):
        for said in ("I used Docker to sandbox each agent's code.", "I built a React dashboard for the frontend.",
                     "I used Python to build a helper that runs coding agents safely."):
            with self.subTest(said):
                self.assertEqual(found(said, ["Docker", "React", "Python"]), [])

    def test_a_sentence_with_no_purpose_or_a_change_of_tools_states_none(self):
        for said in ("I built Wrenbot in Python.", "I used Lua to build Wrenbot.", "I moved Wrenbot from Flask to "
                     "FastAPI.", "Docker and React were both new to me."):
            with self.subTest(said):
                self.assertEqual(found(said, ["Python", "Lua", "Flask", "Docker", "React"]), [])

    def test_a_purpose_another_fact_of_the_work_holds_is_fine(self):
        # "the daemon in Python" was flagged and a true paragraph dropped, as no fact naming Python said
        # "daemon" (2026-11-08)
        facts = ["Gullwing is a daemon that parks low-priority jobs", "Written in Python with SQLite"]
        for said in ("I wrote the daemon in Python.", "I used SQLite to park low-priority jobs."):
            with self.subTest(said):
                self.assertEqual(found(said, ["Python", "SQLite"], facts, "Gullwing"), [])

    def test_a_part_of_a_system_needs_a_fact_naming_the_tool(self):
        facts = ["Wrenbot has a Flask backend", "Wrenbot has a React dashboard"]
        self.assertTrue(found("I used React for the backend.", ["React"], facts))
        self.assertEqual(found("I used Flask for the backend.", ["Flask"], facts), [])

    def test_a_purpose_word_counts_by_its_stem(self):
        facts = ["Gullwing packages its route planner in Docker images"]
        self.assertEqual(found("I used Docker for packaging the planner.", ["Docker"], facts, "Gullwing"), [])


if __name__ == "__main__":
    unittest.main()

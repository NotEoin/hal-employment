"""Hal's instructions to the AI name no one: each person is described by their settings.

Every prompt is rendered for each of the three made-up people of `support.people`. A rendered prompt may
hold no unfilled placeholder, no pronoun for the applicant, no other person's name, and none of the
person's LEAKS words: the words of other careers that must not reach their prompts (for example, the
humanities graduate's list holds "software engineer"). The fixtures themselves are checked to be made up.
"""
import re
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people

from jobhunt import pack, screen
from jobhunt.ai import prompts
from jobhunt.find import goal as goal_mod

PRONOUN = re.compile(r"\b(he|him|his)\b", re.I)
TESTS = Path(__file__).resolve().parent
# Addresses that can never belong to a real person: the reserved example domains and test top-level names.
RESERVED_MAIL = re.compile(r"@([\w-]+\.)*(example\.(org|com|net)|example|invalid|test)$", re.I)
MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# UK mobile numbers set aside for drama and examples by Ofcom: 07700 900000 to 900999.
DRAMA_PHONE = re.compile(r"^07700 ?900\d{3}$")
PHONE = re.compile(r"\b07\d{3} ?\d{6}\b")
FIXTURES = ("*.py", "*.html", "*.txt", "*.json")


def rendered(person: people.Person) -> dict[str, str]:
    """Every prompt filled in for `person`, by id."""
    values = pack.person()
    job = {"eligibility": None}
    values.update({k: v for k, v in screen.values(goal_mod.current(), job).items() if k not in values})
    return {pid: prompts.text(pid, **values) for pid in prompts.all_prompts()}


class Neutral(unittest.TestCase):
    def test_prompts_name_no_one_and_take_the_person_from_the_profile(self):
        for person in people.PEOPLE:
            others = [p for p in people.PEOPLE if p is not person]
            with people.as_person(person):
                texts = rendered(person)
            self.assertIn(person.you["name"], texts["letter"])
            self.assertIn(person.you["preferred_name"], texts["prep"])
            for pid, text in texts.items():
                with self.subTest(person=person.key, prompt=pid):
                    self.assertIsNone(prompts.PLACEHOLDER.search(text), "a placeholder was left unfilled")
                    self.assertIsNone(PRONOUN.search(text))
                    for word in (*person.leaks, *(n for other in others for n in other.names)):
                        self.assertNotIn(word.lower(), text.lower())

    def test_a_prompt_with_no_name_set_calls_the_person_the_applicant(self):
        self.assertIn("from the applicant", prompts.text("letter", **pack.person()))

    def test_fixtures_use_only_made_up_people(self):
        """Every email address in the tests is on a reserved domain and every mobile number is a drama number."""
        found_mail, found_phone = set(), set()
        for pattern in FIXTURES:
            for path in TESTS.rglob(pattern):
                if "vendor" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                found_mail |= {m for m in MAIL.findall(text) if not RESERVED_MAIL.search(m)}
                found_phone |= {p for p in PHONE.findall(text) if not DRAMA_PHONE.match(p)}
        self.assertEqual(sorted(found_mail), [])
        self.assertEqual(sorted(found_phone), [])
        for person in people.PEOPLE:
            self.assertRegex(person.you["email"], RESERVED_MAIL)
            self.assertRegex(person.you["phone"], DRAMA_PHONE)


if __name__ == "__main__":
    unittest.main()

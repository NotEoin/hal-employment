"""The daily summary and the spoken briefing: who they greet and who they are sent to.

The briefing greets the person by the name they chose in Settings (`you.preferred_name`), and by no name
when they have given none. Everything runs in the made-up world of `support.world`.
"""
import re
import unittest

from support import people
from support.world import World

from jobhunt import digest
from jobhunt.core import settings

GREETING = re.compile(r"^Good (morning|afternoon|evening)(, [^.]+)?\.")


class Spoken(unittest.TestCase):
    def test_the_summary_greets_the_person_by_their_chosen_name(self):
        with World() as w, people.as_person(people.TECHNICAL):
            self.assertRegex(digest.spoken(w.con), r"^Good (morning|afternoon|evening), Toby\.")
            settings.set("you.preferred_name", "Tobes", by="you")
            self.assertRegex(digest.spoken(w.con), r"^Good (morning|afternoon|evening), Tobes\.")

    def test_with_no_name_set_the_greeting_names_no_one(self):
        with World() as w:
            greeting = GREETING.match(digest.spoken(w.con))
        self.assertIsNotNone(greeting)
        self.assertIsNone(greeting.group(2))


if __name__ == "__main__":
    unittest.main()

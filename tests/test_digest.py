"""The daily summary and the spoken briefing: who they greet and who they are sent to.

The briefing greets the person by the name they chose in Settings (`you.preferred_name`), and by no name
when they have given none. Everything runs in the made-up world of `support.world`.
"""
import re
import unittest
from unittest import mock

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


class Recipient(unittest.TestCase):
    INBOX = "made-up.inbox@example.org"

    def test_the_summary_goes_to_the_address_you_chose(self):
        with World():
            settings.set("notify.daily_to", "rowan.made-up@example.org", by="you")
            self.assertEqual(digest.recipient(self.INBOX), "rowan.made-up@example.org")

    def test_the_summary_is_never_sent_to_a_list_of_addresses(self):
        with World():
            settings.set("notify.daily_to", "rowan.made-up@example.org", by="you")
            with mock.patch.object(digest.settings, "get", lambda key: "a@example.org, b@example.org"):
                with self.assertRaises(SystemExit):
                    digest.recipient(self.INBOX)

    def test_an_address_hal_set_is_refused(self):
        with World():
            settings.set("notify.daily_to", "someone.else@example.org", by="hal")
            with self.assertRaises(SystemExit):
                digest.recipient(self.INBOX)


if __name__ == "__main__":
    unittest.main()

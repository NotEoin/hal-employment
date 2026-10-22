"""The job sources search where the person's goal says, and name no place of their own."""
import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import sources
from jobhunt.core import settings
from jobhunt.sources import aggregators, hn


class Places(unittest.TestCase):
    def tearDown(self):
        settings.reset("goal.places", by="you")

    def test_searches_follow_the_goals_places_in_order(self):
        settings.set("goal.places", ["Harrowgate Vale", "Lowmere"], by="you")
        self.assertEqual(sources.places(), ["Harrowgate Vale", "Lowmere"])
        asked = []
        with mock.patch.object(aggregators, "_key", return_value="made-up-id:made-up-key"), \
                mock.patch.object(aggregators, "QUERIES", ["museum assistant"]), \
                mock.patch.object(aggregators, "get", lambda url, timeout: asked.append(url) or json.dumps({})):
            aggregators.adzuna()
        self.assertEqual(len(asked), 2)
        self.assertIn("where=Harrowgate+Vale", asked[0])
        self.assertIn("where=Lowmere", asked[1])

    def test_a_goal_with_no_places_searches_the_default(self):
        self.assertEqual(sources.places(), list(sources.DEFAULT_PLACES))

    def test_hacker_news_keeps_posts_naming_the_uk_or_a_goal_place(self):
        settings.set("goal.places", ["Lowmere"], by="you")
        uk = hn.uk_pattern()
        self.assertTrue(uk.search("Acme | Engineer | Lowmere, onsite"))
        self.assertTrue(uk.search("Acme | Engineer | Remote (UK)"))
        self.assertFalse(uk.search("Acme | Engineer | Berlin"))


if __name__ == "__main__":
    unittest.main()

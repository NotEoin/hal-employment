"""Company checks: only an article or a registration that names the whole employer is shown as the employer.

The web is faked: `reputation.get` answers with made-up Wikipedia search results and a made-up Companies
House results page, so no test leaves the machine.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import reputation

EMPLOYER = "Riverside Heritage Trust"   # the made-up humanities advert's employer


def fake_get(titles: list[str], registrations: list[str]):
    """A stand-in for `reputation.get` that knows these article titles and registration lines."""
    def get(url: str, **kw) -> str:
        if url.startswith(reputation.COMPANIES_HOUSE):
            return "".join(f'<li class="type-company"><a>{r}</a></li>' for r in registrations)
        if "list=search" in url:
            return json.dumps({"query": {"search": [{"title": t} for t in titles]}})
        if "prop=sections" in url:
            return json.dumps({"parse": {"sections": []}})
        return json.dumps({"query": {"pages": {"1": {"extract": "A made-up summary."}}}})
    return get


class CompanyCheck(unittest.TestCase):
    def test_a_namesake_is_never_shown_as_the_employer(self):
        # the pack for the made-up Riverside Heritage Trust showed "Riverside Cemetery (Denver)" from Wikipedia
        # and "RIVERSIDE HERITAGE JOINERY LTD" from Companies House as the employer (2026-10-23)
        get = fake_get(["Riverside Cemetery (Denver)", "Riverside Museum"],
                       ["RIVERSIDE HERITAGE JOINERY LTD 10279495 - Incorporated on 14 July 2016"])
        with mock.patch.object(reputation, "get", get):
            flags, md = reputation.check(EMPLOYER)
        self.assertEqual(flags, [])
        self.assertIn("- Wikipedia: no article", md)
        self.assertIn("- Companies House: no registration found", md)
        self.assertNotIn("Cemetery", md)
        self.assertNotIn("JOINERY", md)

    def test_the_employers_own_article_and_registration_are_shown(self):
        get = fake_get(["Riverside Cemetery (Denver)", "Riverside Heritage Trust"],
                       ["RIVERSIDE HERITAGE TRUST LIMITED 01234567 - Incorporated on 2 May 1990"])
        with mock.patch.object(reputation, "get", get):
            _, md = reputation.check(EMPLOYER)
        self.assertIn("[Riverside Heritage Trust]", md)
        self.assertIn("- Companies House: RIVERSIDE HERITAGE TRUST LIMITED 01234567", md)


if __name__ == "__main__":
    unittest.main()

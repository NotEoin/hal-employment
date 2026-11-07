"""Company checks: only an article or a registration that plausibly is the advert's employer is shown.

The web is faked: `reputation.get` answers with made-up Wikipedia search results and articles, and a
made-up Companies House results page and company pages, so no test leaves the machine.
"""
from __future__ import annotations

import json
import unittest
import urllib.parse
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import reputation

EMPLOYER = "Riverside Heritage Trust"   # the made-up humanities advert's employer
ADVERT = reputation.Advert(location="Made-up Town, UK",
                           text="Help catalogue the Trust's museum collections and archives for visitors.",
                           url="https://careers.riverside-heritage.org.uk/jobs/1")
FINTECH = "Brightledger"                # a made-up lender
FINTECH_ADVERT = reputation.Advert(location="London",
                                   text="Join our open banking team building lending and payments software.",
                                   url="https://boards.greenhouse.io/brightledger/jobs/2")


def fake_get(articles: dict[str, tuple[str, list[str]]], registrations: dict[str, tuple[str, str, list[str]]]):
    """A stand-in for `reputation.get`: article title -> (summary, links); number -> (name, status, SIC codes)."""
    def get(url: str, **kw) -> str:
        if url.startswith(reputation.COMPANIES_HOUSE):
            return "".join(f'<li class="type-company"><h3><a href="/company/{n}">{name}</a></h3>'
                           f'<p>{n} - Incorporated on 2 May 1990</p></li>' for n, (name, _, _) in registrations.items())
        if url.startswith(f"{reputation.COMPANIES_HOUSE_SITE}/company/"):
            _, status, sic = registrations[url.rsplit("/", 1)[1]]
            return (f'<dd class="text data" id="company-status">{status}</dd>'
                    + "".join(f'<span id="sic{i}">{s}</span>' for i, s in enumerate(sic)))
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if query.get("list") == "search":
            return json.dumps({"query": {"search": [{"title": t} for t in articles]}})
        if query.get("prop") == "sections":
            return json.dumps({"parse": {"sections": [{"line": "Controversies"}]}})
        summary, links = articles[query["titles"]]
        return json.dumps({"query": {"pages": {"1": {"extract": summary, "extlinks": [{"*": u} for u in links]}}}})
    return get


class CompanyCheck(unittest.TestCase):
    def check(self, company: str, advert: reputation.Advert, articles: dict, registrations: dict):
        with mock.patch.object(reputation, "get", fake_get(articles, registrations)):
            return reputation.check(company, advert)

    def test_a_namesake_is_never_shown_as_the_employer(self):
        # the pack for the made-up Riverside Heritage Trust showed "Riverside Cemetery (Denver)" from Wikipedia
        # and "RIVERSIDE HERITAGE JOINERY LTD" from Companies House as the employer (2026-10-23)
        flags, md = self.check(EMPLOYER, ADVERT, {"Riverside Cemetery (Denver)": ("A cemetery.", []),
                                                  "Riverside Museum": ("A museum in Made-up Town.", [])},
                               {"10279495": ("RIVERSIDE HERITAGE JOINERY LTD", "Active", ["16230 - Joinery"])})
        self.assertEqual(flags, [])
        self.assertIn(f"- Wikipedia: {reputation.NO_MATCH}", md)
        self.assertIn(f"- Companies House: {reputation.NO_MATCH}", md)
        self.assertNotIn("Cemetery", md)
        self.assertNotIn("JOINERY", md)

    def test_the_employers_own_article_and_registration_are_shown(self):
        flags, md = self.check(EMPLOYER, ADVERT,
                               {"Riverside Heritage Trust": ("A charity in Made-up Town that runs a museum.", [])},
                               {"01234567": ("RIVERSIDE HERITAGE TRUST LIMITED", "Active",
                                             ["91020 - Museums activities"])})
        self.assertIn("[Riverside Heritage Trust]", md)
        self.assertIn("- Companies House: RIVERSIDE HERITAGE TRUST LIMITED 01234567", md)
        self.assertIn("(Active; 91020 - Museums activities)", md)
        self.assertEqual(flags, ["company check: Wikipedia sections on Controversies"])

    def test_a_same_named_company_in_another_trade_or_dissolved_is_no_reliable_match(self):
        # one employer's check listed a solar company, a fungicide and a dissolved company of the same name
        # (2026-11-06)
        articles = {"Brightledger": ("Brightledger is a solar panel maker in a made-up city.", []),
                    "Brightledger (fungicide)": ("Brightledger is a fungicide used on wheat.", [])}
        registrations = {"07654321": ("BRIGHTLEDGER LTD", "Dissolved", ["64999 - Financial intermediation"]),
                         "07654322": ("BRIGHTLEDGER SOLAR LIMITED", "Active", ["35110 - Production of electricity"])}
        flags, md = self.check(FINTECH, FINTECH_ADVERT, articles, registrations)
        self.assertEqual(flags, [])
        self.assertIn(f"- Wikipedia: {reputation.NO_MATCH}", md)
        self.assertIn(f"- Companies House: {reputation.NO_MATCH}", md)
        for wrong in ("solar", "fungicide", "DISSOLVED", "07654321", "07654322"):
            self.assertNotIn(wrong, md)

    def test_an_article_linking_to_the_employers_own_site_is_the_employer(self):
        articles = {"Riverside Heritage Trust": ("A charity founded in 1990.",
                                                 ["https://www.riverside-heritage.org.uk/about"])}
        _, md = self.check(EMPLOYER, ADVERT, articles, {})
        self.assertIn("[Riverside Heritage Trust]", md)

    def test_an_article_in_the_adverts_trade_is_the_employer(self):
        articles = {"Brightledger": ("Brightledger is a lender offering payments and open banking loans.", [])}
        registrations = {"07654323": ("BRIGHTLEDGER LIMITED", "Active", ["64929 - Other credit granting"])}
        _, md = self.check(FINTECH, FINTECH_ADVERT, articles, registrations)
        self.assertIn("[Brightledger]", md)
        self.assertIn("- Companies House: BRIGHTLEDGER LIMITED 07654323", md)

    def test_a_job_boards_link_names_no_employer(self):
        self.assertEqual(reputation.domains(FINTECH_ADVERT), set())
        self.assertEqual(reputation.domains(ADVERT), {"riverside-heritage"})


if __name__ == "__main__":
    unittest.main()

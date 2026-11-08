"""Web pages under Projects: one page or article read as a project with its title, date, role, tools and media, and
pictures; and a portfolio whose pieces are offered, of which only the ones picked become projects.

The pages are saved synthetic HTML (`tests/fixtures/pages`) served from 127.0.0.1. The person is Marnie
Quillfeather, a made-up history and design graduate.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_site import FakeSite
from test_ingest import home

from jobhunt.core import runner
from jobhunt.profile import store as profile_store
from jobhunt.sections import actions, pages, pipeline, store


def add(con, url: str, kind: str) -> int:
    """A link added to Projects as `kind`; its source's id."""
    return pipeline.add_link(con, "project", url, doc_kind=kind)["added"][0]


class Page(unittest.TestCase):
    def test_a_page_is_a_project_with_its_title_date_role_and_the_tools_and_media_it_names(self):
        with home() as (con, _, _), FakeSite() as site:
            url = site.saved("/work/harbour-posters/", "harbour-posters.html")
            out = pipeline.read(con, add(con, url, "web_page"))
            card = profile_store.items(con, "project", hidden=False)[0]
            skills = {s["fields"]["name"] for s in profile_store.items(con, "skill", hidden=False)}
        self.assertEqual(out["state"], "read")
        f = card["fields"]
        self.assertEqual((f["name"], f["role"], f["link"], f["period"]),
                         ("Harbour posters", "Lead designer", url, "2025-04"))
        self.assertEqual(f["tools"], ["Illustrator", "InDesign", "screen printing"])
        self.assertIn("screen printing", f["summary"])
        self.assertTrue({"Illustrator", "InDesign"} <= skills)

    def test_an_article_gives_its_date_from_the_page_and_its_role_as_stated(self):
        with home() as (con, vault, _), FakeSite() as site:
            source_id = add(con, site.saved("/blog/registers", "article.html"), "web_page")
            pipeline.read(con, source_id)
            card = profile_store.items(con, "project", hidden=False)[0]
            note = (vault / store.get(con, source_id)["note_rel"]).read_text()
        self.assertEqual((card["fields"]["period"], card["fields"]["role"]), ("2024-11", "author and researcher"))
        self.assertEqual(card["fields"]["tools"], ["Excel", "QGIS"])
        self.assertIn("Reading parish registers", note)

    def test_pictures_alt_text_is_kept_in_the_note(self):
        with home() as (con, vault, _), FakeSite() as site:
            source_id = add(con, site.saved("/work/harbour-posters/", "harbour-posters.html"), "web_page")
            pipeline.read(con, source_id)
            note = (vault / store.get(con, source_id)["note_rel"]).read_text()
        self.assertIn("- A blue poster of a lighthouse", note)

    def test_a_page_that_is_not_there_fails_in_plain_words(self):
        with home() as (con, _, _), FakeSite() as site:
            source_id = add(con, site.url("/missing"), "web_page")
            out = pipeline.read(con, source_id)
            error = store.get(con, source_id)["error"]
        self.assertEqual(out["state"], "failed")
        self.assertIn("There is no page at this link", error)

    def test_an_unchanged_page_answers_the_check_by_its_etag(self):
        with FakeSite() as site:
            url = site.saved("/a", "article.html")
            got = pages.fetch(url)
            same = pages.check(url, got.etag, got.hash)
            site.page("/a", "<html><body><p>New words</p></body></html>")
            changed = pages.check(url, got.etag, got.hash)
        self.assertFalse(same[0])
        self.assertTrue(changed[0])


class Portfolio(unittest.TestCase):
    def test_a_portfolio_offers_its_pieces_and_none_become_projects_until_picked(self):
        with home() as (con, _, _), FakeSite() as site:
            index = site.saved("/work/", "portfolio.html")
            source_id = add(con, index, "portfolio_site")
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
            cards = profile_store.items(con, "project", hidden=False)
        self.assertEqual(out["children"], [])
        self.assertEqual([(p["title"], p["url"]) for p in got["pieces"]],
                         [("Three screen-printed harbour posters", site.url("/work/harbour-posters/")),
                          ("Parish map of Harrowgate Vale", site.url("/work/parish-map/")),
                          ("Tide zine", site.url("/work/tide-zine/"))])
        self.assertEqual(cards, [])

    def test_each_picked_piece_becomes_its_own_project(self):
        with home() as (con, _, _), FakeSite() as site:
            index = site.saved("/work/", "portfolio.html")
            piece = site.saved("/work/harbour-posters/", "harbour-posters.html")
            source_id = add(con, index, "portfolio_site")
            pipeline.read(con, source_id)
            store.change(con, source_id, {"picked": [piece]})
            done = runner.run(actions.KIND, params={"ids": [source_id]})
            children = [s for s in store.listing(con, "project") if s["parent_id"] == source_id]
            cards = [c["fields"]["name"] for c in profile_store.items(con, "project", hidden=False)]
        self.assertEqual(done["result"]["read"], 2)
        self.assertEqual([c["locator"] for c in children], [piece])
        self.assertEqual(cards, ["Harbour posters"])

    def test_only_a_portfolio_takes_picked_pieces_and_only_web_addresses(self):
        with home() as (con, _, _):
            page = add(con, "https://example.org/a", "web_page")
            folio = add(con, "https://example.org/work/", "portfolio_site")
            with self.assertRaises(ValueError):
                store.change(con, page, {"picked": ["https://example.org/b"]})
            with self.assertRaises(ValueError):
                store.change(con, folio, {"picked": ["javascript:alert(1)"]})


if __name__ == "__main__":
    unittest.main()

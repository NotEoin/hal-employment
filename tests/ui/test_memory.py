"""What Hal remembers in a real browser against the real server: a waiting suggestion with its evidence, "Yes,
do this", and the Learned tab's switch and Delete, at both sizes.

They need `HAL_TEST_BROWSER=1` and Playwright, as every browser test does (docs/dev/testing.md). The person
and the employers are made up; the towns are real so distances can be measured.
"""
from __future__ import annotations

import contextlib
import os
import threading
import unittest
from contextlib import closing
from unittest import mock

from support.jobs import advert
from support.ui import AppPage, chromium, skip_unless_browser
from support.world import World

from jobhunt import db
from jobhunt.core import settings
from jobhunt.core.settings import store
from jobhunt.find import feedback
from jobhunt.learn import patterns
from jobhunt.web import app, auth

GOAL = {"you.city": "Leeds", "goal.max_distance": 35, "goal.places": ["Leeds"]}
FAR = [("Researcher", "Barnsley"), ("Policy Officer", "Rotherham"), ("Archivist", "Sheffield"), ("Assistant", "Selby")]
TITLE = "Lower your distance from 35 to 15 miles?"


@contextlib.contextmanager
def real_app(browser, size: str = "wide", scheme: str = "light"):
    """The new server inside the made-up world, and a signed-in tab on it."""
    store.drop_cache()
    httpd = app.make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="ui-test-server", daemon=True).start()
    p = AppPage(browser, f"http://127.0.0.1:{port}", size, scheme)
    try:
        p.page.goto(auth.mint_open_url(port=port))
        yield p
    finally:
        p.close()
        httpd.stop()   # waits for its requests, which hold the world's database open until they end


@skip_unless_browser
class MemoryPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_TZ": "UTC"}))
        self.enterContext(World())
        settings.set_many(GOAL, by="setup")
        with closing(db.connect()) as con:
            for title, place in FAR:
                job = advert(title, "A made-up role.", id=f"test:far-{place.lower()}", location=place,
                             company="Lanternfield Trust")
                db.upsert(con, job)
                con.commit()
                feedback.not_for_me(con, job["id"], "Too far")
            patterns.run(con)

    def test_a_suggestion_shows_its_evidence_and_waits_for_yes(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), real_app(self.browser, size, scheme) as p:
                p.open("memory")
                pg = p.page
                card = pg.locator(".mem-sugg", has_text=TITLE)
                card.wait_for()
                self.assertIn("Researcher, Barnsley (18 miles)", card.inner_text())
                self.assertIn('You chose "Too far" for 4 jobs this week.', card.inner_text())
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])
                store.drop_cache()
                self.assertEqual(settings.get("goal.max_distance"), 35)

    def test_yes_moves_it_to_learned_where_it_can_be_switched_off_and_deleted(self) -> None:
        with real_app(self.browser) as p:
            p.open("memory")
            pg = p.page
            pg.locator(".mem-sugg", has_text=TITLE).get_by_role("button", name="Yes, do this").click()
            pg.get_by_text("Nothing waiting. Hal suggests changes when it sees a pattern.").wait_for()
            store.drop_cache()
            self.assertEqual(settings.get("goal.max_distance"), 15)
            pg.get_by_role("tab", name="Learned").click()
            item = pg.locator(".mem-item", has_text="Travel up to 15 miles, not 35")
            item.wait_for()
            self.assertIn('From: You chose "Too far" for 4 jobs this week. Today.', item.inner_text())
            switch = item.get_by_role("switch")
            self.assertEqual(switch.get_attribute("aria-checked"), "true")
            switch.click()
            item.locator('[role="switch"][aria-checked="false"]').wait_for()
            store.drop_cache()
            self.assertEqual(settings.get("goal.max_distance"), 35)
            item.get_by_role("button", name="Delete: Travel up to 15 miles, not 35").click()
            item.wait_for(state="detached")
            self.assertEqual(pg.url.rsplit("#", 1)[1], "memory-learned")
            self.assertEqual(p.errors, [])

    def test_the_learned_tab_opens_from_its_link(self) -> None:
        with real_app(self.browser, "narrow", "dark") as p:
            p.open("memory-learned")
            pg = p.page
            pg.get_by_text("Nothing learned yet.", exact=False).wait_for()
            self.assertEqual(pg.get_by_role("tab", name="Learned").get_attribute("aria-selected"), "true")
            self.assertEqual(p.sideways(), 0)
            self.assertEqual(p.serious_axe(), [])
            self.assertEqual(p.errors, [])

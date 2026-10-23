"""The Settings page in a real browser against a real server: every setting can be changed, search reads the
help words, Restart and Re-check are offered, and the browsers Hal found are marked.

They need `HAL_TEST_BROWSER=1` and Playwright, like every browser test.
"""
from __future__ import annotations

import contextlib
import time
import unittest
from unittest import mock

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.core import health_routes, paths, settings
from jobhunt.core.settings import registry

SAVE_WAIT_S = 5    # a save answers in well under a second; this bounds a broken one
POLL_S = 0.1
CONTROLS = ("input:not([disabled]), select:not([disabled]), textarea:not([disabled]), "
            "button[role=switch]:not([disabled]), [role=radio], button:not([disabled])")
# Values that make every row with a visible_if condition show.
# Routes other parts serve (/home, /goal/versions) may answer 404 here;
# the browser logs each 404 as a console error, which is not this page's fault.
MISSING_ROUTE = "Failed to load resource: the server responded with a status of 404"
SHOW_ALL = {"you.right_to_work": "uk_visa", "you.start": "date", "ai.provider": "openai_compat",
            "advanced.developer_mode": True}


@skip_unless_browser
class SettingsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()

    def tab(self, route: str = "settings", size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.page.goto(self.hal.open_url(next="/#" + route))
        p.settle()
        p.page.wait_for_selector(".settings .card .setting")
        return p

    def row(self, p: AppPage, key: str):
        return p.page.locator(f'[data-key="{key}"]')

    def show_more(self, p: AppPage) -> None:
        more = p.page.get_by_role("button", name="Show more")
        if more.count():
            more.first.click()

    def until(self, done) -> None:
        deadline = time.monotonic() + SAVE_WAIT_S
        while not done() and time.monotonic() < deadline:
            time.sleep(POLL_S)
        self.assertTrue(done())

    def saved(self, key: str, expected) -> None:
        self.until(lambda: settings.get(key) == expected)

    def test_every_registered_setting_can_be_changed_on_the_settings_page(self) -> None:
        changeable = {k for k, s in registry.REGISTRY.items()
                      if (s.stored or s.kind in ("secret", "private")) and s.group_id != registry.INTERNAL_GROUP}
        here = paths.platform_name()     # the page shows the rows for the computer the server runs on
        seen = set()
        for provider in ("openai_compat", "local"):     # rows for each kind of AI show only with it chosen
            settings.set_many({**SHOW_ALL, "ai.provider": provider})
            groups = self.client.get("/api/v2/settings/schema").json()["groups"]
            p = self.tab()
            for gr in groups:
                p.go("settings-" + gr["id"])
                p.page.wait_for_selector(f'[data-group="{gr["id"]}"] .setting')
                self.show_more(p)
                for it in gr["items"]:
                    if it["key"] not in changeable or not registry.visible(registry.REGISTRY[it["key"]], settings.get,
                                                                           developer=True, platform=here):
                        continue
                    with self.subTest(key=it["key"], provider=provider):
                        row = self.row(p, it["key"])
                        row.wait_for(state="attached")
                        self.assertGreater(row.locator(CONTROLS).count(), 0, "no control to change it")
                        seen.add(it["key"])
            self.assertEqual([e for e in p.errors if not e.startswith(MISSING_ROUTE)], [])
        shown_here = {k for k in changeable if registry.REGISTRY[k].platforms in ((), (here,))}
        self.assertEqual(shown_here - seen, set(), "registered settings with no control on the page")

    def test_each_kind_of_control_saves_its_value(self) -> None:
        p = self.tab("settings-you")
        name = self.row(p, "you.name").get_by_label("Your name")
        name.fill("Robin Example")
        name.press("Enter")
        self.saved("you.name", "Robin Example")
        p.go("settings-goal")
        p.page.wait_for_selector('[data-group="goal"]')
        pay = self.row(p, "goal.min_pay").get_by_label("Lowest pay you will consider")
        pay.fill("24k")
        pay.press("Enter")
        self.saved("goal.min_pay", 24000)
        self.assertIn("24,000", self.row(p, "goal.min_pay").inner_text())
        self.row(p, "goal.pattern").get_by_role("radio", name="Remote").click()
        self.saved("goal.pattern", "remote")
        self.row(p, "goal.kinds").get_by_label("Apprenticeships").check()
        self.until(lambda: "apprenticeship" in settings.get("goal.kinds"))
        roles = self.row(p, "goal.roles").get_by_label("Add to Roles you want")
        roles.fill("archivist")
        roles.press("Enter")
        self.saved("goal.roles", ["archivist"])
        self.row(p, "goal.max_distance").get_by_label("How far you will travel").evaluate(
            "e => { e.value = '40'; e.dispatchEvent(new Event('input', {bubbles: true}));"
            " e.dispatchEvent(new Event('change', {bubbles: true})); }")
        self.saved("goal.max_distance", 40)
        p.go("settings-sites")
        p.page.wait_for_selector('[data-group="sites"]')
        self.row(p, "sites.check_open").get_by_role("switch").click()
        self.saved("sites.check_open", False)
        self.row(p, "sites.max_age_days").get_by_label("Ignore adverts older than").select_option(label="7 days")
        self.saved("sites.max_age_days", 7)
        p.go("settings-schedule")
        p.page.wait_for_selector('[data-group="schedule"]')
        times = self.row(p, "schedule.search_times").get_by_label("Search for jobs at")
        times.fill("08:00 and 14:00")
        times.press("Enter")
        self.saved("schedule.search_times", ["08:00", "14:00"])
        self.assertEqual([e for e in p.errors if not e.startswith(MISSING_ROUTE)], [])

    def test_a_value_hal_cannot_read_shows_words_under_the_field(self) -> None:
        p = self.tab("settings-goal")
        pay = self.row(p, "goal.min_pay").get_by_label("Lowest pay you will consider")
        pay.fill("lots")
        pay.press("Enter")
        p.page.wait_for_selector('[data-key="goal.min_pay"] [role=alert]')
        self.assertIn("Hal could not read that", self.row(p, "goal.min_pay").inner_text())
        self.assertEqual(settings.get("goal.min_pay"), 0)

    def test_search_finds_settings_by_their_help_words(self) -> None:
        p = self.tab()
        p.page.get_by_label("Search all settings").fill("distances")
        p.page.wait_for_selector('[data-key="you.city"]')
        self.assertIn("Where you live", p.page.inner_text("#main"))
        p.page.get_by_label("Search all settings").fill("unpaid roles are hidden")
        p.page.wait_for_selector('[data-key="goal.unpaid_ok"]')
        self.assertEqual(p.page.locator(".settings .stack .setting").count(), 1)
        p.page.get_by_label("Search all settings").fill("no such words anywhere")
        p.page.wait_for_selector("text=No setting matches")

    def test_a_restart_setting_offers_restart(self) -> None:
        settings.set("advanced.developer_mode", True)
        p = self.tab("settings-advanced")
        self.show_more(p)
        port = self.row(p, "advanced.port").get_by_label("Port")
        port.click()
        port.press("Control+a")
        port.press_sequentially("8800")   # typed key by key, as a person does; fill() skips a number box's input
        port.press("Enter")
        p.page.get_by_role("button", name="Restart Hal").first.click()
        p.page.wait_for_selector('[role="dialog"] >> text=Restart now')
        self.assertEqual([e for e in p.errors if not e.startswith(MISSING_ROUTE)], [])

    def test_a_scoring_setting_offers_re_check(self) -> None:
        con = db.connect()
        self.addCleanup(con.close)
        for n in (1, 2):
            con.execute("INSERT INTO jobs (id, source, title, company, first_seen, last_seen, status, active)"
                        " VALUES (?, 'demo', 'Archive Assistant', 'Made-up Museum', '2026-10-20', '2026-10-20',"
                        " 'new', 1)", (f"demo:{n}",))
        con.commit()
        p = self.tab("settings-scoring")
        self.row(p, "scoring.w.fresh").get_by_label("Advert posted in the last 3 days").evaluate(
            "e => { e.value = '9'; e.dispatchEvent(new Event('input', {bubbles: true}));"
            " e.dispatchEvent(new Event('change', {bubbles: true})); }")
        p.page.wait_for_selector("text=Re-check 2 jobs")
        self.saved("scoring.w.fresh", 9)

    def test_applying_lists_the_browsers_found_and_firefox_as_not_supported(self) -> None:
        found = {"opera", "chrome", "edge", "brave", "vivaldi", "firefox"}
        with mock.patch.object(health_routes, "found_browser_ids", return_value=found):
            p = self.tab("settings-applying")
            box = self.row(p, "applying.browser").get_by_label("Browser Hal fills forms in")
            p.page.wait_for_selector('[data-key="applying.browser"] option:has-text("Opera (found)")', state="attached")
            options = box.locator("option").all_inner_texts()
        for words in ("Opera (found)", "Google Chrome (found)", "Microsoft Edge (found)", "Brave (found)",
                      "Vivaldi (found)", "Firefox (not supported yet)"):
            self.assertIn(words, options)
        self.assertIn("Chromium", options)
        self.assertTrue(box.locator("option", has_text="Firefox").is_disabled())

    def test_private_answers_show_only_saved(self) -> None:
        p = self.tab("settings-you")
        self.row(p, "you.private").get_by_role("button", name="Change answers").click()
        p.page.wait_for_selector('[role="dialog"] >> text=Private answers')
        p.page.get_by_label("Adjustments you need for interviews or tests").fill("SYNTH-PRIVATE-3301 made-up")
        p.page.get_by_role("button", name="Save").click()
        p.page.wait_for_selector('[data-key="you.private"] >> text=Saved')
        self.assertNotIn("SYNTH-PRIVATE", p.page.content())

    def test_a_user_install_sees_each_updates_row_once_and_update_hal_opens_its_dialog(self) -> None:
        # Windows 11 install test (2026-10-23): the catalogue's rows came first with an empty Version and an Update
        # Hal button that only said "This part is being built", above the real panel
        from jobhunt.core import updates

        updates.write_install(updates.Install(kind="user", code=str(paths.code_dir())))
        updates.write_check({"available": True, "behind": 1, "newest": "f" * 40, "version": "2.0.1",
                             "whats_new": ["A made-up change"], "words": "Hal 2.0.1 is ready."})
        p = self.tab("settings-updates")
        group = p.page.locator('[data-group="updates"]')
        group.locator('[data-key="updates.apply"] button').wait_for()
        for key in ("updates.version", "updates.github", "updates.apply", "updates.auto_check"):
            self.assertEqual(group.locator(f'[data-key="{key}"]').count(), 1, key)
        self.assertIn("2.0.1 is ready", group.locator('[data-key="updates.version"]').inner_text())
        group.locator('[data-key="updates.apply"] button').click()
        p.page.wait_for_selector("[role=dialog] >> text=Hal stops for about a minute")
        self.assertNotIn("being built", p.page.inner_text("body"))

    def test_a_panel_button_found_by_search_opens_its_group(self) -> None:
        # a search drew panel buttons as plain rows, and pressing one said "This part is being built."
        p = self.tab("settings-you")
        p.page.get_by_label("Search all settings").fill("Get help")
        self.row(p, "health.help").get_by_role("button", name="Get help").click()
        p.page.wait_for_url("**/#settings-health")
        p.page.wait_for_selector('[data-group="health"]')
        self.assertNotIn("being built", p.page.inner_text("body"))

    def test_rows_show_real_words_not_placeholders(self) -> None:
        # the App password said "Kept in {store}." and Where Hal looks said "N sites, M switched on."
        p = self.tab("settings-email")
        password = self.row(p, "email.app_password").inner_text()
        self.assertIn("Kept in ", password)
        self.assertNotIn("{store}", password)
        p.go("settings-sites")
        where = self.row(p, "sites.where")
        p.page.wait_for_selector('[data-key="sites.where"] >> text=switched on')
        self.assertRegex(where.inner_text(), r"\d+ sites?, \d+ switched on\.")

    def test_the_helper_folder_row_shows_the_folder_with_copy(self) -> None:
        # the row showed only its fixed help, with no folder
        p = self.tab("settings-applying")
        row = self.row(p, "applying.helper_folder")
        row.get_by_role("button", name="Copy Browser helper folder").wait_for()
        self.assertIn(str(paths.helper_dir()), row.inner_text())

    def test_the_goal_group_lists_its_earlier_versions_once(self) -> None:
        # the catalogue's link row went to the page already open, above the real list
        p = self.tab("settings-goal")
        p.page.wait_for_selector('[data-group="goal"] #goal-history')
        self.assertEqual(self.row(p, "goal.history").count(), 1)

    def test_no_control_sticks_out_of_its_card_in_the_laptop_window(self) -> None:
        # Windows 11 install test (2026-10-23): Quiet hours stuck out 10 px past its card at 1033 px
        settings.set_many(SHOW_ALL)
        p = self.tab("settings", "laptop")
        out = """() => [...document.querySelectorAll('.card .setting *')].filter(e => {
          const card = e.closest('.card').getBoundingClientRect(), r = e.getBoundingClientRect();
          return r.width > 0 && r.right > card.right + 0.5; }).map(e => e.closest('[data-key]').dataset.key)"""
        for group in [g["id"] for g in self.client.get("/api/v2/settings/schema").json()["groups"]]:
            with self.subTest(group=group):
                p.go("settings-" + group)
                p.page.wait_for_selector(f'[data-group="{group}"]')
                self.show_more(p)
                self.assertEqual(sorted(set(p.page.evaluate(out))), [])

    def test_the_page_draws_cleanly_at_both_sizes(self) -> None:
        settings.set("advanced.developer_mode", True)
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            p = self.tab("settings", size, scheme)
            self.assertEqual(p.page.locator("button:has-text('Open the setup guide')").count(), 1)
            for group in ("you", "goal", "scoring", "applying", "privacy", "health", "advanced"):
                with self.subTest(size=size, group=group):
                    p.go("settings-" + group)
                    p.page.wait_for_selector(f'[data-group="{group}"]')
                    self.assertEqual(p.sideways(), 0)
                    self.assertEqual(p.serious_axe(), [])
            self.assertEqual([e for e in p.errors if not e.startswith(MISSING_ROUTE)], [])


if __name__ == "__main__":
    unittest.main()

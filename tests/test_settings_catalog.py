"""Tests of the settings registry and catalogue: every key is declared, worded and grouped
as the Settings page's design shows it, and the legacy-key map points at real settings.
"""

import os
import sys
import unittest
from unittest import mock

import support  # noqa: F401 - sets up the test home before any jobhunt import

from jobhunt.core.settings import legacy_map, parse, registry
from jobhunt.core.settings.catalog import ai as ai_catalog
from jobhunt.core.settings.registry import REGISTRY, Setting

registry.load_all()


def setUpModule():
    # The suggested AI looks for Ollama on this computer; tests never reach the network.
    patcher = mock.patch.object(ai_catalog, "ollama_answers", return_value=False)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)

# Every key of the catalogue (rows that list several keys at once are expanded in the catalogue).
SPEC_KEYS = (
    "addon.applications_mirror", "addon.enabled", "addon.hal_root", "addon.jobs_command", "addon.pipeline_note",
    "addon.respect_off", "addon.speak_command", "addon.vault",
    "advanced.developer_mode", "advanced.log_level",
    "advanced.pause", "advanced.port", "advanced.prompts", "advanced.quit", "advanced.simple_menu",
    "advanced.theme",
    "ai.allowance", "ai.claude_account", "ai.daily_cap", "ai.gpu_gate", "ai.gpu_ignore", "ai.keep_free",
    "ai.local_only", "ai.model.checker", "ai.model.embed", "ai.model.quick", "ai.model.writer", "ai.ollama_url",
    "ai.openai_key", "ai.openai_url", "ai.parallel", "ai.provider", "ai.test", "ai.timeout",
    "applying.aggregators", "applying.browser", "applying.fill_timeout", "applying.find_contacts",
    "applying.fix_rounds", "applying.max_steps", "applying.mode", "applying.notes", "applying.page_timeout",
    "applying.press_next", "applying.submit", "applying.wait_minutes", "applying.watch_hours", "applying.window",
    "cvdocs.file_name", "cvdocs.folder", "cvdocs.letter_file_name", "cvdocs.letter_tone", "cvdocs.letter_words",
    "cvdocs.never_use", "cvdocs.pick_first", "cvdocs.route", "cvdocs.signoff", "cvdocs.spelling", "cvdocs.tailor",
    "cvdocs.voice",
    "email.address", "email.alert_senders", "email.app_password", "email.codes", "email.first_sync_days",
    "email.lookback_days", "email.provider", "email.refine_with_ai", "email.todos",
    "goal.agencies", "goal.avoid_employers", "goal.avoid_roles", "goal.avoid_sectors", "goal.experience_years",
    "goal.has_clearance", "goal.history", "goal.interests", "goal.kinds", "goal.max_distance", "goal.min_pay",
    "goal.mode", "goal.pattern", "goal.place_aliases", "goal.places", "goal.roles", "goal.stage", "goal.study_end",
    "goal.study_status", "goal.training_ok_places", "goal.training_providers", "goal.training_schemes",
    "goal.unpaid_ok",
    "helper.allowed_ids",
    "notify.alert_score", "notify.daily_summary", "notify.daily_to", "notify.desktop", "notify.digest_new_min",
    "notify.follow_up_days", "notify.ntfy_topic", "notify.phone", "notify.quiet_hours", "notify.weekly_review",
    "privacy.delete", "privacy.export", "privacy.leaves", "privacy.where",
    "record.folder", "record.ocr", "record.private_folders", "record.search",
    "remote.how", "remote.launcher", "remote.sessions",
    "schedule.catch_up", "schedule.mail_every", "schedule.mail_hours", "schedule.record_every",
    "schedule.search_times", "schedule.start_at_login",
    "scoring.base", "scoring.interest_cap", "scoring.min_show", "scoring.prepare_ahead", "scoring.preset",
    "scoring.rescore", "scoring.screen", "scoring.screen_min", "scoring.skill_cap", "scoring.skills",
    "scoring.w.alert_only", "scoring.w.entry_level", "scoring.w.first_place", "scoring.w.fresh",
    "scoring.w.interest", "scoring.w.kind_also", "scoring.w.kind_wanted", "scoring.w.niche_source",
    "scoring.w.no_interest", "scoring.w.old", "scoring.w.other_place", "scoring.w.pattern_match",
    "scoring.w.pattern_other", "scoring.w.pay_under", "scoring.w.remote_uk", "scoring.w.role_text_only",
    "scoring.w.role_title", "scoring.w.small_employer", "scoring.w.stale", "scoring.w.training_scheme",
    "scoring.w.years_required",
    "setup.first_app_seen",
    "sites.adzuna_key", "sites.check_open", "sites.contact", "sites.detail_fetch", "sites.max_age_days",
    "sites.read_alerts", "sites.reed_key", "sites.threads", "sites.where",
    "updates.apply", "updates.auto_check", "updates.github", "updates.held_commit", "updates.helper",
    "updates.last_check", "updates.rollback", "updates.version", "updates.whats_new",
    "you.city", "you.country", "you.diversity", "you.driving_licence", "you.education.degree", "you.email",
    "you.links.linkedin", "you.location_line", "you.name", "you.nationality", "you.notice", "you.office",
    "you.phone", "you.preferred_name", "you.private", "you.relocate", "you.right_to_work", "you.salary_answer",
    "you.start", "you.start_date", "you.visa_detail", "you.work_status",
)

# The Settings page's design: group names in order, and each group's labels. The design's
# "Update key" is replaced by the GitHub sign-in; "Weekly review" is developer-only until built.
MOCK_GROUPS = (
    ("you", "You", ("Your name", "Email for applications", "Phone", "Where you live", "Right to work",
                    "When you can start", "Private answers (equality and adjustments)")),
    ("goal", "What you're looking for", ("Career stage", "Roles you want", "Roles to avoid",
                                         "Places, in order of preference", "How far you will travel",
                                         "Working pattern", "Lowest pay you will consider", "Sectors to avoid",
                                         "How hard you are looking")),
    ("sites", "Job sites", ("Where Hal looks", "Ignore adverts older than",
                            "Check an advert is still open before showing it",
                            "Read job alert emails (Indeed, LinkedIn and others)")),
    ("scoring", "How Hal picks jobs", ("Starting point", "Your first-choice place", "A role you want in the job title",
                                       "Graduate or entry-level role", "Advert posted in the last 3 days",
                                       "Pay under your lowest", "Asks for 2 or more years of experience",
                                       "Lowest score to show in Matches",
                                       "Re-check every open job with these points")),
    ("cvdocs", "CV and documents", ("How your CV is made", "Adjust your CV for each job", "Spelling",
                                    "File names employers see", "Cover letter tone", "Cover letter length",
                                    "Sign-off", "Where documents are saved")),
    ("applying", "Applying", ("Browser Hal fills forms in", "Notes about job sites", "Press Submit for you")),
    ("email", "Email", ("Email account", "Address", "App password", "Turn interview and test emails into to-dos",
                        "Read sign-in codes that job sites email you")),
    ("notify", "Notifications", ("Pop-ups on this computer", "Alerts on your phone",
                                 "Tell me about matches scoring at least", "Quiet hours", "Daily summary by email",
                                 "Weekly review")),
    ("ai", "Hal's brain (AI)", ("How Hal thinks", "Claude account", "For writing letters and CVs",
                                "For checking jobs and facts", "For quick jobs like sorting email",
                                "Your Claude allowance", "Most background AI checks a day",
                                "Keep Claude free for me during", "Never use online AI", "Check that Hal's AI works")),
    ("schedule", "Schedule", ("Search for jobs at", "Check email", "Start Hal when the computer starts",
                              "Catch up on missed searches")),
    ("privacy", "Privacy and data", ("Where your data is kept", "What leaves this computer", "Export everything",
                                     "Delete all your data from this computer")),
    ("updates", "Updates", ("Version", "Check for updates every day", "Browser helper")),
    ("health", "Is Hal working?", ()),
    ("advanced", "Advanced", ("Hal's instructions to the AI", "Most form pages Hal moves through",
                              "Give up on a slow page after")),
)
# Groups the owner's answers add to the design: after Updates, and last.
# Groups added after the mock, each after the one named; Preferences took three rows from Applying (2026-11-04).
ADDED_GROUPS = {"remote": "updates", "addon": "advanced", "prefs": "goal"}
# Ranges whose ends the Settings page shows: key -> (min, max, default).
MOCK_RANGES = {"goal.max_distance": (5, 60), "scoring.w.first_place": (0, 20), "scoring.w.role_title": (0, 20),
               "scoring.w.fresh": (0, 10), "scoring.w.pay_under": (-30, 0), "scoring.w.years_required": (-40, 0),
               "scoring.min_show": (0, 90), "notify.alert_score": (50, 95), "ai.daily_cap": (0, 100)}


def fake_get(values=None):
    """A `get` that answers from `values`, then from defaults, as the live store would."""
    values = values or {}

    def get(key):
        if key in values:
            return values[key]
        return registry.default_of(REGISTRY[key], get)
    return get


class Catalogue(unittest.TestCase):
    """Every setting is declared once, worded, grouped and given a usable default."""

    def test_every_key_of_the_spec_is_registered(self):
        self.assertEqual(sorted(set(SPEC_KEYS) - set(REGISTRY)), [])

    def test_every_setting_has_a_label_a_default_and_a_group(self):
        groups = {g[0] for g in registry.GROUPS} | {registry.INTERNAL_GROUP}
        for s in REGISTRY.values():
            with self.subTest(s.key):
                self.assertTrue(s.label.strip())
                self.assertIn(s.group_id, groups)
                if s.stored:
                    self.assertTrue(s.default is not None or s.default_fn is not None or s.kind == "hours",
                                    "a stored setting needs a default")
                    value = registry.default_of(s, fake_get())
                    if s.kind == "hours" and value is None:
                        continue
                    self.assertEqual(parse.check(s, value), value, "the default passes its own checks")

    def test_settings_groups_follow_the_mock(self):
        ids = [g[0] for g in registry.GROUPS]
        mock_ids = [g[0] for g in MOCK_GROUPS]
        self.assertEqual([i for i in ids if i not in ADDED_GROUPS], mock_ids)
        for added, after in ADDED_GROUPS.items():
            self.assertEqual(ids.index(added), ids.index(after) + 1, added)
        names = dict((g[0], g[1]) for g in registry.GROUPS)
        for group, name, labels in MOCK_GROUPS:
            with self.subTest(group):
                self.assertEqual(names[group], name)
                have = [s.label for s in registry.in_group(group)]
                self.assertEqual([lab for lab in labels if lab not in have], [])
                order = [have.index(lab) for lab in labels]
                self.assertEqual(order, sorted(order), "the design's rows keep its order")

    def test_ranges_the_mock_shows_keep_their_ends(self):
        for key, (low, high) in MOCK_RANGES.items():
            with self.subTest(key):
                self.assertEqual((REGISTRY[key].min, REGISTRY[key].max), (low, high))

    def test_submit_is_locked_to_never(self):
        s = REGISTRY["applying.submit"]
        self.assertEqual((s.kind, s.default), ("locked", "Never"))
        with self.assertRaises(registry.SettingError):
            parse.check(s, "Always")

    def test_preferences_gather_how_hal_behaves_and_keep_each_key(self):
        """Testers asked for one place for the behaviour choices (2026-11-04); saved values keep their keys."""
        from jobhunt.core.settings import registry

        self.assertEqual([g[0] for g in registry.GROUPS][:3], ["you", "goal", "prefs"])
        prefs = sorted((s.order, k) for k, s in REGISTRY.items() if s.group == "prefs")
        self.assertEqual([k for _, k in prefs],
                         ["applying.window", "applying.bring_forward", "applying.close_popups",
                          "applying.keep_site_answers", "applying.press_next", "applying.wait_minutes",
                          "cvdocs.check_open", "updates.reload_pages"])
        # every one starts as Hal behaved before it could be chosen
        self.assertTrue(all(REGISTRY[k].default is True for k in ("applying.bring_forward", "applying.close_popups",
                                                                 "applying.keep_site_answers", "cvdocs.check_open",
                                                                 "updates.reload_pages")))

    def test_forms_can_open_in_this_window(self):
        """The choice waited for the helper to bring a form's tab forward before reading it. Proved by
        `test_engine_fixes.WithTheHelper.test_this_window_brings_the_tab_forward_before_reading`)."""
        s = REGISTRY["applying.window"]
        self.assertEqual([c[0] for c in s.choices], ["separate", "this"])
        self.assertNotIn("coming later", s.help.lower())
        self.assertEqual(parse.check(s, "this"), "this")

    def test_secrets_and_private_answers_have_no_stored_value(self):
        for s in REGISTRY.values():
            if s.kind in ("secret", "private"):
                with self.subTest(s.key):
                    self.assertFalse(s.stored)
                    self.assertIsNone(s.default)

    def test_area_settings_files_are_found_without_schema_steps_or_commands(self):
        # migrations/0003_settings.py ends in "_settings" too, but holds no settings.
        found = registry._area_settings_modules()
        self.assertFalse([name for name in found if ".migrations." in name or name.startswith("jobhunt.cli.")])

    def test_a_duplicate_key_raises_at_registration(self):
        with self.assertRaises(ValueError):
            registry.register(Setting("you.name", "text", "", "Your name again"))

    def test_an_inconsistent_entry_is_refused(self):
        bad = (Setting("tests.x", "text", "", "X", group="nowhere"),
               Setting("tests.y", "choice", "maybe", "Y", group="you", choices=(("yes", "Yes"),)),
               Setting("tests.z", "range", 99, "Z", group="you", min=0, max=10),
               Setting("tests.w", "bool", False, "W", group="you", visible_if="eval('1')"))
        for s in bad:
            with self.subTest(s.key), self.assertRaises(ValueError):
                registry.register(s)
        self.assertFalse([k for k in REGISTRY if k.startswith("tests.")])

    def test_the_goal_rows_write_goal_versions(self):
        stored = [s for s in registry.in_group("goal") if s.stored]
        self.assertTrue(stored)
        self.assertTrue(all(s.goal for s in stored))
        self.assertFalse(REGISTRY["goal.mode"].rescore)


class Defaults(unittest.TestCase):
    """Defaults that depend on the person or the machine."""

    def test_the_kinds_of_role_follow_the_career_stage(self):
        get = fake_get({"goal.stage": "student"})
        self.assertIn("internship", registry.default_of(REGISTRY["goal.kinds"], get))
        self.assertEqual(registry.default_of(REGISTRY["goal.study_status"], get), "at_uni")
        self.assertEqual(registry.default_of(REGISTRY["scoring.preset"], get), "internship_seeker")
        get = fake_get({"goal.stage": "experienced"})
        self.assertEqual(registry.default_of(REGISTRY["goal.experience_years"], get), 5)

    def test_contact_defaults_come_from_the_persons_details(self):
        get = fake_get({"you.name": "Robin Testwell", "you.email": "robin@example.org", "you.city": "Leeds"})
        self.assertEqual(registry.default_of(REGISTRY["you.preferred_name"], get), "Robin")
        self.assertEqual(registry.default_of(REGISTRY["you.location_line"], get), "Leeds, United Kingdom")
        self.assertEqual(registry.default_of(REGISTRY["notify.daily_to"], get), "robin@example.org")
        self.assertEqual(registry.default_of(REGISTRY["email.address"], get), "robin@example.org")

    def test_local_ai_prepares_documents_ahead_and_claude_does_not(self):
        self.assertEqual(registry.default_of(REGISTRY["scoring.prepare_ahead"], fake_get({"ai.provider": "local"})), 5)
        claude = fake_get({"ai.provider": "claude_cli"})
        self.assertEqual(registry.default_of(REGISTRY["scoring.prepare_ahead"], claude), 0)
        self.assertEqual(registry.default_of(REGISTRY["ai.parallel"], claude), 2)
        self.assertEqual(registry.default_of(REGISTRY["ai.model.writer"], claude), "sonnet")

    def test_the_suggested_ai_is_local_only_when_ollama_answers_on_linux(self):
        # the probes are faked here; outside this test a test run never asks this computer's Ollama
        self.enterContext(mock.patch.dict(os.environ, {"HAL_TEST_REAL_AI": "1"}))
        self.enterContext(mock.patch.object(ai_catalog, "hal_answers", return_value=False))
        with mock.patch.object(sys, "platform", "linux"):
            with mock.patch.object(ai_catalog, "ollama_answers", return_value=True):
                self.assertEqual(ai_catalog.suggested_provider(fake_get()), "local")
            with mock.patch.object(ai_catalog, "ollama_answers", return_value=False):
                self.assertEqual(ai_catalog.suggested_provider(fake_get()), "claude_cli")
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(ai_catalog.suggested_provider(fake_get()), "claude_cli")

    def test_a_test_run_never_asks_this_computers_ollama(self):
        # regression: tests passed here only because this computer's Ollama answered, and failed on CI (2026-11-12)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(ai_catalog, "ollama_answers", side_effect=AssertionError("asked Ollama")):
            self.assertEqual(ai_catalog.suggested_provider(fake_get()), "claude_cli")

    def test_the_simple_menu_is_off_in_developer_mode(self):
        get = fake_get({"advanced.developer_mode": True})
        self.assertFalse(registry.default_of(REGISTRY["advanced.simple_menu"], get))
        self.assertTrue(registry.default_of(REGISTRY["advanced.simple_menu"],
                                            fake_get({"advanced.developer_mode": False})))


class Visibility(unittest.TestCase):
    """visible_if, developer-only rows and platforms."""

    def visible(self, key, values=None, developer=False, platform="linux"):
        return registry.visible(REGISTRY[key], fake_get(values), developer=developer, platform=platform)

    def test_a_row_shows_only_when_its_condition_holds(self):
        self.assertFalse(self.visible("you.visa_detail"))
        self.assertTrue(self.visible("you.visa_detail", {"you.right_to_work": "uk_visa"}))
        self.assertTrue(self.visible("ai.openai_url", {"ai.provider": "openai_compat"}))
        self.assertFalse(self.visible("ai.openai_url", {"ai.provider": "local"}))

    def test_developer_rows_and_the_add_on_group_need_developer_mode(self):
        self.assertFalse(self.visible("notify.weekly_review"))
        self.assertTrue(self.visible("notify.weekly_review", developer=True))
        self.assertFalse(self.visible("addon.enabled"))
        self.assertTrue(self.visible("addon.enabled", developer=True))

    def test_hals_own_window_is_offered_on_linux_only(self):
        self.assertTrue(self.visible("applying.mode", developer=True, platform="linux"))
        self.assertFalse(self.visible("applying.mode", developer=True, platform="windows"))

    def test_internal_keys_are_never_shown(self):
        self.assertFalse(self.visible("updates.held_commit", developer=True))

    def test_conditions_are_parsed_without_eval(self):
        self.assertEqual(registry.parse_condition("a.b == 'x' and c.d"), [("a.b", "==", "x"), ("c.d", None, None)])
        for bad in ("__import__('os')", "a.b == x", "a.b or c.d"):
            with self.subTest(bad), self.assertRaises(ValueError):
                registry.parse_condition(bad)


class LegacyMap(unittest.TestCase):
    """The old config keys map onto registered settings."""

    def test_every_mapped_setting_is_registered(self):
        for entry in legacy_map.LEGACY_KEYS:
            if entry.store in ("settings", "developer_env"):
                with self.subTest(entry.old):
                    self.assertIn(entry.setting, REGISTRY)

    def test_the_carer_answer_goes_to_the_private_store_and_never_to_you(self):
        entry = legacy_map.lookup("applicant.carer")
        self.assertEqual((entry.store, entry.setting), ("private", "carer"))
        self.assertFalse([e for e in legacy_map.LEGACY_KEYS if e.store == "settings" and "carer" in e.setting])

    def test_old_values_are_reworded_for_the_new_choices(self):
        entry = legacy_map.lookup("apply_browser")
        self.assertEqual(legacy_map.new_value(entry, "extension"), "helper")
        self.assertEqual(legacy_map.new_value(legacy_map.lookup("apply_window"), "same"), "this")
        for _, new in entry.values:
            parse.check(REGISTRY[entry.setting], new)

    def test_removed_keys_are_not_carried_over(self):
        self.assertEqual(legacy_map.lookup("signed_in_apply").store, "removed")
        self.assertEqual(legacy_map.lookup("ntfy_topic").store, "removed")
        with self.assertRaises(KeyError):
            legacy_map.lookup("no_such_key")


if __name__ == "__main__":
    unittest.main()

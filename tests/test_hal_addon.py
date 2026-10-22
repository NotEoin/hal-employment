"""The owner's Hal add-on: nothing happens without it; with it, the Pipeline note, the application
copies, hal.conf's models and the /jobs command, which names no one by itself.
"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_runner import fresh_home

from jobhunt import vault
from jobhunt.addons import hal
from jobhunt.addons.hal import mirror, models, pipeline
from jobhunt.core import settings


class AddOn(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.vault = self.folder / "Made-up Vault"
        self.vault.mkdir()

    def switch_on(self):
        settings.set("addon.enabled", True)
        settings.set("addon.vault", str(self.vault))

    def test_the_pipeline_note_is_written_only_with_the_hal_addon(self):
        settings.set("addon.vault", str(self.vault))
        self.assertIsNone(vault.write(self.con))
        self.assertFalse((self.vault / "Areas").exists())
        self.assertFalse(vault.VAULT.exists(), "old callers that test VAULT.exists() write nothing")
        settings.set("addon.enabled", True)
        note = vault.write(self.con)
        self.assertEqual(note, self.vault / "Areas" / "Job Search" / "Pipeline.md")
        self.assertIn("# Job search pipeline", note.read_text())
        self.assertEqual(vault.VAULT, self.vault)
        settings.set("addon.pipeline_note", False)
        self.assertIsNone(pipeline.write(self.con))

    def test_a_ticked_task_stays_ticked(self):
        self.switch_on()
        self.con.execute("""INSERT INTO emails (msgid, date, sender, subject, kind, company)
                            VALUES ('e1', datetime('now'), 'jo@example.org', 'Quick question', 'human', 'Riverside')""")
        self.con.commit()
        note = vault.write(self.con)
        text = note.read_text()
        line = next(x for x in text.splitlines() if "Quick question" in x)
        note.write_text(text.replace(line, line.replace("- [ ]", "- [x]")))
        self.assertIn(line.replace("- [ ]", "- [x]"), vault.write(self.con).read_text())

    def test_the_jobs_command_template_names_no_one(self):
        template = hal.TEMPLATE.read_text()
        self.assertEqual(set(re.findall(r"\{(\w+)\}", template)), {"first_name", "vault_note", "code_dir"})
        for word in ("City", "London", "8737", "vaults/"):
            self.assertNotIn(word, template)
        settings.set("you.name", "Robin Ashworth")
        self.switch_on()
        text = hal.render_jobs_command()
        self.assertIn("what needs Robin today", text)
        self.assertIn(str(self.vault / "Areas" / "Job Search" / "Pipeline.md"), text)

    def test_the_jobs_command_replaces_the_old_link_only_when_asked(self):
        target = self.folder / "commands" / "jobs.md"
        target.parent.mkdir()
        old = self.folder / "old-jobs.md"
        old.write_text("the old command")
        target.symlink_to(old)
        self.switch_on()
        self.assertIsNone(hal.install_jobs_command(target))
        settings.set("addon.jobs_command", True)
        self.assertEqual(hal.install_jobs_command(target), target)
        self.assertFalse(target.is_symlink())
        self.assertEqual(old.read_text(), "the old command")
        self.assertIn("Hal Employment", target.read_text())

    def test_the_jobs_command_follows_add_on_changes_but_never_from_a_test_home(self):
        from jobhunt.core import schedule_routes

        target = self.folder / "commands" / "jobs.md"
        self.enterContext(mock.patch.object(hal, "JOBS_COMMAND", target))
        self.switch_on()
        settings.set("addon.jobs_command", True)
        schedule_routes.install_jobs_command()
        self.assertFalse(target.exists(), "a test or worktree home never writes the real command")
        with mock.patch("jobhunt.core.health.in_own_home", return_value=False):
            schedule_routes.install_jobs_command("addon.vault", "", str(self.vault))
        self.assertIn("Pipeline.md", target.read_text())

    def test_application_notes_are_copied_only_into_a_folder_that_exists(self):
        note = self.folder / "pack.md"
        note.write_text("# Museum Assistant")
        copies = self.folder / "Applications"
        settings.set("addon.applications_mirror", str(copies))
        self.assertIsNone(mirror.copy(note))
        self.switch_on()
        self.assertIsNone(mirror.copy(note), "a folder that does not exist is never made")
        copies.mkdir()
        self.assertEqual(mirror.copy(note, "Riverside Trust - Museum Assistant.md").read_text(), "# Museum Assistant")

    def test_hal_conf_models_are_offered_as_choices(self):
        root = self.folder / "Hal"
        root.mkdir()
        (root / "hal.conf").write_text('HAL_MODEL="made-up-checker:8b"\nHAL_WRITER_MODEL=made-up-writer:14b  # big\n'
                                       "HAL_FAST_MODEL=made-up-checker:8b\nOTHER=1\n")
        settings.set("addon.hal_root", str(root))
        self.assertEqual(models.role_models(), {})
        self.switch_on()
        self.assertEqual(models.role_models(), {"writer": "made-up-writer:14b", "checker": "made-up-checker:8b",
                                                "quick": "made-up-checker:8b"})
        self.assertEqual(models.choices(), [("made-up-writer:14b", "made-up-writer:14b (Hal's writer model)"),
                                            ("made-up-checker:8b", "made-up-checker:8b (Hal's checker, quick model)")])


if __name__ == "__main__":
    unittest.main()

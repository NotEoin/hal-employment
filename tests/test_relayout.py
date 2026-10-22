"""The data task `docs.relayout_packs`, on made-up folders only: each old shared pack folder goes to
the job its `pack.md` names, and every other job that used the same folder is marked to make its documents
again. Regression: before numbered folders, two jobs with the same employer and title shared one folder.
"""
import json
import unittest

from support.world import World, job_id

from jobhunt import pack
from jobhunt.core import settings
from jobhunt.core.db import tasks
from jobhunt.docs import folders, relayout


class Relayout(unittest.TestCase):
    def setUp(self):
        self.w = self.enterContext(World())
        self.con = self.w.con
        settings.set("you.name", "Robin Testwell")
        self.owner = self.con.execute("SELECT * FROM jobs WHERE id = ?", (job_id("sorrel-ready"),)).fetchone()
        # A later advert with the same employer and title, which the old naming put in the same folder.
        self.con.execute("""INSERT INTO jobs (id, source, company, title, first_seen, last_seen, status)
                            SELECT 'lever:sorrel-again', source, company, title, first_seen, last_seen, 'ready'
                            FROM jobs WHERE id = ?""", (self.owner["id"],))
        self.con.commit()
        self.old = pack.PACKS / pack.slug(f"{self.owner['company']}-{self.owner['title']}")
        self.old.mkdir(parents=True)
        (self.old / "pack.md").write_text(f"# Sorrel Payments: Software Engineer, Graduate\n\n"
                                          f"- Drafted: 2026-10-01 | Job id: `{self.owner['id']}`\n\n"
                                          "## Cover letter\n\nThe first draft, not the edit.\n")
        (self.old / "Made-Up-Person-CV.pdf").write_bytes(b"%PDF-1.4 cv\n%%EOF\n")
        (self.old / "Made-Up-Person-Cover-Letter.pdf").write_bytes(b"%PDF-1.4 letter\n%%EOF\n")
        (pack.PACKS / "_base-cv").mkdir()
        self.before = sorted(p.name for p in self.old.iterdir())

    def test_old_folders_shared_by_two_jobs_are_resolved_by_job_id(self):
        counts = relayout.run(self.con)
        self.assertEqual((counts["moved"], counts["redraft"]), (1, 1))
        dest = folders.folder_for(self.con, self.owner, create=False)
        names = sorted(p.name for p in dest.iterdir())
        self.assertIn("Robin Testwell CV.pdf", names)
        self.assertIn("Robin Testwell Cover Letter.pdf", names)
        self.assertIn("pack.md", names)
        # The edited letter from the database, never the first draft in pack.md.
        self.assertEqual((dest / "letter.txt").read_text(), "A made-up letter.\n")
        self.assertEqual(json.loads((dest / "manifest.json").read_text())["job_id"], self.owner["id"])

        other = self.con.execute("SELECT docs_need_redraft FROM applications WHERE job_id = 'lever:sorrel-again'")
        self.assertEqual(other.fetchone()[0], 1)
        row = self.con.execute("SELECT text, why FROM activity WHERE job_id = 'lever:sorrel-again'").fetchone()
        self.assertEqual(row["text"], "Documents for Software Engineer, Graduate need making again, because an "
                                      "older job used the same folder")
        self.assertTrue(row["why"])
        self.assertEqual(sorted(p.name for p in self.old.iterdir()), self.before)

        again = relayout.run(self.con)
        self.assertEqual((again["moved"], again["redraft"]), (0, 0))

    def test_a_folder_no_job_owns_is_left_alone(self):
        (self.old / "pack.md").write_text("# Someone: x\n\n- Drafted: 2026-10-01 | Job id: `lever:gone`\n")
        counts = relayout.run(self.con)
        self.assertEqual((counts["moved"], counts["skipped"]), (0, 1))

    def test_the_task_runs_once_after_applications_are_numbered(self):
        tasks.load()
        self.assertEqual(tasks.TASKS["docs.relayout_packs"].after, 9)
        done = tasks.run_now("docs.relayout_packs")
        self.assertEqual(done["state"], "done")
        self.assertEqual(tasks.state(self.con, "docs.relayout_packs"), "done")


if __name__ == "__main__":
    unittest.main()

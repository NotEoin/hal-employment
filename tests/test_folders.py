"""Application folders: one numbered folder per application that always matches what was approved,
with `pack.md` kept for the command line and every file registered for the Documents page.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from support.world import World, job_id

from jobhunt import documents, pack
from jobhunt.core import paths, settings
from jobhunt.docs import files as registry
from jobhunt.docs import folders


def fake_render(job, docs, out=None):
    """Stands in for the PDF makers: one CV and one letter PDF holding the letter's first words."""
    out = out or documents._OUT.get()
    out.mkdir(parents=True, exist_ok=True)
    cv_pdf = out / "Made-Up-Maker-CV.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4 cv\n%%EOF\n")
    letter_pdf = out / "Robin Testwell Cover Letter.pdf"
    letter_pdf.write_bytes(b"%PDF-1.4 " + docs.get("letter", "").encode()[:20] + b"\n%%EOF\n")
    return {"cv": str(cv_pdf), "letter": str(letter_pdf)}


class Folders(unittest.TestCase):
    def setUp(self):
        self.w = self.enterContext(World())
        self.con = self.w.con
        self.enterContext(mock.patch.object(documents, "render", fake_render))
        settings.set("you.name", "Robin Testwell")
        self.job = self.con.execute("SELECT * FROM jobs WHERE id = ?", (job_id("sorrel-ready"),)).fetchone()

    def approve(self, letter: str) -> Path:
        docs = {**documents.load(self.con, self.job), "letter": letter, "message": "A short made-up note.",
                "answers": [{"question": "Why us?", "answer": "A made-up answer."}], "approved": "2026-10-22T10:44:00Z"}
        documents.save(self.con, self.job, docs)
        return documents.pack_dir(self.job, self.con, create=False)

    def test_each_application_has_a_numbered_folder_matching_what_you_approved(self):
        folder = self.approve("Dear team, the first made-up letter.")
        self.assertEqual(folder.parent, paths.applications_dir())
        self.assertRegex(folder.name, r"^\d{4} Sorrel Payments - Software Engineer, Graduate$")
        self.assertEqual((folder / "letter.txt").read_text(), "Dear team, the first made-up letter.\n")
        self.assertIn("**Why us?**", (folder / "answers.md").read_text())
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual((manifest["job_id"], manifest["company"]), (self.job["id"], "Sorrel Payments"))
        by_name = {f["name"]: f for f in manifest["files"]}
        self.assertIn("Robin Testwell CV.pdf", by_name)
        for name, f in by_name.items():
            self.assertEqual(f["sha256"], registry.sha256(folder / name), name)
            self.assertEqual(f["approved_at"], "2026-10-22T10:44:00Z")

        again = self.approve("Dear team, the second made-up letter.")
        self.assertEqual(again, folder)
        self.assertEqual((folder / "letter.txt").read_text(), "Dear team, the second made-up letter.\n")
        self.assertEqual([p.name for p in folder.parent.iterdir() if p.name.startswith(".")], [])
        rows = self.con.execute("SELECT kind, text FROM app_documents WHERE job_id = ?", (self.job["id"],)).fetchall()
        self.assertIn(("letter_txt", "Dear team, the second made-up letter."), [tuple(r) for r in rows])
        number = self.con.execute("SELECT number FROM applications WHERE job_id = ?", (self.job["id"],)).fetchone()[0]
        self.assertEqual(folder.name[:4], f"{number:04d}")

    def test_two_jobs_with_the_same_title_never_share_a_folder(self):
        self.con.execute("""INSERT INTO jobs (id, source, company, title, first_seen, last_seen, status)
                            SELECT 'lever:sorrel-again', source, company, title, first_seen, last_seen, 'ready'
                            FROM jobs WHERE id = ?""", (self.job["id"],))
        other = self.con.execute("SELECT * FROM jobs WHERE id = 'lever:sorrel-again'").fetchone()
        self.assertNotEqual(documents.pack_dir(self.job, self.con), documents.pack_dir(other, self.con))

    def test_the_folder_keeps_pack_md_for_the_cli(self):
        md = pack.build(self.con, self.job["id"], use_web=False)
        self.assertIn(f"Job id: `{self.job['id']}`", md.read_text())
        folder = self.approve("Dear team, a made-up letter.")
        self.assertTrue((folder / "pack.md").is_file())
        self.assertNotIn("pack_md", [k for k, _ in folders.listed_files(folder)])
        self.assertIn("Role", documents.read_pack(self.job))

    def test_a_failed_approval_leaves_the_folder_as_it_was(self):
        folder = self.approve("Dear team, the kept letter.")

        def broken(job, docs, out=None):
            raise OSError("the made-up PDF maker failed")

        with mock.patch.object(documents, "render", broken), self.assertRaises(OSError):
            self.approve("Dear team, a letter that never lands.")
        self.assertEqual((folder / "letter.txt").read_text(), "Dear team, the kept letter.\n")
        self.assertEqual([p.name for p in folder.parent.iterdir() if p.name.startswith(".")], [])

    def test_an_old_shared_pack_is_read_only_by_the_job_it_names(self):
        old = pack.PACKS / pack.slug(f"{self.job['company']}-{self.job['title']}")
        old.mkdir(parents=True)
        (old / "pack.md").write_text("# Sorrel: x\n\n- Drafted: 2026-10-01 | Job id: `lever:someone-else`\n\n"
                                     "## Role\n\nNot this job.\n")
        self.assertEqual(documents.read_pack(self.job), {})
        (old / "pack.md").write_text(f"# Sorrel: x\n\n- Drafted: 2026-10-01 | Job id: `{self.job['id']}`\n\n"
                                     "## Role\n\nThis job.\n")
        self.assertEqual(documents.read_pack(self.job)["Role"], "This job.")


if __name__ == "__main__":
    unittest.main()

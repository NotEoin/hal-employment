"""The code project reader: the stack from manifests, languages by size, tests and CI, who made the commits, and
the AI's quoted summary, for made-up projects built in temporary folders with `git init`.

The person is Avery Quillon (avery@example.org); Robin Testwell and Sam Lowfield are made-up classmates.
"""
import json
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from support.repos import AVERY, ROBIN, SAM
from test_ingest import home
from test_sections_pipeline import zip_of

from jobhunt import ai
from jobhunt.ai.fake import FakeBackend
from jobhunt.core import settings
from jobhunt.profile import store as profile_store
from jobhunt.sections import pipeline, readers, repo, stack, store
from jobhunt.sections.pipeline import Incoming

README = ("# Tide gauge\n\nA made-up dashboard that reads tide heights for Harrowgate Bay.\n\n"
          "It fetches readings every ten minutes and draws them as a chart.\n\n"
          "Used by 40 sailors in the made-up Harrowgate Sailing Club.\n")
PYPROJECT = ('[project]\nname = "tide-gauge"\ndescription = "Tide heights for a made-up bay"\n'
             'license = "MIT"\nkeywords = ["tides", "dashboard"]\n'
             'dependencies = [\n  "torch>=2.0",\n  "fastapi",\n  "tidewords==1.2",\n]\n')


def me() -> None:
    """Tells Hal the person is Avery Quillon."""
    settings.set("you.name", AVERY[0])
    settings.set("you.email", AVERY[1])


class Stack(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def tools(self, files: dict) -> dict[str, stack.Dep]:
        repos.write(self.root, files)
        return {d.tool: d for d in stack.read(self.root)}

    def test_manifests_name_their_direct_dependencies_as_tools_with_file_and_line(self):
        got = self.tools({"pyproject.toml": PYPROJECT, "web/package.json": json.dumps(
            {"dependencies": {"react": "^18", "@angular/core": "17"}, "devDependencies": {"vitest": "1"}})})
        self.assertEqual((got["PyTorch"].file, got["PyTorch"].line, got["PyTorch"].said),
                         ("pyproject.toml", 7, '"torch>=2.0"'))
        self.assertIn("FastAPI", got)
        self.assertIn("tidewords", got)          # an unknown package keeps its own name
        self.assertEqual(got["React"].file, "web/package.json")
        self.assertIn("Vitest", got)
        self.assertIn("Angular", got)

    def test_each_ecosystem_is_read_and_indirect_go_modules_are_not(self):
        got = self.tools({
            "requirements-dev.txt": "# tools\npandas>=2\n-r base.txt\n",
            "go.mod": ("module x\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.0\n"
                       "\tgolang.org/x/sys v0.1.0 // indirect\n)\n"),
            "Cargo.toml": '[package]\nname = "x"\n[dependencies]\ntokio = "1"\n',
            "Gemfile": "source 'https://rubygems.org'\ngem 'rails', '7.1'\n",
            "DESCRIPTION": "Package: tides\nImports: ggplot2,\n    dplyr (>= 1.0)\nSuggests: testthat\n",
            "setup.cfg": "[options]\ninstall_requires =\n    flask\n    scipy>=1\n[options.extras_require]\n",
            "App.csproj": '<Project><ItemGroup><PackageReference Include="xunit" Version="2" /></ItemGroup></Project>',
            "environment.yml": "dependencies:\n  - python=3.11\n  - numpy\n  - pip\n",
            "Dockerfile": "FROM python:3.12-slim\n",
            "project.godot": "config_version=5\n"})
        for tool in ("pandas", "Gin", "Tokio", "Ruby on Rails", "ggplot2", "dplyr", "xUnit", "NumPy", "Docker",
                     "Godot"):
            self.assertIn(tool, got)
        self.assertNotIn("golang.org/x/sys", got)
        self.assertTrue({"Flask", "SciPy"} <= set(got))
        self.assertNotIn("testthat", got)
        self.assertNotIn("python", {t.lower() for t in got})


class Folder(unittest.TestCase):
    def test_languages_are_counted_by_size_without_vendored_generated_or_lfs_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos.write(root, {"app.py": "x = 1\n" * 100, "web/app.ts": "let a = 1;\n" * 10,
                               "vendor/big.js": "var a;\n" * 5000, "app.min.js": "a" * 9000,
                               "proto/tide_pb2.py": "y = 2\n" * 5000, "package-lock.json": "{}",
                               "data/model.py": "version https://git-lfs.github.com/spec/v1\noid sha256:ab\n"})
            got = repo.languages(repo.files(root))
        self.assertEqual([lang for lang, _ in got], ["Python", "TypeScript"])
        self.assertEqual(got[0][1], len("x = 1\n" * 100))

    def test_test_files_and_the_ci_provider_are_found(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos.write(root, {"tests/test_tides.py": "", "src/chart.test.ts": "", "src/chart.ts": "",
                               ".github/workflows/ci.yml": "on: push\n"})
            self.assertEqual((repo.tests(root, repo.files(root)), repo.ci(root)), (2, "GitHub Actions"))

    def test_a_sole_authors_project_gives_a_project_card_with_its_stack_role_and_dates(self):
        with home() as (_, _, files):
            me()
            folder = repos.make(files / "tide-gauge", {"README.md": README, "pyproject.toml": PYPROJECT,
                                                       "tide/app.py": "print('tides')\n"},
                                [(AVERY, "2024-01-05T09:00:00Z", {}), (AVERY, "2024-06-20T09:00:00Z", {"b.py": "1\n"})])
            got = repo.read_folder(folder)
        project = [f for f in got.facts if f.kind == "project"]
        fields = {k: v for f in project for k, v in f.fields.items()}
        self.assertEqual(got.title, "tide-gauge")
        self.assertEqual(fields["role"], "sole author")
        self.assertEqual(fields["period"], "2024-01 to 2024-06")
        self.assertEqual(fields["tools"][:3], ["PyTorch", "FastAPI", "tidewords"])
        self.assertIn("Python", fields["tools"])
        self.assertEqual(got.date, "2024-06")
        self.assertIn("- Licence: MIT", got.text)
        self.assertIn("- Commits by you: 2 of 2", got.text)
        self.assertEqual(got.details["commits"], 2)
        self.assertIn({"PyTorch", "Python"}, [{f.fields["name"] for f in got.facts if f.kind == "skill"} & {"PyTorch",
                                                                                                         "Python"}])
        for f in got.facts:
            self.assertIn(" ".join(f.quote.quote.split()), " ".join(got.text.split()), f)

    def test_a_shared_project_says_the_persons_share_of_commits_and_contributors(self):
        with home() as (_, _, files):
            me()
            folder = repos.make(files / "harbour", {"README.md": "# Harbour\n"},
                                [(AVERY, "2024-01-01T00:00:00Z", {}),
                                 (ROBIN, "2024-01-02T00:00:00Z", {"a": "1"}),
                                 (SAM, "2024-01-03T00:00:00Z", {"b": "1"}),
                                 (ROBIN, "2024-01-04T00:00:00Z", {"c": "1"})])
            got = repo.read_folder(folder)
        self.assertEqual(got.details["role"], "contributor (25% of commits, 3 contributors)")

    def test_commits_from_a_github_noreply_address_count_by_the_persons_handle(self):
        who = repo.Person(handles=frozenset({"averyq"}))
        self.assertTrue(who.made("Someone", f"1234+averyq@{repo.NOREPLY_DOMAIN}"))
        self.assertFalse(who.made("Avery", "avery@other.example.org"))

    def test_a_project_with_none_of_the_persons_commits_is_not_theirs_and_gives_no_card(self):
        with home() as (_, _, files):
            me()
            folder = repos.make(files / "lent", {"README.md": "# Lent\n", "app.py": "1\n"},
                                [(ROBIN, "2023-02-01T00:00:00Z", {})])
            got = repo.read_folder(folder)
            theirs = repo.read_folder(folder, theirs=True)
        self.assertEqual((got.facts, got.details["not_yours"]), ([], True))
        self.assertIn("None of the commits are yours", got.text)
        self.assertTrue([f for f in theirs.facts if f.kind == "project"])

    def test_with_no_git_history_dates_come_from_the_files_and_there_is_no_role(self):
        with home() as (_, _, files):
            me()
            repos.write(files / "copy", {"README.md": "# Copy\n", "main.go": "package main\n"})
            got = repo.read_folder(files / "copy")
        fields = {k: v for f in got.facts for k, v in f.fields.items()}
        self.assertNotIn("role", fields)
        self.assertTrue(fields["period"])
        self.assertIn("- Files dated:", got.text)

    def test_the_reflog_never_counts_as_history(self):
        with home() as (_, _, files):
            me()
            folder = repos.make(files / "cloned", {"README.md": "# Cloned\n"}, [(ROBIN, "2023-02-01T00:00:00Z", {})])
            log = folder / ".git" / "logs" / "HEAD"
            log.write_text(log.read_text() + f"0 1 {AVERY[0]} <{AVERY[1]}> 1700000000 +0000\tclone: from x\n")
            got = repo.read_folder(folder)
        self.assertTrue(got.details["not_yours"])

    def test_env_files_keys_and_a_private_readme_are_never_read(self):
        with home() as (_, _, files):
            me()
            folder = repos.make(files / "keys", {
                "README.md": "# Keys\n\nSort code: 12-34-56\nAccount number: 12345678\n",
                ".env": "API_KEY=abcdefgh12345678", "deploy.pem": "-----BEGIN", "app.py": "1\n"})
            got = repo.read_folder(folder)
        self.assertNotIn("12345678", got.text)
        self.assertNotIn("API_KEY", got.text)
        self.assertNotIn("deploy.pem", got.text)

    def test_the_ai_summarises_from_the_readme_and_each_fact_quotes_a_real_line(self):
        reply = json.dumps({"summary": "A dashboard of tide heights. It draws charts. Sailors use it. Extra.",
                            "facts": [{"kind": "what", "text": "Reads tides.", "file": "README.md",
                                       "quote": "A made-up dashboard that reads tide heights for Harrowgate Bay."},
                                      {"kind": "result", "text": "Used by sailors.", "file": "README.md",
                                       "quote": "Used by 40 sailors in the made-up Harrowgate Sailing Club."},
                                      {"kind": "how", "text": "Invented.", "file": "README.md",
                                       "quote": "Built with Kubernetes on a supercomputer."}]})
        with home() as (_, _, files), ai.use(FakeBackend({repo.PURPOSE: reply})) as fake:
            me()
            folder = repos.make(files / "tide", {"README.md": README})
            got = repo.read_folder(folder)
        self.assertEqual(got.summary, "A dashboard of tide heights. It draws charts. Sailors use it.")
        quoted = [(f.quote.field, f.quote.place, f.quote.line) for f in got.facts if f.quote.field in ("what", "result",
                                                                                                        "how")]
        self.assertEqual(quoted, [("what", "README.md", 3), ("result", "README.md", 7)])
        self.assertNotIn("Kubernetes", json.dumps([f.fields for f in got.facts]))
        self.assertIn("=== README.md ===\n1: # Tide gauge", fake.log[0].user)

    def test_without_the_ai_the_summary_is_the_projects_own_description(self):
        with home() as (_, _, files):
            me()
            folder = repos.make(files / "tide", {"README.md": README, "pyproject.toml": PYPROJECT})
            got = repo.read_folder(folder)
        self.assertEqual(got.summary, "Tide heights for a made-up bay")


class Sources(unittest.TestCase):
    def test_a_code_project_folder_added_to_projects_becomes_a_project_card(self):
        with home() as (con, _, files):
            me()
            folder = repos.make(files / "tide-gauge", {"README.md": README, "pyproject.toml": PYPROJECT})
            source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
            out = pipeline.read(con, source_id)
            cards = profile_store.items(con, "project", hidden=False)
            got = store.get(con, source_id)
        self.assertEqual(out["state"], "read")
        self.assertEqual([c["fields"]["name"] for c in cards], ["tide-gauge"])
        self.assertEqual(cards[0]["fields"]["role"], "sole author")
        self.assertEqual(got["not_yours"], False)

    def test_a_project_that_is_not_yours_gives_a_card_once_you_say_it_is(self):
        with home() as (con, _, files):
            me()
            folder = repos.make(files / "lent", {"README.md": "# Lent\n", "a.py": "1\n"},
                                [(ROBIN, "2023-02-01T00:00:00Z", {})])
            source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
            pipeline.read(con, source_id)
            before = (store.get(con, source_id)["not_yours"], profile_store.items(con, "project", hidden=False))
            store.change(con, source_id, {"theirs": True})
            pipeline.read(con, source_id)
            after = (store.get(con, source_id), profile_store.items(con, "project", hidden=False))
        self.assertEqual(before, (True, []))
        self.assertTrue(after[0]["theirs"])
        self.assertEqual([c["fields"]["name"] for c in after[1]], ["lent"])

    def test_an_uploaded_zip_of_a_code_project_is_read_by_the_code_project_reader(self):
        data = zip_of({"tide-gauge/README.md": README, "tide-gauge/pyproject.toml": PYPROJECT,
                       "tide-gauge/tide/app.py": "print(1)\n"})
        with home() as (con, _, _):
            me()
            source_id = pipeline.add_uploads(con, "project", [Incoming("tide-gauge.zip", data)], doc_kind="upload")[
                "added"][0]
            pipeline.read(con, source_id)
            cards = profile_store.items(con, "project", hidden=False)
        self.assertEqual(cards[0]["fields"]["name"], "tide-gauge")
        self.assertIn("PyTorch", cards[0]["fields"]["tools"])

    def test_an_uploaded_folder_that_is_not_code_is_a_project_named_after_it(self):
        with home() as (con, _, _):
            source_id = pipeline.add_uploads(con, "project", [
                Incoming("poster.md", b"# Harbour poster\n\nA made-up exhibition poster.\n", "Harbour show/poster.md")],
                doc_kind="upload")["added"][0]
            pipeline.read(con, source_id)
            cards = profile_store.items(con, "project", hidden=False)
        self.assertEqual([c["fields"]["name"] for c in cards], ["Harbour show"])

    def test_the_project_readers_are_ready(self):
        self.assertTrue(readers.ready("repo"))
        self.assertTrue(readers.ready("project_files"))


if __name__ == "__main__":
    unittest.main()

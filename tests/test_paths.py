"""Where Hal keeps its files: the dev override, the legacy layout, the test guard, and the rule
that no module builds a data path inside the code folder.
"""
import ast
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import paths

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "jobhunt"
RUN_TIMEOUT_S = 60
# Only these modules may name the old in-checkout folders: they find or move the owner's old data.
MAY_NAME_OLD_FOLDERS = {"core/paths.py", "core/importer.py", "core/importer_usb.py"}
DATA_FUNCTIONS = ("config_dir", "data_dir", "cache_dir", "documents_dir", "helper_dir", "record_default", "db_path",
                  "private_path", "browser_profile", "shots_dir", "cv_dir", "backups_dir", "logs_dir", "state_dir",
                  "legacy_packs_dir", "claude_work_dir")


def temp_dir(case: unittest.TestCase) -> Path:
    """A temporary folder removed after the test."""
    tmp = tempfile.TemporaryDirectory(prefix="hal-paths-")
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name).resolve()


class Override(unittest.TestCase):
    """`HAL_EMPLOYMENT_HOME` and `set_override` put every folder in one place."""

    def test_dev_override_puts_everything_under_one_folder(self):
        home = temp_dir(self)
        with paths.set_override(home):
            for name in DATA_FUNCTIONS:
                with self.subTest(name):
                    self.assertTrue(getattr(paths, name)().is_relative_to(home), name)
            self.assertEqual(paths.data_dir(), home / "data")
            self.assertEqual(paths.config_dir(), home / "config")
            self.assertEqual(paths.cache_dir(), home / "cache")
            self.assertEqual(paths.record_default(), home / "record")
            self.assertEqual(paths.db_path(), home / "data" / "jobs.db")
            self.assertEqual(paths.applications_dir(), home / "documents" / "Applications")
            self.assertFalse(paths.is_legacy_layout())

    def test_the_environment_variable_works_like_set_override(self):
        home = temp_dir(self)
        with mock.patch.dict(os.environ, {paths.ENV_HOME: str(home)}):
            self.assertEqual(paths.data_dir(), home / "data")

    def test_ensure_makes_private_folders_and_marks_fresh_data(self):
        home = temp_dir(self)
        with paths.set_override(home):
            paths.ensure()
            paths.ensure()
            self.assertTrue((home / "data" / paths.MARKER).is_file())
            self.assertTrue(paths.state_dir().is_dir() and paths.logs_dir().is_dir())
            if os.name != "nt":
                self.assertEqual(paths.config_dir().stat().st_mode & 0o777, 0o700)
                self.assertEqual(paths.data_dir().stat().st_mode & 0o777, 0o700)


class Guard(unittest.TestCase):
    """Tests can never reach the person's real folders."""

    def test_paths_raise_real_data_in_tests_without_an_override(self):
        env = {k: v for k, v in os.environ.items() if k != paths.ENV_HOME} | {paths.ENV_TEST: "1"}
        with mock.patch.dict(os.environ, env, clear=True):
            for name in DATA_FUNCTIONS:
                with self.subTest(name), self.assertRaises(paths.RealDataInTests):
                    getattr(paths, name)()
            self.assertIsNotNone(paths.code_dir())

    def run_python(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
        home = temp_dir(self)
        env = {k: v for k, v in os.environ.items() if k not in (paths.ENV_HOME, paths.ENV_TEST)}
        env |= {"HOME": str(home), "XDG_DATA_HOME": str(home / "xdg-data"), "XDG_CONFIG_HOME": str(home / "xdg-config"),
                "PYTHONPATH": str(REPO)}
        return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=RUN_TIMEOUT_S, check=False)

    def test_paths_raise_under_the_test_runner_even_without_hal_test_mode(self):
        folder = temp_dir(self)
        (folder / "test_forgetful.py").write_text(textwrap.dedent("""
            import unittest
            from jobhunt.core import paths

            class Forgetful(unittest.TestCase):
                def test_data_dir(self):
                    with self.assertRaises(paths.RealDataInTests):
                        paths.data_dir()
        """), encoding="utf-8")
        done = self.run_python(["-m", "unittest", "discover", "-s", str(folder)], folder)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("OK", done.stderr)

    def test_a_program_that_is_not_the_test_runner_gets_its_folders(self):
        done = self.run_python(["-c", "from jobhunt.core import paths; print(paths.data_dir().name)"], REPO)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), paths.APP_DIR)

    @unittest.skipIf(os.name == "nt", "the worktree guard is for Linux and macOS")
    def test_a_build_worktree_never_reaches_the_real_folders(self):
        # a server started in a worktree without a temporary home created the real data folder (2026-10-22)
        account = temp_dir(self)
        code = account / "worktrees" / "feature"
        code.mkdir(parents=True)
        (code / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in (paths.ENV_HOME, paths.ENV_TEST, "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")}
        with mock.patch.object(paths, "_account_home", return_value=account), \
             mock.patch.object(paths, "code_dir", return_value=code), \
             mock.patch.object(paths, "_under_test_runner", return_value=False), \
             mock.patch.object(paths, "_linux_documents", return_value=account / "Documents"):
            with mock.patch.dict(os.environ, env | {"HOME": str(account)}, clear=True):
                for name in DATA_FUNCTIONS:
                    with self.subTest(name), self.assertRaises(paths.RealDataFromWorktree):
                        getattr(paths, name)()
            with mock.patch.dict(os.environ, env | {"HOME": str(code / ".tmp-home")}, clear=True):
                self.assertTrue(paths.data_dir().is_relative_to(code))
            (code / ".git").unlink()
            (code / ".git").mkdir()
            with mock.patch.dict(os.environ, env | {"HOME": str(account)}, clear=True):
                self.assertTrue(paths.data_dir().is_relative_to(account))

    def test_importing_support_gives_a_run_with_no_variables_a_temporary_home(self):
        # Regression: a suite run by hand with no variables failed at import instead of using a temp home
        script = ("import os, sys; sys.path.insert(0, 'tests'); import support; from jobhunt.core import paths; "
                  "print(os.environ['HAL_TEST_MODE'], paths.data_dir().parent == paths.home_override())")
        done = self.run_python(["-c", script], REPO)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["1", "True"])

    def test_every_test_module_imports_support_before_jobhunt(self):
        late = []
        for file in sorted((REPO / "tests").rglob("test_*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            seen_support = False
            for node in tree.body:
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                if any(n == "support" or n.startswith("support.") for n in names):
                    seen_support = True
                if any(n == "jobhunt" or n.startswith("jobhunt.") for n in names) and not seen_support:
                    late.append(f"{file.relative_to(REPO)}:{node.lineno}")
        self.assertEqual(late, [], "import support before jobhunt, so the temporary home is set first")


class Layout(unittest.TestCase):
    """The folder resolution order, run against a made-up checkout and made-up platform folders."""

    def setUp(self):
        root = temp_dir(self)
        self.code = root / "checkout"
        (self.code / "data").mkdir(parents=True)
        (self.code / ".git").mkdir()
        (self.code / "data" / "jobs.db").write_bytes(b"")
        self.xdg_data, self.xdg_config = root / "xdg-data", root / "xdg-config"
        env = {k: v for k, v in os.environ.items() if k != paths.ENV_HOME}
        env |= {"XDG_DATA_HOME": str(self.xdg_data), "XDG_CONFIG_HOME": str(self.xdg_config)}
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(paths, "code_dir", lambda: self.code),
                      # the guard is what these tests switch off on purpose, to see the real resolution order
                      mock.patch.object(paths, "_guarded", lambda: None)):
            patch.start()
            self.addCleanup(patch.stop)

    @unittest.skipIf(os.name == "nt", "the legacy layout is the owner's Linux checkout")
    def test_the_legacy_layout_is_used_until_the_data_is_moved(self):
        self.assertTrue(paths.is_legacy_layout())
        self.assertEqual(paths.data_dir(), self.code / "data")
        self.assertEqual(paths.legacy_packs_dir(), self.code / "packs")
        moved = self.xdg_data / paths.APP_DIR
        moved.mkdir(parents=True)
        (moved / paths.MARKER).write_text("{}", encoding="utf-8")
        self.assertFalse(paths.is_legacy_layout())
        self.assertEqual(paths.data_dir(), moved)
        self.assertEqual(paths.legacy_packs_dir(), moved / "legacy-packs")

    def test_a_folder_hal_installed_is_never_the_legacy_layout(self):
        (self.code / ".git").rmdir()
        self.assertFalse(paths.is_legacy_layout())
        self.assertNotEqual(paths.data_dir(), self.code / "data")

    def test_paths_toml_moves_the_data_but_never_to_an_imported_copy(self):
        config = self.xdg_config / paths.APP_DIR
        config.mkdir(parents=True)
        elsewhere = self.code.parent / "elsewhere"
        (config / paths.PATHS_FILE).write_text(f'data_dir = "{elsewhere.as_posix()}"\n', encoding="utf-8")
        self.assertEqual(paths.data_dir(), elsewhere)
        old = self.code.parent / "data.imported-2026-10-22"
        (config / paths.PATHS_FILE).write_text(f'data_dir = "{old.as_posix()}"\n', encoding="utf-8")
        with self.assertRaises(RuntimeError):
            paths.data_dir()


class CodeFolder(unittest.TestCase):
    """Data never lives in the code folder, so a `git pull` can never touch it."""

    @staticmethod
    def builds_old_folder(node: ast.AST) -> bool:
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            return False
        right = node.right
        if not (isinstance(right, ast.Constant) and right.value in ("data", "packs")):
            return False
        left = ast.unparse(node.left)
        return "__file__" in left or left.split(".")[-1] == "ROOT" or "code_dir()" in left

    def test_no_module_builds_a_path_under_the_code_folder(self):
        found = []
        for file in sorted(PACKAGE.rglob("*.py")):
            rel = file.relative_to(PACKAGE).as_posix()
            if rel in MAY_NAME_OLD_FOLDERS:
                continue
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if self.builds_old_folder(node):
                    found.append(f"jobhunt/{rel}:{node.lineno}")
        self.assertEqual(found, [], "use jobhunt.core.paths for data folders")

    def test_the_check_finds_the_old_forms(self):
        for code in ('Path(__file__).resolve().parent.parent / "data"', 'ROOT / "packs"', 'config.ROOT / "data"'):
            with self.subTest(code):
                self.assertTrue(self.builds_old_folder(ast.parse(code, mode="eval").body))


if __name__ == "__main__":
    unittest.main()

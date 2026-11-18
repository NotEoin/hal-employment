"""scripts/dev/run_tests.py: a run that asks for the browser tests but cannot run them stops instead of passing.

Regression (2026-11-18): a local run meant for Python 3.11 ran on 3.13 without the browser extra, because
`uv run` swapped the environment for one on `.python-version`'s Python. Its 234 browser and practice-site tests
skipped, and the run passed in 38 s.
"""
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "dev"))
import run_tests


class BrowserCheck(unittest.TestCase):
    def test_a_browser_run_without_playwright_stops_before_any_test(self):
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.dict(os.environ, {"HAL_TEST_BROWSER": "1"}), \
                mock.patch.object(run_tests.importlib.util, "find_spec", return_value=None), \
                mock.patch.object(run_tests, "test_ids", side_effect=AssertionError("tests were listed")), \
                mock.patch.object(sys, "stdout", out):
            status = run_tests.main(["--share", "1/1"])
            out.seek(0)
            said = out.read()
        self.assertEqual(status, 2)
        self.assertIn("has no Playwright", said)
        self.assertIn(f"Python {run_tests.platform.python_version()}", said)

    def test_a_run_without_browser_tests_needs_no_playwright(self):
        with mock.patch.dict(os.environ, {"HAL_TEST_BROWSER": "0"}), \
                mock.patch.object(run_tests.importlib.util, "find_spec", return_value=None):
            self.assertIsNone(run_tests.browser_missing())


if __name__ == "__main__":
    unittest.main()

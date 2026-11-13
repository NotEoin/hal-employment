"""The shared MarkItDown loader: built once, and a missing extra is logged once."""
import builtins
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import markitdown_loader


class MarkItDownLoader(unittest.TestCase):
    def setUp(self):
        markitdown_converter = markitdown_loader.markitdown_converter
        markitdown_converter.cache_clear()
        self.addCleanup(markitdown_converter.cache_clear)

    def test_a_missing_extra_is_logged_once_and_gives_no_converter(self):
        real_import = builtins.__import__

        def refuse(name, *args, **kw):
            if name == "markitdown":
                raise ImportError("No module named 'markitdown'")
            return real_import(name, *args, **kw)

        with mock.patch.object(builtins, "__import__", refuse), \
                self.assertLogs("jobhunt.markitdown_loader", "INFO") as logged:
            self.assertIsNone(markitdown_loader.markitdown_converter())
            self.assertIsNone(markitdown_loader.markitdown_converter())
        self.assertEqual(len(logged.output), 1)

    def test_one_converter_is_shared_by_every_caller(self):
        first = markitdown_loader.markitdown_converter()
        self.assertIs(markitdown_loader.markitdown_converter(), first)


if __name__ == "__main__":
    unittest.main()

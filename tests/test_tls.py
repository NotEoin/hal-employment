"""Hal trusts the certificates Windows trusts (`jobhunt/core/tls.py`).

Windows 11 install test, 2026-10-23: on a fresh Windows 11 every job site failed with CERTIFICATE_VERIFY_FAILED,
so Hal's first search found nothing. These tests hold the fix: Windows processes hand certificate checks
to Windows through `truststore`, and every `python -m jobhunt` command does so before it starts.
"""
from __future__ import annotations

import sys
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - must come before jobhunt

from jobhunt.core import tls

ROOT = Path(__file__).resolve().parent.parent


class SystemTrust(unittest.TestCase):
    def test_windows_hands_certificate_checks_to_windows(self) -> None:
        fake = types.SimpleNamespace(inject_into_ssl=mock.Mock())
        with mock.patch.dict(sys.modules, {"truststore": fake}):
            self.assertTrue(tls.use_system_trust("win32"))
        fake.inject_into_ssl.assert_called_once_with()

    def test_linux_keeps_pythons_own_check(self) -> None:
        fake = types.SimpleNamespace(inject_into_ssl=mock.Mock())
        with mock.patch.dict(sys.modules, {"truststore": fake}):
            self.assertFalse(tls.use_system_trust("linux"))
        fake.inject_into_ssl.assert_not_called()

    def test_a_missing_truststore_is_logged_not_fatal(self) -> None:
        with mock.patch.dict(sys.modules, {"truststore": None}), self.assertLogs("jobhunt.core.tls", "ERROR"):
            self.assertFalse(tls.use_system_trust("win32"))

    def test_every_command_sets_trust_before_it_runs(self) -> None:
        from jobhunt import __main__ as entry

        with mock.patch.object(entry.tls, "use_system_trust") as trust, \
                mock.patch.object(sys, "argv", ["jobhunt", "stats"]), mock.patch.object(entry, "cmd_stats") as stats:
            stats.side_effect = lambda args: self.assertTrue(trust.called)
            entry.main()
        stats.assert_called_once()

    def test_windows_installs_get_truststore(self) -> None:
        deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        self.assertTrue(any(d.startswith("truststore") and "sys_platform == 'win32'" in d for d in deps), deps)


if __name__ == "__main__":
    unittest.main()

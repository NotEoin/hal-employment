"""Stand-ins for the contracts the importer calls: the secrets and private
answers stores and `platform.retire_legacy_timers`. This file can go once those modules
exist and the importer's tests run on the real stores with a fake keyring.

`installed()` puts them where the importer looks them up, for the length of a `with` block.
"""
from __future__ import annotations

import contextlib
import sys
import types
from collections.abc import Iterator
from unittest import mock


class Secrets(types.ModuleType):
    """`core.settings.secrets`: put, get, delete, status; values kept in memory."""

    def __init__(self):
        super().__init__("jobhunt.core.settings.secrets")
        self.values: dict[str, str] = {}
        self.puts: list[str] = []

    def put(self, name: str, value: str) -> str:
        self.values[name] = value
        self.puts.append(name)
        return "file"

    def get(self, name: str, *, interactive: bool = False) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

    def status(self, name: str) -> str:
        return "saved" if name in self.values else "not saved"


class Private(types.ModuleType):
    """`core.settings.private`: `save` merges answers; here it only remembers the keys and values."""

    def __init__(self):
        super().__init__("jobhunt.core.settings.private")
        self.saved: dict[str, str] = {}

    def save(self, answers: dict[str, str]) -> None:
        self.saved.update(answers)

    def status(self) -> dict[str, bool]:
        return {key: True for key in self.saved}


class Platform:
    """`core.platform.retire_legacy_timers` and its reverse, counting calls instead of touching systemd."""

    def __init__(self):
        self.retired = 0
        self.restored = 0

    def retire_legacy_timers(self) -> list[str]:
        self.retired += 1
        return ["jobhunt-discover.timer", "jobhunt-mail.timer", "jobhunt-digest.timer"]

    def restore_legacy_timers(self) -> list[str]:
        self.restored += 1
        return ["jobhunt-discover.timer", "jobhunt-mail.timer", "jobhunt-digest.timer"]


@contextlib.contextmanager
def installed() -> Iterator[tuple[Secrets, Private, Platform]]:
    """The three stand-ins, installed for the block."""
    from jobhunt.core import platform as real_platform

    secrets, private, platform = Secrets(), Private(), Platform()
    with mock.patch.dict(sys.modules, {secrets.__name__: secrets, private.__name__: private}), \
            mock.patch.object(real_platform, "retire_legacy_timers", platform.retire_legacy_timers, create=True), \
            mock.patch.object(real_platform, "restore_legacy_timers", platform.restore_legacy_timers, create=True):
        yield secrets, private, platform

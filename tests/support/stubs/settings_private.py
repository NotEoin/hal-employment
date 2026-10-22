"""Stand-in for the private answers store, `jobhunt.core.settings.private`, for the AI tests.

`saved(answers)` makes `value_for_form` and `values_for_guard` answer from `answers` while the block runs.
When the real module exists its two functions are patched; without it a module with the same contract
is put in its place. `values_for_guard` keeps only the free-text answers of 8 or more characters, as the
contract says.
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import types
from collections.abc import Iterator
from unittest import mock

NAME = "jobhunt.core.settings.private"
FREE_TEXT = ("disability_type", "adjustments")
MIN_CHARS = 8


def _guard_values(answers: dict[str, str]) -> list[str]:
    return [answers[k] for k in FREE_TEXT if len(answers.get(k) or "") >= MIN_CHARS]


@contextlib.contextmanager
def saved(answers: dict[str, str] | None = None) -> Iterator[dict[str, str]]:
    """The private answers are `answers` (none by default) while the block runs."""
    answers = dict(answers or {})
    try:
        real = importlib.import_module(NAME)
    except ModuleNotFoundError:
        real = None
    if real is not None:
        with mock.patch.object(real, "value_for_form", lambda key: answers.get(key)), \
                mock.patch.object(real, "values_for_guard", lambda: _guard_values(answers)):
            yield answers
        return
    stub = types.ModuleType(NAME)
    stub.value_for_form = lambda key: answers.get(key)
    stub.values_for_guard = lambda: _guard_values(answers)
    parent = importlib.import_module("jobhunt.core.settings")
    with mock.patch.dict(sys.modules, {NAME: stub}), mock.patch.object(parent, "private", stub, create=True):
        yield answers

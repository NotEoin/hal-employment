"""Hooks the AI layer adds to the settings store; its keys are in `core/settings/catalog/ai.py`.

Loaded by the settings registry in every process that reads settings, so a change made from the command
line keeps `ai.provider` stored just as one made in the app does.
"""
from __future__ import annotations

from ..core import settings
from . import provider

settings.on_change(provider.KEY, provider.keep_stored)

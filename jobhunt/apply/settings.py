"""Settings the browser helper adds to the Applying group: where the helper's folder is.

The row is information only; the Settings page shows the folder from `GET /api/v2/helpers/browsers`.
"""
from __future__ import annotations

from ..core.settings.registry import Setting, register

FOLDER_ORDER = 15   # just after "Browser Hal fills forms in"

register(
    Setting("applying.helper_folder", "info", None, "Browser helper folder", group="applying", order=FOLDER_ORDER,
            help="Your browser loads Hal's helper from this folder. Hal refreshes it every time it starts."),
)

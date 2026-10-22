"""Settings group "Updates": git-based updates and the browser helper's version.

There is no "Update key" row: updates come by git, so the GitHub sign-in row takes its place.
"""

from __future__ import annotations

from jobhunt.core.settings.registry import Setting, register

register(
    Setting("updates.version", "info", None, "Version", order=10),
    Setting("updates.auto_check", "bool", True, "Check for updates every day", order=20),
    Setting("updates.github", "info", None, "GitHub sign-in", remote="local_only", order=30),
    Setting("updates.apply", "button", None, "Update Hal", action="updates.apply", remote="label", order=40),
    Setting("updates.whats_new", "link", None, "What's new", to="whats-new", order=50),
    Setting("updates.rollback", "button", None, "Go back to the previous version", action="updates.rollback",
            remote="label", order=60),
    Setting("updates.helper", "info", None, "Browser helper", order=70),
    Setting("updates.held_commit", "text", "", "Version held back", group="internal"),
    Setting("updates.last_check", "text", "", "Last update check", group="internal"),
)

"""Settings group "Is Hal working?": rows come from the health checks of each area."""

from __future__ import annotations

from jobhunt.core.settings.registry import Setting, register

register(
    Setting("health.checks", "list", None, "Checks", order=10),
    Setting("health.help", "button", None, "Get help", action="health.help", order=20),
)

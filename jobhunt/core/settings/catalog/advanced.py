"""Settings group "Advanced": prompts, port, developer mode, theme and the like.

Also holds Hal's internal keys (group "internal", never shown) that do not belong to another group.
"""

from __future__ import annotations

from pathlib import Path

from jobhunt.core.settings.catalog._shared import choices, safe
from jobhunt.core.settings.registry import Setting, register

PORT = 8737
LOWEST_PORT = 1024    # ports below this need administrator rights on Linux
HIGHEST_PORT = 65535
PROMPTS_HELP = "Read or change exactly what Hal asks the AI to do. Reset puts back the original."


def developer_checkout(get=None) -> bool:
    """True when Hal runs from a developer's own git checkout rather than an install."""
    try:
        from jobhunt.core import paths
        return bool(paths.is_developer_checkout())
    except (ImportError, AttributeError):
        # Before core.paths exists: a .git folder in the code folder means a checkout.
        return (Path(__file__).resolve().parents[4] / ".git").exists()


register(
    Setting("advanced.prompts", "prompts", None, "Hal's instructions to the AI", help=PROMPTS_HELP, order=10),
    Setting("advanced.port", "int", PORT, "Port", restart=True, machine=True, min=LOWEST_PORT, max=HIGHEST_PORT,
            advanced=True, remote="label", order=60),
    Setting("advanced.developer_mode", "bool", False, "Developer mode", default_fn=developer_checkout, order=70),
    Setting("advanced.allow_old_helper", "bool", False, "Accept the old browser helper", dev_only=True, order=80),
    Setting("advanced.simple_menu", "bool", True, "Simple menu: 5 items and More", order=90,
            default_fn=lambda get: not safe(get, "advanced.developer_mode", False)),
    Setting("advanced.theme", "choice", "system", "Light or dark", order=100, choices=choices(
        ("system", "Follow this computer"), ("light", "Light"), ("dark", "Dark"))),
    Setting("advanced.log_level", "choice", "info", "Log detail", advanced=True, order=110,
            choices=choices(("info", "Normal"), ("debug", "Detailed"))),
    Setting("advanced.pause", "button", None, "Pause Hal", action="system.pause", remote="local_only", order=120),
    Setting("advanced.quit", "button", None, "Quit Hal", action="system.quit", remote="local_only", order=130),
    Setting("setup.first_app_seen", "bool", False, "First look at the app", group="internal"),
    Setting("helper.allowed_ids", "tags", [], "Browser helper ids Hal accepts", group="internal"),
)

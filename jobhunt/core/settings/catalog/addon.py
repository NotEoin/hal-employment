"""Settings group "Hal add-on": the owner's own Hal tools.

Developer only. The page also hides the group unless the Hal tools' config (~/.config/hal/config.toml)
exists on this computer; that check belongs to the add-on (`jobhunt/addons/hal/`), not to the catalogue.
"""

from __future__ import annotations

from jobhunt.core.settings.registry import Setting, register


def addon(key, kind, default, label, **kw) -> Setting:
    """An add-on setting: developer only and about this computer."""
    return Setting(key, kind, default, label, dev_only=True, machine=True, **kw)


register(
    addon("addon.enabled", "bool", False, "Use my Hal setup", order=10),
    addon("addon.hal_root", "path", "", "Hal folder", help="Hal's model names are read from hal.conf here.",
          order=20),
    addon("addon.vault", "path", "", "Hal vault", help="The Pipeline note is written here.", order=30),
    addon("addon.pipeline_note", "bool", True, "Write the Pipeline note after each email check", order=40),
    addon("addon.applications_mirror", "path", "", "Copy each application note into this folder", order=50),
    addon("addon.speak_command", "path", "", "Speak urgent alerts with this command", order=60),
    # Off by default: the old timers searched and read email even while Hal was switched off.
    addon("addon.respect_off", "bool", False, "Pause searches and email while Hal is switched off", order=70),
    addon("addon.jobs_command", "bool", False, "Install the /jobs command for Claude Code", order=80),
)

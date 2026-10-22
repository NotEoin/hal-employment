"""The owner's Hal add-on: what Hal Employment does only for someone running the Hal tools.

Everything here runs only when `addon.enabled` is on: the Pipeline note in Hal's vault after each email
check (`pipeline.py`), the copy of each application note (`mirror.py`), urgent alerts spoken through
`addon.speak_command`, hal.conf's role models offered as AI model choices (`models.py`), the `hal off`
pause (`addon.respect_off`, off by default, in `core/scheduler.py`) and the `/jobs` command for Claude Code
rendered from `jobs.md.tmpl` (`install_jobs_command`). Nothing in the Hal tools is edited from here.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

HAL_CONFIG = Path("~/.config/hal/config.toml")     # the Settings group shows only when this exists
JOBS_COMMAND = Path("~/.claude/commands/jobs.md")
TEMPLATE = Path(__file__).with_name("jobs.md.tmpl")
PIPELINE_NOTE = Path("Areas") / "Job Search" / "Pipeline.md"


def _get(key: str):
    from jobhunt.core import settings

    return settings.get(key)


def enabled() -> bool:
    """True when the person switched on "Use my Hal setup"."""
    return bool(_get("addon.enabled"))


def available() -> bool:
    """True when the Hal tools are set up on this computer (their config file exists)."""
    return HAL_CONFIG.expanduser().exists()


def _path_setting(key: str) -> Path | None:
    value = str(_get(key) or "").strip()
    return Path(value).expanduser() if value else None


def vault() -> Path | None:
    """Hal's vault folder, when the add-on is on and the folder is set."""
    return _path_setting("addon.vault") if enabled() else None


def speak(text: str) -> bool:
    """Says `text` through `addon.speak_command`; True when it was started."""
    command = _path_setting("addon.speak_command") if enabled() else None
    if command is None:
        return False
    try:
        subprocess.Popen([str(command), text], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log.warning("could not speak the alert: %s", e)
        return False
    return True


def render_jobs_command() -> str:
    """The `/jobs` command for Claude Code, filled from settings; it names no one by itself."""
    from jobhunt.core import paths

    first = str(_get("you.preferred_name") or "").strip() or "the person"
    folder = vault()
    note = str(folder / PIPELINE_NOTE) if folder else "(no Pipeline note: set Hal vault in Settings)"
    return TEMPLATE.read_text(encoding="utf-8").format(first_name=first, vault_note=note,
                                                       code_dir=paths.code_dir())


def install_jobs_command(target: Path | None = None) -> Path | None:
    """Writes `~/.claude/commands/jobs.md` from the template when `addon.jobs_command` is on.

    An old symlink into the repo is replaced by a plain file.
    """
    if not (enabled() and _get("addon.jobs_command")):
        return None
    target = (target or JOBS_COMMAND).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    target.write_text(render_jobs_command(), encoding="utf-8")
    return target

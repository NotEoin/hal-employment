"""hal.conf's role models, offered as choices for the AI model settings.

hal.conf is a shell file of `HAL_*=value` lines that Hal's model setup writes (L llm.py:29-38). Only the
Hal add-on reads it; the AI layer itself never does.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import _path_setting, enabled

LINE = re.compile(r"^\s*(HAL_\w+)=(.*)$")
# hal.conf key for each AI role; old roles main, writer, fast map to checker, writer, quick.
ROLE_KEYS = {"writer": "HAL_WRITER_MODEL", "checker": "HAL_MODEL", "quick": "HAL_FAST_MODEL"}


def read_conf(path: Path) -> dict[str, str]:
    """The `HAL_*` values of a hal.conf file; empty when it is missing."""
    conf: dict[str, str] = {}
    if not path.is_file():
        return conf
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(line)
        if m:
            conf[m.group(1)] = (shlex.split(m.group(2), comments=True) or [""])[0]
    return conf


def role_models() -> dict[str, str]:
    """{role: model} from hal.conf, when the add-on is on and `addon.hal_root` holds one."""
    root = _path_setting("addon.hal_root") if enabled() else None
    if root is None:
        return {}
    conf = read_conf(root / "hal.conf")
    return {role: conf[key] for role, key in ROLE_KEYS.items() if conf.get(key)}


def choices() -> list[tuple[str, str]]:
    """(model, label) pairs for the model settings, one per distinct model hal.conf names."""
    seen: dict[str, list[str]] = {}
    for role, model in role_models().items():
        seen.setdefault(model, []).append(role)
    return [(model, f"{model} (Hal's {', '.join(roles)} model)") for model, roles in seen.items()]

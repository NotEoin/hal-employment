"""Retired: course evidence now comes from your record (record.py), which replaced the course vault.

Kept only because `pack.py` and the test world still import it; once they stop, this file is deleted.
Nothing here reads a vault any more: `evidence` returns nothing
unless an old config.toml still names a `course_vault`, and even then it only says so in the log.
"""
import logging

from .config import CONFIG

log = logging.getLogger(__name__)


def evidence(requirements: list[str], per_requirement: int = 2) -> dict[str, list[dict]]:
    """Course evidence per requirement: always empty; your record's passages take its place."""
    if requirements and CONFIG.get("course_vault"):
        log.info("course_vault is set but no longer read; add course notes to your record instead")
    return {}


def as_text(found: dict[str, list[dict]]) -> str:
    """Formats evidence as plain text for a prompt."""
    lines = []
    for req, hits in found.items():
        for h in hits:
            lines.append(f"- {req} <- {h['module']} ({h['code']}), \"{h['heading']}\": {h['excerpt'][:200]}")
    return "\n".join(lines)

"""The sorter's rules for folders that are never a project, whatever README, manifest or code they hold: a
course's materials folder and a notes vault. Their files go by their own signals instead.

`sections.sorter` proposes a project for each code project and each folder with a README. On a real
10,000-file dump (2026-11-09) a learning platform's "<Module> - <CODE>/Linked materials" folder was proposed
as a code project for its README and code, and a whole Obsidian vault, "obsidian/Master", as one project for
the README at its root.

Design decisions, each with its reason:

- A course's materials folder (`MATERIALS_NAME`: "Linked materials", "Course materials", "Resources",
  "Readings", "Handouts"), at any depth below the folder sorted, is what a course gave out. Only a whole
  folder name counts, so the person's own "learning-resources" project stays a project. Its files are
  lecture material (`sort_rules.course_material`), its code too.
- A notes vault (`vaults`) is a folder holding a notes app's settings (`VAULT_DIRS`, `VAULT_FILES`,
  `LOGSEQ_DIR`: Obsidian, Logseq, Foam, Zettlr) or a Notion export (`NOTION_NAME`: most of its notes named
  "<title> <32 hex>"). The settings live in hidden folders the walk never lists, so the folder is looked at
  on disk. A README folder inside a vault is notes too; a code project inside one is still a project.
"""
from __future__ import annotations

import re
from pathlib import Path

from jobhunt.sections import sort_units

MATERIALS_NAME = re.compile(r"^(?:(?:linked|course|module|class|lecture|teaching|learning|shared)[ _-]?)?"
                            r"(?:materials?|resources?|readings?|handouts?|reading[ _-]?lists?)$", re.I)
VAULT_DIRS = {".obsidian", ".logseq", ".foam", ".zettlr"}
VAULT_FILES = {".ztr-directory"}       # Zettlr's settings for a folder of notes
LOGSEQ_DIR, LOGSEQ_CONFIG = "logseq", "config.edn"
NOTION_NAME = re.compile(r"\s[0-9a-f]{32}\.md$", re.I)     # a Notion export names each page "<title> <id>.md"
NOTION_MIN = 2                 # one such name could be chance
NOTION_SHARE = 0.5             # "most of its notes"


def _below(path: Path, folder: Path) -> bool:
    return folder in path.parents


def materials_folder(folder: Path, base: Path) -> bool:
    """True when `folder`, or a folder between it and `base`, is a course's materials folder."""
    try:
        parts = folder.relative_to(base).parts
    except ValueError:
        return False
    return any(MATERIALS_NAME.match(part.strip()) for part in parts)


def _vault_root(folder: Path) -> bool:
    try:
        names = {p.name for p in folder.iterdir()}
    except OSError:
        return False
    if names & VAULT_FILES or any((folder / d).is_dir() for d in names & VAULT_DIRS):
        return True
    if (folder / LOGSEQ_DIR / LOGSEQ_CONFIG).is_file():
        return True
    notes = [n for n in names if n.lower().endswith(".md")]
    exported = [n for n in notes if NOTION_NAME.search(n)]
    return len(exported) >= NOTION_MIN and len(exported) >= NOTION_SHARE * len(notes)


def vaults(base: Path, files: list[Path]) -> set[Path]:
    """The notes vaults at or below `base` that hold any of `files`, outermost only (see the module notes)."""
    folders = {p for f in files for p in f.parents if p == base or _below(p, base)}
    found: set[Path] = set()
    for folder in sorted(folders, key=lambda p: len(p.parts)):
        if not any(folder == v or _below(folder, v) for v in found) and _vault_root(folder):
            found.add(folder)
    return found


def in_vault(path: Path, found: set[Path]) -> bool:
    """True when `path` is, or is inside, one of the vaults `found`."""
    return any(path == v or _below(path, v) for v in found)


def not_a_unit(folder: Path, base: Path, found_vaults: set[Path], *, repo: bool) -> bool:
    """True for a folder that is never a project unit: a notes app's own folder (`sort_units.app_folder`), a
    materials folder, a notes vault, or a README folder inside a vault (see the module notes)."""
    return sort_units.app_folder(folder) or materials_folder(folder, base) or folder in found_vaults or (
        not repo and in_vault(folder, found_vaults))

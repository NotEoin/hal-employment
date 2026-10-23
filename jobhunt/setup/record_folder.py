"""The setup guide's Your record step: make the record folder, or find the one already there.

The folder is wherever `record.folder` points, else Documents, Employment Record. A folder that already
exists is found and kept as it is: only the starter folders it lacks and "Start Here.md" are added, so an
older Hal's record (or one the person made) is never emptied or rearranged. Making the folders is
`evidence.routes.make_default`, the same function the Your record page uses.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.evidence import routes as evidence

START_HERE = "Start Here.md"   # written by Hal, so it does not count as the person's own file


def _count_files(folder: Path) -> int:
    return sum(1 for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".") and p.name != START_HERE)


def ensure() -> dict:
    """Makes or finds the record folder; says which, where, and how many files it already holds."""
    folder = Path(record._live("VAULT"))
    existed = folder.is_dir()
    files_before = _count_files(folder) if existed else 0
    evidence.make_default()
    return {"folder_words": evidence._folder_words(folder), "found": existed and files_before > 0,
            "files": files_before, "subfolders": list(evidence.STARTER_FOLDERS)}

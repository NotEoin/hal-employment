"""A folder of projects on this computer (`local_folder`): each project inside it becomes its own source.

The reader lists the folder's projects and gives each as a child source (`readers.Child`) of kind `repo`, held
as a folder, which the code project reader reads (`sections.repo`): a code project with its history, or a
folder of documents, drawings or notes as a project named after the folder. A folder that is itself a code
project is one project, read as such. The refresh (`sections.refresh`) reads the folder again when a project
appears in it, so the folder is watched.

Design decisions, each with its reason:

- A project is a folder inside that is a code project (`ingest.discover.is_repo`), or one with files of its
  own at its top: a folder holding only more folders is a shelf, not a project, and its contents are left
  for the person to add on their own. Nothing deeper is searched, so a folder of thousands of files costs
  one listing.
- Hidden folders and `ingest.discover`'s junk folders (`node_modules`, `.venv`) are never projects.
- At most `PROJECTS_MAX` projects are taken from one folder: more is a disk, not a person's projects.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path

from jobhunt.ingest import discover
from jobhunt.sections import readers, repo

log = logging.getLogger(__name__)

PROJECTS_MAX = 200          # a folder with more projects than this is a disk, not one person's projects
FOLDER = "folder"


def _entries(folder: Path) -> list[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError as e:     # a folder Hal may not open has no projects to give
        log.info("could not list %s: %s", folder, e)
        return []


def is_project(folder: Path) -> bool:
    """True when a folder inside a folder of projects is a project: code, or files of its own at its top."""
    if folder.is_symlink() or not folder.is_dir() or discover.is_junk_dir(folder.name):
        return False
    entries = _entries(folder)
    if discover.is_repo([p.name for p in entries]):
        return True
    return any(p.is_file() and not p.name.startswith(".") and not discover.is_binary_name(p.name) for p in entries)


def projects(folder: Path) -> list[Path]:
    """The project folders inside `folder`, by name, at most `PROJECTS_MAX`."""
    return [p for p in _entries(folder) if is_project(p)][:PROJECTS_MAX]


class LocalFolderReader:
    """The `local_folder` reader: a folder of projects, each a child source; or one project when the folder is
    one itself."""

    name = "local_folder"

    def read(self, source: readers.Source) -> readers.Reading:
        """The folder's projects as children, listed in the note; ValueError when it is not a folder."""
        folder = Path(source.locator)
        if not folder.is_dir():
            raise ValueError(f"{source.locator} is not a folder Hal can open now. Check it is still there.")
        if repo.is_code(folder):
            return repo.RepoReader().read(dataclasses.replace(source, held=FOLDER))
        found = projects(folder)
        lines = [f"# {folder.name}", "", f"{len(found)} projects in {folder}.", ""]
        lines += [f"- {p.name}" for p in found]
        names = [p.name for p in found]
        return readers.Reading(title=folder.name, text="\n".join(lines) + "\n",
                               children=[readers.Child("repo", FOLDER, str(p)) for p in found],
                               details={"projects": names,
                                        "hash": hashlib.sha256("\n".join(names).encode()).hexdigest()})


readers.register(LocalFolderReader())

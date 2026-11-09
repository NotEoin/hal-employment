"""The sorter's rules for what is not the person's own work, by folder: libraries and build output.

`sections.sorter` proposes a section for each file and project folder of a dump. A sort of a real 10,000-file
graduate dump proposed 6,931 rows, many of them wrong (2026-11-09), so these rules keep out what a person
should never have to untick. When in doubt the sorter leaves a file out with a reason, or groups it, rather
than proposing it.

Design decisions, each with its reason:

- Nothing inside a library or build folder (`VENDORED`) is proposed, at any depth: a project's
  "_build/libs/<library>" folder was proposed about 20 times as the person's projects. The walk
  (`ingest.discover`) still enters "build" and the like outside a repository, as the bulk read may hold the
  person's own files there; the sorter, which only proposes, leaves them out.
- "lib" and "libs" (`PACKAGE_HOMES`) hold the person's code as often as a third-party package, so only a
  project found inside one (a folder with its own README or manifest) is taken for a package.
- A project's own folder is the top of a project: a project found inside a library or build folder is never
  one, so the project around it is proposed whole.
"""
from __future__ import annotations

from pathlib import Path

# Folders of other people's code or of generated output: dependency folders, virtual environments, build and
# compiler output, and caches (compared lower case).
VENDORED = {"_build", "build", "dist", "vendor", "vendors", "third_party", "third-party", "node_modules",
            "bower_components", "site-packages", ".venv", "venv", "target", "out", "bin", "obj", "pods",
            ".gradle", "__pycache__"}
PACKAGE_HOMES = {"lib", "libs"}
VENDORED_SKIP = "file in a library or build folder"


def _parts(path: Path, base: Path) -> tuple[str, ...]:
    try:
        return tuple(p.lower() for p in path.relative_to(base).parts)
    except ValueError:
        return ()


def in_vendored(path: Path, base: Path) -> bool:
    """True when the file at `path` sits in a library or build folder below `base`."""
    return any(p in VENDORED for p in _parts(path.parent, base))


def vendored_project(folder: Path, base: Path) -> bool:
    """True when a project found at `folder` is a library or build output: it sits in a library or build
    folder, or it is a package inside a "lib" or "libs" folder."""
    parts = _parts(folder, base)
    return any(p in VENDORED for p in parts) or any(p in PACKAGE_HOMES for p in parts[:-1])

"""Made-up code projects for tests: a folder of files and a git history by invented authors.

`make(folder, files, commits)` writes `files` and then makes each commit in `commits` with its own author
and date, so a test can say who made a project and when without any real repository. Git runs with a clean
configuration (no user config, no hooks, no signing) and a timeout.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_TIMEOUT_S = 30
AVERY = ("Avery Quillon", "avery@example.org")
ROBIN = ("Robin Testwell", "robin@example.org")
SAM = ("Sam Lowfield", "sam.lowfield@example.org")


def git(folder: Path, *args: str, author: tuple[str, str] = AVERY, date: str = "2024-03-01T10:00:00Z") -> str:
    """Runs one git command in `folder` as `author` at `date`; returns its output."""
    name, email = author
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date}
    out = subprocess.run(["git", "-C", str(folder), "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
                          *args], capture_output=True, text=True, env=env, timeout=GIT_TIMEOUT_S, check=True)
    return out.stdout


def write(folder: Path, files: dict[str, str | bytes]) -> None:
    """Writes made-up files under `folder`."""
    for rel, data in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8", newline="\n")   # as git checks out, not CR LF on Windows


def make(folder: Path, files: dict[str, str | bytes] | None = None,
         commits: list[tuple[tuple[str, str], str, dict[str, str]]] | None = None) -> Path:
    """A project at `folder` with `files`, then each commit (author, date, more files) in order; with no
    `commits`, one commit by Avery Quillon of the files."""
    folder.mkdir(parents=True, exist_ok=True)
    write(folder, files or {})
    git(folder, "init", "-q")
    for author, date, more in commits or [(AVERY, "2024-03-01T10:00:00Z", {})]:
        write(folder, more)
        git(folder, "add", "-A", author=author, date=date)
        git(folder, "commit", "-q", "--allow-empty", "-m", "work", author=author, date=date)
    return folder

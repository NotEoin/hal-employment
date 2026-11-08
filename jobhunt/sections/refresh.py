"""The projects refresh: a cheap check of each project source, and a new read of only the ones that changed.

`changed(con)` asks each project source, in the cheapest way its kind allows, whether it changed since its
last read (`CHECKS`): a GitHub repository's `pushed_at`, an account's list of projects, a folder's HEAD or its
newest file (`repo.fingerprint`), a folder of projects' list of folders, a page's ETag or a hash of its words.
The `record.projects_refresh` action (`sections.actions`) then reads those again through the pipeline, so
each read replaces the source's facts (`profile.store.swap_source`), keeps a revision and says what changed
(`sections.revisions`). The scheduled job (`sections.schedule`) starts it every `schedule.projects_every`
hours.

Design decisions, each with its reason:

- A check never reads a source and never fails the refresh: a source that cannot be checked (offline,
  a folder that is gone, GitHub's limit used up) counts as unchanged and is checked again next time, so a
  refresh with no network never drops a project.
- Fields the person edited are never overwritten: a read only replaces Hal's facts, and an edited card keeps
  the person's values (`profile.store`).
- Uploads and projects added by hand never change on their own, so they are never checked. A source still
  waiting for its first read, or one whose last read failed, is read again.
"""
from __future__ import annotations

import logging
import sqlite3
import urllib.error
from collections.abc import Callable
from pathlib import Path

from jobhunt.ingest import web
from jobhunt.sections import folders, github, pages, readers, registry, repo, store

log = logging.getLogger(__name__)

SECTION = "project"
RETRY_STATES = ("new", "failed")


def _repo_url(locator: str, details: dict) -> bool:
    owner, name = github.parse_url(locator)
    before = github.meta_of(locator).get("pushed_at", "")
    return github.about(owner, name).get("pushed_at", "") != before


def _account(locator: str, details: dict) -> bool:
    names = [r["name"] for r in github.account_repos(locator.strip().lstrip("@"))]
    return sorted(names) != sorted(details.get("repos") or [])


def _folder(locator: str, details: dict) -> bool:
    folder = Path(locator)
    if not folder.is_dir():
        return False
    return repo.fingerprint(folder) != details.get("fingerprint")


def _projects(locator: str, details: dict) -> bool:
    folder = Path(locator)
    if not folder.is_dir():
        return False
    if repo.is_code(folder):
        return repo.fingerprint(folder) != details.get("fingerprint")
    return sorted(p.name for p in folders.projects(folder)) != sorted(details.get("projects") or [])


def _page(locator: str, details: dict) -> bool:
    return pages.check(locator, details.get("etag", ""), details.get("hash", ""))[0]


# Each kind of project source and its cheap check; kinds not here never change on their own.
CHECKS: dict[str, Callable[[str, dict], bool]] = {"repo_url": _repo_url, "github_account": _account,
                                                   "repo": _folder, "local_folder": _projects,
                                                   "web_page": _page, "portfolio_site": _page}


def is_changed(r: sqlite3.Row, details: dict) -> bool:
    """True when one project source changed since its last read, or needs its first; False when it cannot be
    told (the check failed), so it is checked again next time."""
    section = registry.get(r["section"])
    if not readers.ready(section.reader_of(r["doc_kind"])):
        return False
    if r["state"] in RETRY_STATES:
        return True
    check = CHECKS.get(r["doc_kind"])
    if check is None or (r["doc_kind"] == "repo" and r["kind"] != registry.FOLDER):
        return False
    try:
        return check(r["locator"], details)
    except (web.LinkError, urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        log.info("could not check source %s: %s", r["id"], e)
        return False


def changed(con: sqlite3.Connection) -> list[int]:
    """The ids of the project sources that changed since their last read, parents before their children."""
    rows = con.execute("SELECT * FROM record_sources WHERE section = ? AND state != 'reading' "
                       "ORDER BY parent_id IS NOT NULL, id", (SECTION,)).fetchall()
    return [r["id"] for r in rows if is_changed(r, store.details(r))]

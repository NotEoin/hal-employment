"""GitHub sources of the Projects section: an account (`github_account`) and one repository link (`repo_url`).

An account's reader lists the person's own repositories through GitHub's API and gives each as a child
source (`readers.Child`), so each is its own project, starred and removed on its own. A repository link's
reader makes a partial clone into Hal's data folder (`data/projects/<owner>/<repo>`) and reads it with the
code project reader (`sections.repo`), with what GitHub says of it (description, topics, licence, link).

Design decisions, each with its reason:

- Which repositories count (sections plan, 2026-11-09): the account's own ones; a fork only when the person
  has commits in it, as a fork is mostly someone else's code; and, with a token, an organisation's
  repository only where the person has commits. Private ones are listed and cloned only with the person's
  token (`record.github_token`), which reaches git through `GIT_CONFIG_*` variables, so it is never written
  into the clone's `.git/config` or shown in the list of running programs.
- A clone is partial (`--filter=blob:limit=1m`): every commit for the dates and authors, but no file over a
  megabyte, which is a dataset or a build, not the person's writing. `CLONE_DEPTH` keeps a huge history from
  filling the disk.
- Every network call has a timeout (`web.TIMEOUT_S` for the API, `CLONE_TIMEOUT_S` for git). A used-up API
  limit that resets within `BACKOFF_MAX_S` is waited out once; a longer one stops with its reset time.
- Offline, or when GitHub fails, a repository already cloned is read from its last clone, so a refresh with no
  network never loses a project.
- The API's address and the address clones come from are module constants (`web.API_BASE`, `CLONE_BASE`), so
  tests serve both from a temporary folder and never reach GitHub.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
from pathlib import Path

from jobhunt.core import paths
from jobhunt.ingest import web
from jobhunt.sections import readers, repo

log = logging.getLogger(__name__)

CLONE_BASE = web.GITHUB_WEB         # tests point this at a folder of made-up repositories
CLONE_TIMEOUT_S = 300               # a partial clone of a large student project takes well under a minute
CLONE_DEPTH = 5000                  # commits kept; a person's project rarely has more, and dates need them all
BLOB_LIMIT = "1m"                   # files over this are data or builds, not the person's writing
BACKOFF_MAX_S = 60                  # a limit that resets sooner than this is waited for, once
REPOS_MAX = 200                     # repositories listed from one account; more is an organisation's, not one person's
PROJECTS_FOLDER = "projects"
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GITHUB_HOSTS = ("github.com", "www.github.com")
_sleep = time.sleep                 # tests replace it to check the back-off without waiting


def parse_url(url: str) -> tuple[str, str]:
    """The (owner, repository) a GitHub link names; ValueError for any other link."""
    p = urllib.parse.urlsplit(url.strip())
    parts = [urllib.parse.unquote(x) for x in p.path.split("/") if x]
    if p.netloc.lower() not in GITHUB_HOSTS or len(parts) < 2:
        raise ValueError("Paste the link to one project on GitHub, like https://github.com/you/project.")
    owner, name = parts[0], parts[1].removesuffix(".git")
    if not NAME.match(owner) or not NAME.match(name) or name in (".", ".."):
        raise ValueError("That GitHub link does not name a project.")
    return owner, name


def repo_url(owner: str, name: str) -> str:
    """The link to a repository on GitHub."""
    return f"{web.GITHUB_WEB}/{owner}/{name}"


def clone_dir(owner: str, name: str) -> Path:
    """Where Hal keeps its clone of a repository."""
    return paths.data_dir() / PROJECTS_FOLDER / owner / name


def _meta_path(owner: str, name: str) -> Path:
    return clone_dir(owner, name).parent / f"{name}.json"


def meta_of(url: str) -> dict:
    """What GitHub said of a repository at its last read: name, description, topics, licence, link, pushed_at;
    {} when Hal has not read it."""
    try:
        return json.loads(_meta_path(*parse_url(url)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _token() -> str:
    return web._github_token() or ""


def _api(path: str, run: web._Run) -> object:
    """GitHub's answer to GET `path`, waiting once for a limit that resets within `BACKOFF_MAX_S`."""
    try:
        return web._api(path, run)
    except web._RateLimited as e:
        wait = _seconds_until(e.reset_at)
        if wait > BACKOFF_MAX_S:
            raise
        log.info("GitHub's limit resets in %ss; waiting once", wait)
        _sleep(max(wait, 1))
        return web._api(path, run)


def _seconds_until(iso: str) -> int:
    try:
        at = time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, OverflowError):
        return BACKOFF_MAX_S + 1
    return int(at - time.time())


def _run() -> web._Run:
    return web._Run(cap=REPOS_MAX, progress=None, links_left=0, token=_token())


def _meta(data: dict) -> dict:
    lic = data.get("license") if isinstance(data.get("license"), dict) else {}
    return {"name": data.get("name") or "", "description": data.get("description") or "",
            "topics": [str(t) for t in data.get("topics") or []], "licence": (lic or {}).get("spdx_id") or "",
            "link": data.get("html_url") or "", "pushed_at": data.get("pushed_at") or "",
            "private": bool(data.get("private")), "fork": bool(data.get("fork"))}


def about(owner: str, name: str, run: web._Run | None = None) -> dict:
    """What GitHub says of one repository now (`meta_of`'s shape); LinkError when it cannot say."""
    data = _api(f"/repos/{web._q(owner)}/{web._q(name)}", run or _run())
    if not data:
        raise web.LinkError("GitHub has no project at this link that Hal can see. Check the link, or add a GitHub "
                            "token in Settings for a private one.")
    return _meta(data)


def _git_env(token: str) -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "LC_ALL": "C"}
    if token:
        import base64

        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"})
    return env


def _git(args: list[str], token: str) -> None:
    out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", *args],
                         capture_output=True, text=True, timeout=CLONE_TIMEOUT_S, env=_git_env(token), check=False)
    if out.returncode != 0:
        raise OSError(out.stderr.strip()[-300:] or "git failed")


def _clone(owner: str, name: str, token: str) -> Path:
    """A fresh partial clone, or the existing one brought up to date; OSError when git fails."""
    dest = clone_dir(owner, name)
    source = f"{CLONE_BASE}/{owner}/{name}.git"
    if (dest / ".git").is_dir():
        _git(["-C", str(dest), "fetch", "-q", f"--filter=blob:limit={BLOB_LIMIT}", f"--depth={CLONE_DEPTH}",
              source, "HEAD"], token)
        _git(["-C", str(dest), "reset", "-q", "--hard", "FETCH_HEAD"], token)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{name}.part")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _git(["clone", "-q", f"--filter=blob:limit={BLOB_LIMIT}", f"--depth={CLONE_DEPTH}", "--no-tags", source,
              str(tmp)], token)
        tmp.rename(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def ensure_clone(url: str) -> Path:
    """The folder of Hal's clone of a repository, made or brought up to date now when GitHub answers, else the
    last clone; ValueError with plain words when there is neither."""
    owner, name = parse_url(url)
    dest = clone_dir(owner, name)
    try:
        info = about(owner, name)
        if info.get("private") and not _token():
            raise web.LinkError("This project is private. Add a GitHub token in Settings to read it.")
        _clone(owner, name, _token())
        part = _meta_path(owner, name).with_suffix(".part")
        part.write_text(json.dumps(info), encoding="utf-8")
        part.replace(_meta_path(owner, name))       # a read never sees half a file
    except (web.LinkError, urllib.error.URLError, TimeoutError, OSError, subprocess.TimeoutExpired) as e:
        if (dest / ".git").is_dir():
            log.info("reading the last clone of %s/%s: %s", owner, name, e)
            return dest
        raise ValueError(str(e) if isinstance(e, web.LinkError) else
                         "Hal could not reach GitHub to copy this project. Try again later.") from e
    return dest


def forget(url: str) -> None:
    """Removes Hal's clone of a repository and what GitHub said of it."""
    try:
        owner, name = parse_url(url)
    except ValueError:
        return
    shutil.rmtree(clone_dir(owner, name), ignore_errors=True)
    _meta_path(owner, name).unlink(missing_ok=True)


def _has_commits(owner: str, name: str, login: str, run: web._Run) -> bool:
    try:
        found = _api(f"/repos/{web._q(owner)}/{web._q(name)}/commits?author={web._q(login)}&per_page=1", run)
    except web.LinkError:       # an empty repository answers 409
        return False
    return bool(found)


def account_repos(login: str) -> list[dict]:
    """The repositories of an account that are the person's: their own, and forks and organisations' ones only
    where they have commits; each as `meta_of`'s shape. LinkError when GitHub cannot say."""
    run = _run()
    me = _api("/user", run) if run.token else None
    mine = isinstance(me, dict) and str(me.get("login") or "").lower() == login.lower()
    if not mine and not _api(f"/users/{web._q(login)}", run):
        raise web.LinkError(f"GitHub has no user called {login}. Check the name.")
    listing = "/user/repos?affiliation=owner,organization_member&sort=pushed" if mine else \
        f"/users/{web._q(login)}/repos?type=owner&sort=pushed"
    found: list[dict] = []
    page = 1
    while len(found) < REPOS_MAX:
        batch = _api(f"{listing}&per_page={web.GITHUB_PER_PAGE}&page={page}", run) or []
        found += [r for r in batch if isinstance(r, dict)]
        if len(batch) < web.GITHUB_PER_PAGE:
            break
        page += 1
    out = []
    for r in found[:REPOS_MAX]:
        owner = str((r.get("owner") or {}).get("login") or login)
        if (r.get("fork") or owner.lower() != login.lower()) and not _has_commits(owner, r["name"], login, run):
            continue
        out.append({**_meta(r), "owner": owner})
    return out


class RepoLinkReader(repo.RepoReader):
    """The `repo_url` reader: one GitHub repository, cloned and read as a code project."""

    name = "repo_url"

    def forget(self, source: readers.Source) -> None:
        """Removes the clone when the source is removed."""
        forget(source.locator)


class AccountReader:
    """The `github_account` reader: the person's repositories, each a child source of its own."""

    name = "github_account"

    def read(self, source: readers.Source) -> readers.Reading:
        """A list of the account's repositories as the note, and each as a child; ValueError when GitHub cannot
        list them."""
        login = source.locator.strip().lstrip("@")
        if not NAME.match(login):
            raise ValueError("Type a GitHub username, like averyq.")
        try:
            listed = account_repos(login)
        except (web.LinkError, urllib.error.URLError, TimeoutError) as e:
            raise ValueError(str(e) if isinstance(e, web.LinkError) else
                             "Hal could not reach GitHub. Try again later.") from e
        lines = [f"# GitHub: {login}", "", f"{len(listed)} code projects.", ""]
        lines += [f"- {r['name']}: {r['description'] or 'no description'} ({r['link']})" for r in listed]
        children = [readers.Child("repo_url", "link", repo_url(r["owner"], r["name"])) for r in listed]
        pushed = max((r["pushed_at"] for r in listed if r["pushed_at"]), default="")
        return readers.Reading(title=f"GitHub: {login}", date=pushed[:7], text="\n".join(lines) + "\n",
                               children=children, details={"repos": [r["name"] for r in listed], "pushed_at": pushed})


readers.register(RepoLinkReader())
readers.register(AccountReader())

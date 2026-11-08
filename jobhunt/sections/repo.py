"""The code project reader (`repo`): what a project folder says about itself, mostly with no AI.

`read_folder(folder)` gives a `readers.Reading` for one code project: a folder on this computer, a folder or
zip the person uploaded, or a clone of a GitHub repository (`sections.github`). `RepoReader` is the reader the
Projects section names for those, registered with `readers.register` when this module loads.

What it finds with no AI: the name, description, topics and licence; the languages by size; the stack from
the manifests (`sections.stack`); how many test files there are and which CI runs them; and from `git log`,
the first and last commit dates and who made the commits. The person's share of the commits, matched to the
names, emails and handles they gave Hal (`person`), gives their role: "sole author", or "contributor (N% of
commits, M contributors)". With no git history the dates come from the files and there is no role.

What the AI writes, from the README and docs only (`ai_reading`): a two or three sentence summary and
"what it does", "how" and "result" facts, each quoting a file and line. The AI never names a tool: tools
come only from the manifests and languages.

Design decisions, each with its reason:

- `git log` runs as a subprocess with a timeout (`GIT_TIMEOUT_S`) on HEAD, never through the reflog: the
  reflog of a copied folder names whoever cloned it, which made a classmate's project look like the
  person's (2026-11-05). Repository settings that run programs (`core.fsmonitor`, hooks) are switched off
  for the call, because a cloned or copied project's own `.git/config` is not trusted.
- A project whose commits are all by others is "not yours": its note is written but it gives no project
  card, unless the person said it is theirs (`theirs` in the source's details). A project with no git
  history, or read while the person has not told Hal their name or email, is not judged at all.
- Private files are never read: `.env` files, keys and certificates by name (`SECRET_NAMES`,
  `SECRET_SUFFIXES`), and any README or doc the privacy check flags (`ingest.privacy.reason`).
- Size caps: a text file is read only up to `TEXT_MAX`; a Git LFS pointer is skipped (its content is not in
  the folder); at most `FILES_MAX` files are counted.
- Vendored, generated and lock files do not count as the project's languages: the folders in
  `VENDORED_DIRS` (on top of `ingest.discover`'s junk folders), the names in `GENERATED`, and the tool files
  `ingest.discover.is_tool_file` knows.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from jobhunt.ingest import convert, discover, privacy, roles
from jobhunt.sections import readers, stack

log = logging.getLogger(__name__)

GIT_TIMEOUT_S = 30             # a git log of a large project takes a second or two; past this git is stuck
TEXT_MAX = 1_000_000           # a README or doc past 1 MB is generated, not written
FILES_MAX = 20_000             # a folder with more files than this is not one project
FOLDER_FILES_MAX = 50          # files read from a project folder that is not code; more is an archive, not a project
DOCS_CHARS = 12_000            # README and docs sent to the AI; its first part says what the project is
DOC_FILES = 6                  # docs files read besides the README
SUMMARY_SENTENCES = 3
AI_FACTS_MAX = 6
AI_TIMEOUT_S = 120             # a summary of 12,000 characters; the slowest local model answers in a minute
LICENCE_HEAD = 2000            # a licence names itself in its first lines
TITLE_CHARS = 80               # an unknown licence is named by its first line, cut to a title
PURPOSE = "project_read"
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
SECRET_NAMES = {".env", ".envrc", ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
                "credentials", "credentials.json", "secrets.json", "secrets.yml", "secrets.yaml",
                "service-account.json"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".asc", ".gpg"}
VENDORED_DIRS = {"vendor", "vendors", "third_party", "third-party", "thirdparty", "external", "extern", "deps",
                 "lib", "libs", "node_modules", "bower_components", "Pods", "Carthage", "generated", "gen"}
# Generated code by its name: protobuf and gRPC output, migrations made by a framework, and minified files.
GENERATED = re.compile(r"(?:_pb2(?:_grpc)?\.py|\.pb\.go|\.g\.dart|\.generated\.\w+|\.designer\.cs|\.min\.\w+)$", re.I)
NOT_LANGUAGES = {"Markdown"}   # documentation, not the project's code
TEST_NAME = re.compile(r"(?:^test_.+\.py|_test\.(?:py|go)|\.(?:test|spec)\.[jt]sx?|Tests?\.(?:java|cs|kt)"
                       r"|_spec\.rb|^test.+\.R)$", re.I)
TEST_DIRS = {"test", "tests", "spec", "__tests__", "testthat"}
CI = ((".github/workflows", "GitHub Actions"), (".gitlab-ci.yml", "GitLab CI"), (".circleci", "CircleCI"),
      (".travis.yml", "Travis CI"), ("azure-pipelines.yml", "Azure Pipelines"), ("Jenkinsfile", "Jenkins"),
      ("bitbucket-pipelines.yml", "Bitbucket Pipelines"))
LICENCES = (("MIT License", "MIT"), ("Apache License", "Apache 2.0"), ("GNU AFFERO GENERAL PUBLIC", "AGPL"),
            ("GNU LESSER GENERAL PUBLIC", "LGPL"), ("GNU GENERAL PUBLIC LICENSE", "GPL"),
            ("Mozilla Public License", "MPL 2.0"), ("BSD 3-Clause", "BSD 3-Clause"), ("BSD 2-Clause", "BSD 2-Clause"),
            ("The Unlicense", "Unlicense"), ("This is free and unencumbered", "Unlicense"),
            ("Creative Commons", "Creative Commons"))
LICENCE_FILE = re.compile(r"^(?:licen[cs]e|copying)(?:\.(?:md|txt))?$", re.I)
NOREPLY_DOMAIN = "users.noreply.github.com"     # GitHub's address for a person who keeps their email private
NOREPLY = re.compile(rf"^(?:\d+\+)?([^@]+)@{re.escape(NOREPLY_DOMAIN)}$", re.I)
GIT_FORMAT = "%H%x09%aI%x09%an%x09%ae"
SOLE = "sole author"


@dataclass(frozen=True)
class Person:
    """How the person shows up in commits: their names, email addresses and usernames, all lower case."""

    names: frozenset[str] = frozenset()
    emails: frozenset[str] = frozenset()
    handles: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.names or self.emails or self.handles)

    def made(self, name: str, email: str) -> bool:
        """True when a commit by `name` <`email`> is the person's."""
        name, email = " ".join(name.lower().split()), email.lower().strip()
        handle = (m.group(1) if (m := NOREPLY.match(email)) else "").lower()
        return (email in self.emails or name in self.names or name in self.handles
                or (handle and handle in self.handles))


def person() -> Person:
    """The person as the settings and their CVs' links name them (`ingest.roles.identity`)."""
    from jobhunt import db
    from jobhunt.core import settings

    full = " ".join(str(settings.get("you.name") or "").lower().split())
    others = [n.strip().lower() for n in re.split(r"[,;\n]", str(settings.get("you.other_names") or "")) if n.strip()]
    with closing(db.connect()) as con:
        words = roles.identity(full, ", ".join(others), con, roles.own_links())
    parts = set(full.split())
    emails = {w.lower() for w in words if "@" in w}
    handles = {w.lower() for w in words if "@" not in w and w.lower() not in parts}
    return Person(frozenset({full, *others} - {""}), frozenset(emails), frozenset(handles))


@dataclass
class History:
    """A project's commits on HEAD, oldest first, as (ISO date, author name, author email)."""

    commits: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def first(self) -> str:
        """The first commit's date, `YYYY-MM-DD`; "" with no history."""
        return self.commits[0][0][:10] if self.commits else ""

    @property
    def last(self) -> str:
        """The last commit's date, `YYYY-MM-DD`; "" with no history."""
        return self.commits[-1][0][:10] if self.commits else ""

    def authors(self) -> Counter:
        """Commits by author, one author per email (or name when a commit has no email)."""
        return Counter((email.lower() or name.lower()) for _, name, email in self.commits)

    def names(self) -> dict[str, str]:
        """The name each author key (`authors`) last committed under."""
        return {(email.lower() or name.lower()): name for _, name, email in self.commits}


def git_history(folder: Path) -> History:
    """The commits on HEAD from `git log` (never the reflog); an empty `History` when there is no git history,
    git is missing, or it does not answer within `GIT_TIMEOUT_S`."""
    if not (folder / ".git").exists():
        return History()
    cmd = ["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
           "-c", f"safe.directory={folder}", "-C", str(folder), "log", "--reverse", f"--format={GIT_FORMAT}",
           "HEAD"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("git log in %s did not answer: %s", folder, e)
        return History()
    if out.returncode != 0:
        log.info("git log in %s gave no history: %s", folder, out.stderr.strip()[:200])
        return History()
    commits = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            commits.append((parts[1], parts[2], parts[3]))
    return History(commits)


def git_head(folder: Path) -> str:
    """The sha of HEAD, or "" when there is none or git does not answer in time."""
    if not (folder / ".git").exists():
        return ""
    try:
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", f"safe.directory={folder}", "-C",
                              str(folder), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def share(history: History, who: Person) -> dict:
    """The person's part in the commits: {"role", "mine", "total", "contributors", "yours"}.

    `yours` is None when it cannot be judged (no history, or Hal does not know the person), else whether any
    commit is theirs. `role` is "" unless they made some.
    """
    total = len(history.commits)
    authors = history.authors()
    mine = sum(1 for _, name, email in history.commits if who.made(name, email)) if who else 0
    judged = bool(total and who)
    role = "" if not judged or not mine else SOLE if mine == total else \
        f"contributor ({round(100 * mine / total)}% of commits, {len(authors)} contributors)"
    return {"role": role, "mine": mine, "total": total, "contributors": len(authors),
            "yours": mine > 0 if judged else None}


def secret(name: str) -> bool:
    """True for a file never read: an `.env` file, a key or a certificate, or credentials."""
    low = name.lower()
    return low in SECRET_NAMES or low.startswith(".env") or Path(low).suffix in SECRET_SUFFIXES


def _skipped_dir(name: str) -> bool:
    return discover.is_junk_dir(name, in_repo=True) or name in VENDORED_DIRS


def files(folder: Path) -> list[Path]:
    """The project's own files: no junk, vendored or generated ones, no binaries, keys or tool files."""
    out: list[Path] = []
    for here, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not _skipped_dir(d))
        for n in sorted(names):
            if discover.is_binary_name(n) or discover.is_tool_file(n) or secret(n) or GENERATED.search(n):
                continue
            out.append(Path(here) / n)
            if len(out) >= FILES_MAX:
                return out
    return out


def _lfs(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(len(LFS_POINTER)) == LFS_POINTER
    except OSError:
        return True


def languages(paths: list[Path]) -> list[tuple[str, int]]:
    """The languages by size in bytes, most first; LFS pointers and `NOT_LANGUAGES` left out."""
    sizes: Counter = Counter()
    for p in paths:
        lang = convert.LANGUAGES.get(p.suffix.lower())
        if not lang or lang in NOT_LANGUAGES:
            continue
        try:
            size = p.stat().st_size
        except OSError as e:       # a file gone or unreadable counts for no language
            log.debug("no size for %s: %s", p, e)
            continue
        if size and not _lfs(p):
            sizes[lang] += size
    return sizes.most_common(convert.LANGUAGES_SHOWN)


def tests(folder: Path, paths: list[Path]) -> int:
    """How many test files the project has."""
    def is_test(p: Path) -> bool:
        parts = p.relative_to(folder).parts
        return bool(TEST_NAME.search(p.name)) or (
            any(d.lower() in TEST_DIRS for d in parts[:-1]) and p.suffix.lower() in convert.LANGUAGES
            and p.suffix.lower() not in (".md",))
    return sum(1 for p in paths if is_test(p))


def ci(folder: Path) -> str:
    """The CI provider the project uses, or ""."""
    return next((name for rel, name in CI if (folder / rel).exists()), "")


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > TEXT_MAX or _lfs(path) or secret(path.name):
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def licence(folder: Path, said: str = "") -> str:
    """The project's licence: `said` (its manifest's or GitHub's), else named from its licence file, or ""."""
    if said:
        return said
    for p in sorted(folder.iterdir()):
        if p.is_file() and LICENCE_FILE.match(p.name):
            head = _read(p)[:LICENCE_HEAD]
            for words, name in LICENCES:
                if words.lower() in head.lower():
                    return name
            return (head.strip().splitlines() or [""])[0][:TITLE_CHARS]
    return ""


def _about(folder: Path) -> dict:
    """Name, description, topics and licence from the project's manifests."""
    out: dict = {"name": "", "description": "", "topics": [], "licence": ""}
    for name in ("pyproject.toml", "Cargo.toml"):
        text = _read(folder / name)
        data = stack._toml(text) if text else {}
        pkg = data.get("project") or data.get("package") or ((data.get("tool") or {}).get("poetry")) or {}
        if isinstance(pkg, dict) and pkg.get("name"):
            lic = pkg.get("license")
            return {"name": str(pkg["name"]), "description": str(pkg.get("description") or ""),
                    "topics": [str(k) for k in pkg.get("keywords") or []],
                    "licence": str(lic.get("text", "") if isinstance(lic, dict) else lic or "")}
    text = _read(folder / "package.json")
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("name"):
        out = {"name": str(data["name"]), "description": str(data.get("description") or ""),
               "topics": [str(k) for k in data.get("keywords") or [] if isinstance(k, str)],
               "licence": str(data.get("license") or "") if isinstance(data.get("license"), str) else ""}
    return out


def _docs(folder: Path) -> list[tuple[str, str]]:
    """The README and up to `DOC_FILES` docs as (file, text), each left out when the privacy check flags it."""
    found = [p for p in sorted(folder.iterdir()) if p.is_file() and convert.README.match(p.name)][:1]
    docs = folder / "docs"
    if docs.is_dir():
        found += sorted(p for p in docs.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".rst", ".txt"))[
            :DOC_FILES]
    out = []
    for p in found:
        text = _read(p)
        rel = p.relative_to(folder).as_posix()
        if text.strip() and not privacy.reason(rel, text):
            out.append((rel, text))
        elif text.strip():
            log.info("left %s out of a project's reading: it looks private", rel)
    return out


def _numbered(docs: list[tuple[str, str]]) -> str:
    parts, left = [], DOCS_CHARS
    for rel, text in docs:
        lines = text.splitlines()
        body = "\n".join(f"{n}: {ln}" for n, ln in enumerate(lines, 1))[:left]
        parts.append(f"=== {rel} ===\n{body}")
        left -= len(body)
        if left <= 0:
            break
    return "\n\n".join(parts)


def _line(text: str, quote: str) -> int | None:
    want = " ".join(quote.split()).lower()
    for n, ln in enumerate(text.splitlines(), 1):
        if want and want in " ".join(ln.split()).lower():
            return n
    return None


def ai_reading(name: str, docs: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """The AI's summary and its quoted facts [{"kind", "text", "quote", "file", "line"}] from the docs only.

    A fact whose quote is not found on one line of the file it names is left out. ("", []) when there are no
    docs or the AI cannot answer now; the caller then uses the description.
    """
    if not docs:
        return "", []
    from jobhunt import ai
    from jobhunt.ai import prompts

    try:
        reply = ai.chat_json(prompts.text(PURPOSE), f"Project: {name}\n\n{_numbered(docs)}", role="checker",
                             timeout=AI_TIMEOUT_S, purpose=PURPOSE, priority=2)
    except ai.LLMUnavailable as e:
        log.info("the AI could not summarise %s: %s", name, e)
        return "", []
    summary = " ".join(str(reply.get("summary") or "").split())
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    summary = " ".join(sentences[:SUMMARY_SENTENCES])
    texts = dict(docs)
    facts = []
    for f in reply.get("facts") or []:
        if not isinstance(f, dict) or f.get("kind") not in ("what", "how", "result"):
            continue
        rel, quote = str(f.get("file") or ""), " ".join(str(f.get("quote") or "").split())
        line = _line(texts.get(rel, ""), quote) if quote else None
        if line is None:
            continue
        facts.append({"kind": f["kind"], "text": " ".join(str(f.get("text") or "").split()), "quote": quote,
                      "file": rel, "line": line})
    return summary, facts[:AI_FACTS_MAX]


def _day(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d")


def _file_dates(paths: list[Path]) -> tuple[str, str]:
    """The oldest and newest file dates, for a project with no history."""
    stamps = []
    for p in paths:
        try:
            stamps.append(p.stat().st_mtime)
        except OSError as e:       # a file gone or unreadable has no date; the others still do
            log.debug("no date for %s: %s", p, e)
    if not stamps:
        return "", ""
    return _day(min(stamps)), _day(max(stamps))


def _remote(folder: Path) -> str:
    try:
        config = (folder / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for url in convert.REMOTE_URL.findall(config):
        m = re.match(r"^(?:https://|git@)(github\.com|gitlab\.com|bitbucket\.org)[/:](.+?)(?:\.git)?/?$", url)
        if m:
            return f"https://{m.group(1)}/{m.group(2)}"
    return ""


def _period(first: str, last: str) -> str:
    a, b = first[:7], last[:7]
    return a if a == b else f"{a} to {b}" if a and b else a or b


def read_folder(folder: Path, *, name: str = "", meta: dict | None = None, theirs: bool = False) -> readers.Reading:
    """One code project's reading: its note text, summary, project and skill facts, and `details`.

    `meta` is what GitHub says of it (name, description, topics, licence, link); `theirs` is the person
    saying a project with none of their commits is theirs. ValueError when `folder` is not a folder.
    """
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder Hal can read.")
    meta = meta or {}
    paths = files(folder)
    about = _about(folder)
    title = meta.get("name") or about["name"] or name or folder.name
    description = meta.get("description") or about["description"]
    topics = list(dict.fromkeys([*(meta.get("topics") or []), *about["topics"]]))
    lic = licence(folder, meta.get("licence") or about["licence"])
    langs = languages(paths)
    deps = stack.read(folder)
    test_files, ci_name = tests(folder, paths), ci(folder)
    history = git_history(folder)
    who = person()
    part = share(history, who)
    first, last = (history.first, history.last) if history.commits else _file_dates(paths)
    link = meta.get("link") or _remote(folder)
    docs = _docs(folder)
    summary, ai_facts = ai_reading(title, docs)
    summary = summary or description
    not_yours = part["yours"] is False and not theirs
    total_bytes = sum(n for _, n in langs) or 1
    lang_line = ", ".join(f"{lang} {round(100 * n / total_bytes)}%" for lang, n in langs)
    tools = list(dict.fromkeys([d.tool for d in deps] + [lang for lang, _ in langs]))

    lines = [f"# {title}", ""]
    if summary:
        lines += [summary, ""]
    facts_lines = [("Description", description), ("Topics", ", ".join(topics)), ("Licence", lic),
                   ("Languages", lang_line), ("Test files", str(test_files) if test_files else ""),
                   ("CI", ci_name), ("Link", link)]
    lines += [f"- {k}: {v}" for k, v in facts_lines if v]
    if history.commits:
        lines.append(f"- Commits: {part['total']} from {first} to {last}, by {part['contributors']} "
                     f"{'person' if part['contributors'] == 1 else 'people'}")
        if who:
            lines.append(f"- Commits by you: {part['mine']} of {part['total']}")
        if part["role"]:
            lines.append(f"- Your part: {part['role']}")
        if not_yours:
            lines.append("- None of the commits are yours, so Hal makes no project card from it.")
        names = history.names()
        lines += ["", "## Commits by author", ""] + [f"- {names[k]}: {n}" for k, n in history.authors().most_common()]
    elif first:
        lines.append(f"- Files dated: {first} to {last}")
    if deps:
        lines += ["", "## Stack", ""] + [f"- {d.tool}: `{d.said}` in {d.file} line {d.line}" for d in deps]
    if ai_facts:
        lines += ["", "## What the README says", ""] + [f"- {f['kind'].capitalize()}: {f['text']} "
                                                        f"(\"{f['quote']}\", {f['file']} line {f['line']})"
                                                        for f in ai_facts]
    for rel, text in docs:
        lines += ["", f"## {rel}", "", text.strip()]
    text = "\n".join(lines).strip() + "\n"

    facts: list[readers.Fact] = []
    if not not_yours:
        project = {"name": title}

        def fact(fields: dict, field_name: str, quote: str, place: str, line: int | None = None) -> None:
            facts.append(readers.Fact("project", {**project, **fields}, readers.Quote(field_name, quote, place, line)))

        fact({"summary": summary} if summary else {}, "name", f"# {title}", place=folder.name)
        if description:
            fact({}, "summary", description, place="README" if not about["description"] else "manifest")
        if tools:
            fact({"tools": tools}, "tools", lang_line or deps[0].said, place=deps[0].file if deps and not lang_line
                 else "languages")
        if part["role"]:
            fact({"role": part["role"]}, "role", f"Your part: {part['role']}", place="git log")
        if first:
            fact({"period": _period(first, last)}, "period", f"{first} to {last}", place="git log" if history.commits
                 else "file dates")
        if link:
            fact({"link": link}, "link", link, place="git remote" if not meta.get("link") else "GitHub")
        for f in ai_facts:
            fact({}, f["kind"], f["quote"], place=f["file"], line=f["line"])
        for d in deps:
            facts.append(readers.Fact("skill", {"name": d.tool}, readers.Quote("name", d.said, d.file, d.line)))
        for lang, _ in langs:
            facts.append(readers.Fact("skill", {"name": lang}, readers.Quote("name", lang_line, "languages")))
    details = {"tools": tools, "commits": part["total"], "mine": part["mine"], "first": first, "last": last,
               "role": part["role"], "yours": part["yours"], "head": git_head(folder), "link": link,
               "not_yours": not_yours}
    return readers.Reading(title=title, date=(last or first)[:7], text=text, summary=summary, facts=facts,
                           details=details)


def root_of(source: readers.Source) -> Path:
    """The folder a code project source is in: a folder on this computer, its uploaded files, or its clone."""
    from jobhunt.sections import store

    if source.held == "folder":
        return Path(source.locator)
    if source.held == "link":
        from jobhunt.sections import github

        return github.ensure_clone(source.locator)
    root = store.files_dir(source.id)
    tops = {f.rel.split("/", 1)[0] for f in source.files}
    if len(tops) == 1 and all("/" in f.rel for f in source.files):
        return root / tops.pop()
    return root


class RepoReader:
    """The `repo` reader: one code project from a folder, an upload or a GitHub clone."""

    name = "repo"

    def read(self, source: readers.Source) -> readers.Reading:
        """The project's reading (`read_folder`), with GitHub's details for a clone."""
        root = root_of(source)              # a clone is made or brought up to date first, with GitHub's details
        if source.held == "folder" and root.is_dir() and not is_code(root):
            return files_reading(source.title or root.name, folder_files(root))
        meta: dict = {}
        if source.held == "link":
            from jobhunt.sections import github

            meta = {"link": source.locator, **github.meta_of(source.locator)}
        return read_folder(root, name=source.title or (source.locator if source.held == "files" else ""),
                           meta=meta, theirs=bool((source.meta or {}).get("theirs")))


def is_code(folder: Path) -> bool:
    """True when the top of `folder` looks like a code project (`ingest.discover.is_repo`)."""
    try:
        return discover.is_repo([p.name for p in folder.iterdir()])
    except OSError:
        return False


class ProjectFilesReader:
    """The `project_files` reader: an uploaded folder or zip, read as a code project when it is one, else as
    the files of a project named after its folder."""

    name = "project_files"

    def read(self, source: readers.Source) -> readers.Reading:
        """A code project's reading, or the files' text with the project's name as its one fact."""
        if not source.files:
            raise ValueError("This source has no files to read.")
        root = root_of(source)
        if is_code(root):
            return RepoReader().read(source)
        return files_reading(source.title or source.locator or root.name, list(source.files))


def files_reading(title: str, kept: list[readers.SourceFile]) -> readers.Reading:
    """A project that is not code: its files' text under its name, and the name as its one fact."""
    text = f"# {title}\n\n{readers.text_of(kept)}".strip() + "\n"
    return readers.Reading(title=title, date=readers.date_in(text), text=text, facts=[
        readers.Fact("project", {"name": title}, readers.Quote("name", f"# {title}", title))])


def folder_files(root: Path) -> list[readers.SourceFile]:
    """The files of a project folder on this computer that is not code, up to `FOLDER_FILES_MAX`, no keys."""
    return [readers.SourceFile(p.relative_to(root).as_posix(), p) for p in files(root)[:FOLDER_FILES_MAX]]


readers.register(RepoReader())
readers.register(ProjectFilesReader())

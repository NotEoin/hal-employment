"""Turning one file, or one repository, into text for its note and for the model.

Documents go through `record.text_of`, so a file reads the same here as when it is dropped into the record.
Plain text, code and saved emails are read directly. Pictures are read by OCR only when they rank as a
certificate or artwork (and `record.ocr` is on); any other picture is listed by name and date alone, since
OCR of a holiday photo gives noise. An empty result is "no text": the caller never sends it to a model.
"""
from __future__ import annotations

import email
import email.policy
import json
import os
import re
import tomllib
from collections import Counter
from pathlib import Path

from jobhunt import record
from jobhunt.cvstudio import pdfread
from jobhunt.ingest import discover, frontmatter, rank

READ_CHARS = 400_000         # a 150-page document; past this the note keeps the start and says so
LIGHT_PDF_PAGES = 20         # a light PDF's tags come from its first pages; the note links the whole file
README_CHARS = 8000          # a README's first part says what the project is
DOC_HEADINGS = 40            # headings taken from a repository's docs folder
TREE_LINES = 80              # lines of a repository's file tree
TREE_DEPTH = 2
LANGUAGES_SHOWN = 8
CONVERTED = set(record.DOCUMENTS) | {".epub", ".xls", ".doc", ".ppt", ".rtf", ".odt", ".csv", ".json", ".xml"}
PLAIN = rank.TEXT_SUFFIXES | rank.CODE_SUFFIXES | {".csv", ".tsv", ".yaml", ".yml", ".toml", ".tex", ".bib"}
LANGUAGES = {".py": "Python", ".ipynb": "Jupyter notebooks", ".js": "JavaScript", ".jsx": "JavaScript",
             ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".c": "C", ".h": "C",
             ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
             ".php": "PHP", ".swift": "Swift", ".scala": "Scala", ".r": "R", ".m": "MATLAB", ".jl": "Julia",
             ".sql": "SQL", ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".vue": "Vue",
             ".svelte": "Svelte", ".dart": "Dart", ".lua": "Lua", ".hs": "Haskell", ".ex": "Elixir",
             ".tex": "LaTeX", ".md": "Markdown"}
README = re.compile(r"^readme(\.(md|markdown|rst|txt))?$", re.I)


def _plain(path: Path) -> str:
    raw = path.read_bytes()[:READ_CHARS * 4]
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _email(path: Path) -> str:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    head = [f"{k}: {msg[k]}" for k in ("Subject", "From", "To", "Date") if msg[k]]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return "\n".join(head) + "\n\n" + text.strip()


def _notebook(path: Path) -> str:
    cells = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("cells") or []
    return "\n\n".join("".join(c.get("source") or []) for c in cells if c.get("cell_type") in ("markdown", "code"))


def text_of(path: Path, rule: rank.Rule) -> str:
    """The file's words as Markdown; "" when it has none Hal can read, or is a picture left to its name."""
    suffix = path.suffix.lower()
    if suffix in rank.IMAGE_SUFFIXES:
        if rule.doc_type not in ("certificate", "artwork") or suffix not in record.IMAGES:
            return ""
        return record.text_of(path)
    if suffix in rank.MEDIA_SUFFIXES:
        return ""
    if suffix in (".eml",):
        text = _email(path)
    elif suffix == ".ipynb":
        text = _notebook(path)
    elif suffix in PLAIN and suffix != ".html":
        text = _plain(path)
        if suffix in (".md", ".markdown"):
            # A note's own properties are the old vault's bookkeeping; copied into the new note's text they
            # read as a second, broken frontmatter that the link pass then linked into (2026-11-05).
            text = frontmatter.parse(text)[1]
    elif suffix == ".pdf" and not rule.rich:
        # A light note needs words for its tags, not a full conversion: a book-length PDF took MarkItDown a
        # minute and more, and a 37,000-file dump held hundreds of them (2026-11-05).
        text = pdfread.first_pages_text(path, LIGHT_PDF_PAGES)
    elif suffix in CONVERTED or discover.magic(path) in ("pdf", "docx", "pptx", "xlsx", "epub", "rtf", "ole"):
        text = record.text_of(path)
    elif discover.magic(path) == "text":
        text = _plain(path)
    else:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()
    # PDF text can hold lone surrogates, which no UTF-8 file can store: a poster PDF's note failed to write
    # (2026-11-05). Each becomes "?", so the rest of the note survives.
    text = text.encode("utf-8", "replace").decode("utf-8")
    return text[:READ_CHARS]


def _manifest(folder: Path) -> list[str]:
    """The project's name, description and stated dependencies from its manifest."""
    out = []
    pyproject = folder / "pyproject.toml"
    if pyproject.is_file():
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
        except (OSError, tomllib.TOMLDecodeError):
            project = {}
        out += [f"Python project: {project.get('name', '')}", str(project.get("description") or ""),
                "Depends on: " + ", ".join(str(d) for d in project.get("dependencies") or [])]
    package = folder / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            out += [f"JavaScript package: {data.get('name', '')}", str(data.get("description") or ""),
                    "Depends on: " + ", ".join(sorted(data.get("dependencies") or {}))]
    cargo = folder / "Cargo.toml"
    if cargo.is_file():
        try:
            pkg = tomllib.loads(cargo.read_text(encoding="utf-8")).get("package") or {}
        except (OSError, tomllib.TOMLDecodeError):
            pkg = {}
        out += [f"Rust crate: {pkg.get('name', '')}", str(pkg.get("description") or "")]
    return [line for line in out if line.strip() and not line.endswith(": ")]


def repo_files(folder: Path) -> list[Path]:
    """The repository's files, without its junk folders and binaries."""
    out = []
    for here, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not discover.is_junk_dir(d, in_repo=True))
        out += [Path(here) / n for n in sorted(names) if not discover.is_binary_name(n)]
    return out


def languages(files: list[Path]) -> list[tuple[str, int]]:
    """The languages of a repository by number of files, most first."""
    counts = Counter(LANGUAGES[p.suffix.lower()] for p in files if p.suffix.lower() in LANGUAGES)
    return counts.most_common(LANGUAGES_SHOWN)


def _tree(folder: Path, files: list[Path]) -> list[str]:
    shown = sorted({"/".join(p.relative_to(folder).parts[:TREE_DEPTH]) for p in files})
    return shown[:TREE_LINES] + ([f"... and {len(shown) - TREE_LINES} more"] if len(shown) > TREE_LINES else [])


def repo_text(folder: Path) -> str:
    """A repository as one text: its README, docs headings, manifest, language mix and file tree."""
    files = repo_files(folder)
    parts = [f"# {folder.name}", "Code repository."]
    parts += _manifest(folder)
    langs = languages(files)
    if langs:
        parts.append("Languages: " + ", ".join(f"{name} ({n} files)" for name, n in langs))
    readme = next((p for p in sorted(folder.iterdir()) if p.is_file() and README.match(p.name)), None)
    if readme is not None:
        parts += ["## README", _plain(readme)[:README_CHARS]]
    docs = [p for p in files if p.suffix.lower() in (".md", ".rst") and "docs" in p.relative_to(folder).parts[:1]]
    heads = [line.strip() for p in docs for line in _plain(p).splitlines() if line.startswith("#")][:DOC_HEADINGS]
    if heads:
        parts += ["## Docs", "\n".join(heads)]
    parts += ["## Files", "\n".join(_tree(folder, files))]
    return "\n\n".join(p for p in parts if p.strip())

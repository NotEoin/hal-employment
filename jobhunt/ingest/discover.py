"""Finding the files to read: a walk that leaves out junk, with each file's hash and kind by its first bytes.

- Junk folders (version control, virtual environments, dependency folders, caches) are never entered, nor
  are build folders inside a repository. Binaries that hold no words for a person (model weights, programs,
  libraries, databases, fonts) are left out by name, so a folder of a few projects does not become a hundred
  thousand items.
- Hidden folders and files (a name starting with ".") are never read: they hold an app's settings, an
  editor's state or a search index, never a document. Nor are an AI tool's instruction files, lockfiles and
  minified or bundled third-party code (`is_tool_file`): on a real dump these were filed as the person's
  notes and code (2026-11-05).
- A file whose bytes are all zero is a cloud placeholder: OneDrive and Dropbox keep the name and size of a
  file not yet downloaded and fill it with zeros. It is listed as "not downloaded", never converted, since
  its "text" would be nothing and a model given nothing invents a document.
- A folder holding a version-control folder or a project manifest at its top is a repository; the walk
  notes it, as its `.git` folder is never entered. It notes each folder with a README at its top too
  (`has_readme`), from which `group.projects` finds the person's projects that are not repositories.
- Symbolic links are not followed: a link can point anywhere on the computer.
"""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from jobhunt.ingest import rank

CHUNK = 1 << 20           # read 1 MiB at a time, so a large video never sits in memory
PEEK = 16                 # bytes enough for every signature below
MAX_CUT = 3               # a UTF-8 character is at most 4 bytes, so the peek can cut off at most 3 of them
JUNK_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".tox", ".nox", ".cache", ".gradle", ".idea", ".vscode", ".next", ".nuxt",
             "site-packages", ".ipynb_checkpoints", ".obsidian", ".trash", ".Trash", "$RECYCLE.BIN",
             "System Volume Information", ".Spotlight-V100", ".fseventsd", "bower_components", ".terraform", ".eggs"}
# Junk only inside a repository: elsewhere a folder called "build" or "target" may be the person's own.
REPO_JUNK_DIRS = {"build", "dist", "target", "coverage", "htmlcov", "env", "out", "bin", "obj"}
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
# Instructions for AI coding tools and dependency lockfiles: written for a program, not about the person.
TOOL_FILES = {"claude.md", "agents.md", "gemini.md", "copilot-instructions.md", "package-lock.json", "yarn.lock",
              "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "uv.lock", "composer.lock", "gemfile.lock"}
# A tool file by its shape: "CLAUDE-archive.md", "jquery.min.js", "app.js.map", "react-entry-be1bde4765d8e661.js"
# (a bundler names its output after a hash of at least 8 hex digits, letters and digits mixed, so a dated
# "notes-20241105.js" is not one).
TOOL_NAME = re.compile(r"^(?:claude|agents|gemini)[-_ ].*\.md$|\.min\.(?:js|css)$|\.(?:js|css)\.map$|"
                       r"[-.](?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{8,}\.(?:js|mjs|css)$", re.I)
BINARY_SUFFIXES = {
    # model weights and data dumps
    ".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".h5", ".hdf5", ".gguf", ".pkl", ".pickle", ".npy", ".npz",
    ".parquet", ".feather", ".tfrecord", ".bin", ".dat",
    # programs and libraries
    ".exe", ".dll", ".so", ".dylib", ".o", ".obj", ".a", ".lib", ".class", ".jar", ".pyc", ".pyo", ".whl",
    ".msi", ".dmg", ".iso", ".img", ".apk", ".deb", ".rpm", ".app", ".sys",
    # databases, fonts, disk images and half-written downloads
    ".db", ".sqlite", ".sqlite3", ".mdb", ".ttf", ".otf", ".woff", ".woff2", ".eot", ".vmdk", ".vdi", ".qcow2",
    ".lock", ".tmp", ".swp", ".part", ".crdownload",
}
# Files in a project folder that make it a repository, besides a version-control folder.
REPO_MARKERS = {"pyproject.toml", "setup.py", "package.json", "cargo.toml", "go.mod", "pom.xml", "build.gradle",
                "build.gradle.kts", "gemfile", "composer.json", "cmakelists.txt", "mix.exs", "pubspec.yaml",
                "project.clj", "stack.yaml", "deno.json"}
VCS_DIRS = {".git", ".hg", ".svn"}
# A copied project often lost its .git: a README beside one of these at the top still marks a repository.
PROJECT_SIGNS = {".gitignore", "makefile", "requirements.txt", "license", "license.md", "license.txt", "src",
                 "dockerfile", ".editorconfig"}
NOTES_VAULT = ".obsidian"
README_SUFFIXES = {"", *rank.TEXT_SUFFIXES}     # "README", "README.md", "readme.txt"
CODE_FILES_MIN = 2        # a README with two source files beside it is a project, not a folder of notes
SIGNATURES = ((b"%PDF", "pdf"), (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"),
              (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"\xd0\xcf\x11\xe0", "ole"), (b"{\\rtf", "rtf"),
              (b"\x7fELF", "exe"), (b"\x1f\x8b", "gzip"), (b"7z\xbc\xaf", "7z"), (b"Rar!", "rar"),
              (b"ID3", "audio"), (b"fLaC", "audio"), (b"OggS", "audio"))
OFFICE_PARTS = (("word/", "docx"), ("ppt/", "pptx"), ("xl/", "xlsx"), ("META-INF/container.xml", "epub"))


@dataclass
class Found:
    """One file found by the walk."""

    path: Path
    rel: str                       # inside the folder walked, with "/", starting with the folder's own name
    size: int
    mtime: float
    sha256: str = ""
    magic: str = ""
    state: str = "pending"         # pending | not_downloaded | skipped


@dataclass
class Walk:
    """What one walk found: the files, the repository folders, the folders with a README at their top, and how
    many things it left out and why."""

    files: list[Found] = field(default_factory=list)
    repos: set[Path] = field(default_factory=set)
    readmes: set[Path] = field(default_factory=set)
    left_out: dict[str, int] = field(default_factory=dict)   # reason -> count

    def leave_out(self, why: str) -> None:
        """Counts one thing left out for `why`."""
        self.left_out[why] = self.left_out.get(why, 0) + 1


def is_junk_dir(name: str, in_repo: bool = False) -> bool:
    """True for a folder never worth entering, such as `.git` or `node_modules`, or `build` in a repository."""
    return (name in JUNK_DIRS or name.startswith(".") or name.endswith(".egg-info")
            or (in_repo and name.lower() in REPO_JUNK_DIRS))


def is_binary_name(name: str) -> bool:
    """True for a file left out by its name: weights, programs, libraries, databases, fonts, temporary files."""
    low = name.lower()
    return low in JUNK_FILES or low.startswith("~$") or Path(low).suffix in BINARY_SUFFIXES


def is_tool_file(name: str) -> bool:
    """True for a file written for a program rather than a person: a hidden file, an AI tool's instructions,
    a lockfile, or minified or bundled code."""
    low = name.lower()
    return low.startswith(".") or low in TOOL_FILES or TOOL_NAME.search(low) is not None


def is_repo(names: list[str]) -> bool:
    """True when a folder whose entries are `names` is the top of a code repository."""
    lower = [n.lower() for n in names]
    if NOTES_VAULT in lower:
        return False        # an Obsidian vault of notes, even one kept in git, is notes to read one by one
    if any(n in VCS_DIRS for n in names) or any(n in REPO_MARKERS for n in lower):
        return True
    if not any(n.startswith("readme") for n in lower):
        return False
    code = sum(Path(n).suffix in rank.CODE_SUFFIXES - {".html", ".css"} for n in lower)
    return any(n in PROJECT_SIGNS for n in lower) or code >= CODE_FILES_MIN


def has_readme(names: list[str]) -> bool:
    """True when a folder whose entries are `names` holds a README ("README.md", "readme.txt", "README")."""
    return any(Path(n).stem.lower() == "readme" and Path(n).suffix.lower() in README_SUFFIXES for n in names)


def hash_and_zero(path: Path) -> tuple[str, bool]:
    """The file's SHA-256 and whether every byte of it is zero, in one read."""
    digest, zero, zeros = hashlib.sha256(), True, bytes(CHUNK)
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
            if zero and chunk != zeros[:len(chunk)]:
                zero = False
    return digest.hexdigest(), zero


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return "zip"
    for prefix, kind in OFFICE_PARTS:
        if any(n.startswith(prefix) for n in names):
            return kind
    return "zip"


def magic(path: Path) -> str:
    """The kind of file by its first bytes ("pdf", "docx", "png", "zip", "text" ...); "" when unknown."""
    try:
        with path.open("rb") as f:
            head = f.read(PEEK)
    except OSError:
        return ""
    for sig, kind in SIGNATURES:
        if head.startswith(sig):
            return kind
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        return "video"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _zip_kind(path)
    if not head:
        return "empty"
    if b"\x00" in head:
        return ""
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start < len(head) - MAX_CUT:   # not just a character cut short by the peek
            return ""
    return "text"


def walk(root: Path, prefix: str | None = None) -> Walk:
    """Every file under `root` worth reading, with the repositories in it; `prefix` starts each `rel`.

    Hashes are not taken here: `fill` takes them, so a resumed ingest hashes only what it has not seen.
    """
    out = Walk()
    root = Path(root)
    prefix = root.name if prefix is None else prefix
    if root.is_file():
        st = root.stat()
        out.files.append(Found(root, prefix or root.name, st.st_size, st.st_mtime))
        return out
    for folder, dirs, names in os.walk(root, followlinks=False):
        here = Path(folder)
        if is_repo(dirs + names):
            out.repos.add(here)
        if has_readme(names):
            out.readmes.add(here)
        in_repo = any(here == r or r in here.parents for r in out.repos)
        kept = []
        for d in sorted(dirs):
            if is_junk_dir(d, in_repo) or (here / d).is_symlink():
                out.leave_out("junk folder")
            else:
                kept.append(d)
        dirs[:] = kept
        for name in sorted(names):
            path = here / name
            if path.is_symlink():
                out.leave_out("link")
                continue
            if is_binary_name(name):
                out.leave_out("program or data file")
                continue
            if is_tool_file(name):
                out.leave_out("settings or tool file")
                continue
            try:
                st = path.stat()
            except OSError:
                out.leave_out("unreadable")
                continue
            rel = path.relative_to(root).as_posix()
            out.files.append(Found(path, f"{prefix}/{rel}" if prefix else rel, st.st_size, st.st_mtime))
    return out


def fill(found: Found) -> Found:
    """Takes the file's hash and kind, and marks a cloud placeholder or an empty file."""
    if found.size == 0:
        found.magic, found.state = "empty", "skipped"
        return found
    found.sha256, zero = hash_and_zero(found.path)
    if zero:
        found.state, found.magic = "not_downloaded", "zero"
        return found
    found.magic = magic(found.path)
    if found.magic == "exe":
        found.state = "skipped"
    return found

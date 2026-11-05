"""The file registry: every file the API may serve has a row in `files`, and only those are served.

A file is stored as a root name plus a path relative to that root, so moving the applications folder (the
`cvdocs.folder` setting) or the data folder never breaks a link. `path_of` refuses any row whose path would
leave its root, so a bad row can never serve a file from elsewhere on the computer.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import secrets
import sqlite3
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

from ..core import clock, paths

log = logging.getLogger(__name__)

ID_BYTES = 8                       # 16 hex characters, as the schema says (migration 0009)
HASH_CHUNK = 1024 * 1024


def _record_root() -> Path:
    from ..core import settings

    chosen = settings.get("record.folder")
    return Path(chosen).expanduser() if chosen else paths.record_default()


ROOTS: dict[str, Callable[[], Path]] = {
    "applications": paths.applications_dir,
    "legacy_packs": paths.legacy_packs_dir,
    "shots": paths.shots_dir,
    "cache": paths.cache_dir,
    "cv": paths.cv_dir,
    "exports": lambda: paths.data_dir() / "exports",
    "record": _record_root,
}


def root_dir(root: str) -> Path:
    """The folder a root name stands for, resolved now."""
    if root not in ROOTS:
        raise ValueError(f"unknown file root {root!r}; one of {', '.join(ROOTS)}")
    return ROOTS[root]()


def sha256(path: Path) -> str:
    """The file's SHA-256, read in chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def register(con: sqlite3.Connection, root: str, path: Path, kind: str, *, job_id: str | None = None,
             label: str | None = None) -> str:
    """Registers (or refreshes) one file under `root` and returns its id; the caller commits."""
    base = root_dir(root).resolve()
    rel = Path(path).resolve().relative_to(base).as_posix()
    digest, size = sha256(Path(path)), Path(path).stat().st_size
    # One statement, so two saves of the same folder at once both land on the one row (a double-clicked approval
    # failed with "UNIQUE constraint failed: files.root, files.rel", testers 2026-11-04).
    con.execute("""INSERT INTO files (id, root, rel, kind, job_id, sha256, size, label, created)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (root, rel) DO UPDATE SET kind = excluded.kind, job_id = excluded.job_id,
                       sha256 = excluded.sha256, size = excluded.size, label = excluded.label""",
                (secrets.token_hex(ID_BYTES), root, rel, kind, job_id, digest, size, label, clock.iso()))
    return con.execute("SELECT id FROM files WHERE root = ? AND rel = ?", (root, rel)).fetchone()[0]


def forget_under(con: sqlite3.Connection, root: str, folder_rel: str, *, keep: set[str] = frozenset()) -> None:
    """Drops the rows of files inside one folder of `root` that are not in `keep`; the caller commits.

    Documents that pointed at a dropped row keep their text and lose only the link to the file.
    """
    stale = [file_id for file_id in ids_under(con, root, folder_rel).values() if file_id not in keep]
    for file_id in stale:
        con.execute("UPDATE app_documents SET file_id = NULL WHERE file_id = ?", (file_id,))
        con.execute("DELETE FROM files WHERE id = ?", (file_id,))


def ids_under(con: sqlite3.Connection, root: str, folder_rel: str) -> dict[str, str]:
    """File ids by path inside one folder of `root` (a prefix compare, so no character needs escaping)."""
    prefix = folder_rel + "/"
    return {r["rel"][len(prefix):]: r["id"] for r in con.execute(
        "SELECT id, rel FROM files WHERE root = ? AND substr(rel, 1, ?) = ?", (root, len(prefix), prefix))}


def path_of(con: sqlite3.Connection, file_id: str) -> tuple[Path, sqlite3.Row]:
    """The path of a registered file and its row; LookupError when there is none or it is gone."""
    row = con.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise LookupError("That file is not one Hal keeps.")
    base = root_dir(row["root"]).resolve()
    path = (base / row["rel"]).resolve()
    if not path.is_relative_to(base):
        raise LookupError("That file is not one Hal keeps.")
    if not path.is_file():
        raise LookupError("That file is no longer in its folder.")
    return path, row


def zip_folder(folder: Path, *, leave_out: tuple[str, ...] = ()) -> bytes:
    """Every file in `folder` (not hidden ones, not `leave_out` names), as one zip held in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            rel = p.relative_to(folder)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts) and p.name not in leave_out:
                z.write(p, rel.as_posix())
    return buf.getvalue()


def open_folder(folder: Path) -> None:
    """Opens `folder` in the computer's file manager, without waiting for it; only logs in test mode."""
    if os.environ.get(paths.ENV_TEST) == "1":
        # a test run must never open a window on the desktop of the computer it runs on
        log.info("test mode: not opening %s", folder)
        return
    try:
        from ..core import platform as hal_platform

        opener = getattr(hal_platform, "open_folder", None)
    except ImportError:
        opener = None
    if opener is not None:
        opener(folder)
        return
    if sys.platform == "win32":
        os.startfile(folder)  # type: ignore[attr-defined]  # only defined on Windows
        return
    command = "open" if sys.platform == "darwin" else "xdg-open"
    # Popen returns at once; the file manager outlives this call, so there is nothing to time out.
    subprocess.Popen([command, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    log.info("opened %s with %s", folder, command)

"""Where an ingest's files come from: folders on this computer, uploads, zips and the drop folder.

Zips are unpacked under the data folder, never beside the zip, and treated as hostile:

- a member whose name is absolute or climbs out with `..` (zip slip) is refused, and every target is checked
  again after resolving, so no name can write outside the folder made for that zip;
- symbolic links stored in a zip are skipped, as a link could later point a write anywhere;
- a member is copied in chunks and counted as it goes, because a zip's stated sizes can lie (a zip bomb
  says 1 KB and unpacks to many GB): past `ZIP_FILE_MAX` for one member or `ZIP_TOTAL_MAX` for the whole zip
  the member is dropped and named in the report;
- encrypted members are named, never guessed at;
- a zip inside a zip is unpacked in place, down to `ZIP_DEPTH_MAX` levels; deeper ones are named.

The drop folder's files are a mix of the person's own work and course material unless the person said
otherwise in Settings (`record.drop_origin`).
"""
from __future__ import annotations

import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from jobhunt.core import paths

ZIP_TOTAL_MAX = 4 * 1024 ** 3      # 4 GiB unpacked per zip: a degree's worth of files with room to spare
ZIP_FILE_MAX = 1024 ** 3           # 1 GiB for any one member: past that it is a video or a disk image
ZIP_MEMBERS_MAX = 50_000           # more members than this is a zip of a whole disk, not of a person's files
ZIP_DEPTH_MAX = 3                  # a zip in a zip in a zip; deeper nesting is named, not unpacked
COPY_CHUNK = 1 << 20
DROP_DEFAULT = "+Inbox"            # the drop folder inside the record when `record.drop_folder` is empty
ENCRYPTED_FLAG = 0x1               # general purpose bit 0 of a zip member (APPNOTE 4.4.4)
UNIX_MODE_SHIFT = 16               # a member's Unix mode sits in the high half of its external attributes


@dataclass
class ZipReport:
    """What unpacking one zip did: members written, and each one refused and why."""

    written: int = 0
    refused: list[str] = field(default_factory=list)       # zip slip: absolute or ".." names
    links: list[str] = field(default_factory=list)
    encrypted: list[str] = field(default_factory=list)
    too_big: list[str] = field(default_factory=list)
    too_deep: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        """Each refused member in plain words, for the ingest's list of errors."""
        words = (("refused", "has a name that points outside the zip"), ("links", "is a link"),
                 ("encrypted", "is locked with a password"), ("too_big", "is too big to unpack"),
                 ("too_deep", "is a zip nested too deep"), ("broken", "could not be read"))
        return [f"{name} {why}" for attr, why in words for name in getattr(self, attr)]


def ingest_dir() -> Path:
    """The ingest's own folder in the data folder: uploads, unpacked zips and fetched pages."""
    return paths.data_dir() / "ingest"


def uploads_dir() -> Path:
    """Where uploaded zips wait to be read."""
    return ingest_dir() / "uploads"


def unpacked_dir(batch: str) -> Path:
    """Where the zips of one ingest are unpacked."""
    return ingest_dir() / "unpacked" / batch


def drop_folder() -> Path:
    """The drop folder: `record.drop_folder`, else `+Inbox` inside the record."""
    from jobhunt import record
    from jobhunt.core import settings

    chosen = str(settings.get("record.drop_folder") or "").strip()
    return Path(chosen).expanduser() if chosen else Path(record._live("VAULT")) / DROP_DEFAULT


def drop_origin() -> str:
    """Where the drop folder's files come from, as the person said in `record.drop_origin` (`roles.ORIGINS`)."""
    from jobhunt.core import settings
    from jobhunt.ingest import roles

    return roles.origin_of(settings.get("record.drop_origin"))


def safe_member(name: str) -> PurePosixPath | None:
    """A zip member's name as a relative path inside the zip, or None when it would leave it."""
    clean = name.replace("\\", "/")
    if clean.startswith("/") or (len(clean) > 1 and clean[1] == ":"):
        return None
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return PurePosixPath(*parts)


def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> UNIX_MODE_SHIFT)


def _copy_capped(src, dest: Path, cap: int) -> int | None:
    """Copies a member to `dest`; None (and nothing left behind) when it runs past `cap` bytes."""
    written = 0
    with dest.open("wb") as out:
        while chunk := src.read(COPY_CHUNK):
            written += len(chunk)
            if written > cap:
                break
            out.write(chunk)
    if written > cap:
        dest.unlink(missing_ok=True)
        return None
    return written


def unpack(zip_path: Path, dest: Path, *, depth: int = 1, report: ZipReport | None = None,
           label: str = "") -> ZipReport:
    """Unpacks `zip_path` into `dest` with every guard of the module header; returns what it did."""
    report = report or ZipReport()
    label = label or zip_path.name
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    total = 0
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile):
        report.broken.append(label)
        return report
    nested = []
    with zf:
        for n, info in enumerate(zf.infolist()):
            shown = f"{label}/{info.filename}"
            if n >= ZIP_MEMBERS_MAX:
                report.too_big.append(f"{label} (more than {ZIP_MEMBERS_MAX} files)")
                break
            if info.is_dir():
                continue
            rel = safe_member(info.filename)
            target = (root / rel).resolve() if rel is not None else None
            if target is None or root not in target.parents:
                report.refused.append(shown)
                continue
            if _is_link(info):
                report.links.append(shown)
                continue
            if info.flag_bits & ENCRYPTED_FLAG:
                report.encrypted.append(shown)
                continue
            room = min(ZIP_FILE_MAX, ZIP_TOTAL_MAX - total)
            if info.file_size > room:
                report.too_big.append(shown)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src:
                    size = _copy_capped(src, target, room)
            except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError):
                # RuntimeError and ValueError are what zipfile raises for an unsupported or corrupt member
                target.unlink(missing_ok=True)
                report.broken.append(shown)
                continue
            if size is None:
                report.too_big.append(shown)
                continue
            total += size
            report.written += 1
            if target.suffix.lower() == ".zip":
                nested.append((target, shown))
    for inner, shown in nested:
        if depth >= ZIP_DEPTH_MAX:
            report.too_deep.append(shown)
        else:
            unpack(inner, inner.with_suffix(""), depth=depth + 1, report=report, label=shown)
        inner.unlink(missing_ok=True)
    return report


def remove(folder: Path) -> None:
    """Deletes a folder the ingest made under its own folder; never anything outside it."""
    base = ingest_dir().resolve()
    target = Path(folder).resolve()
    if base in target.parents and target.exists():
        shutil.rmtree(target, ignore_errors=True)

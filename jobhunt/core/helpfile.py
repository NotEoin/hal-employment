"""The help file: what someone helping the person needs, with the person taken out.

It holds the versions, the "Is Hal working?" checks, recent Activity and the last failed actions, so a
helper can see what went wrong without asking. Before anything is written, every line passes `scrub()`:

- names, contact details and links from `you.*`, the names and addresses of contacts Hal found, and every
  saved form answer are replaced with a bracketed word, so the file can be sent to anyone;
- a line that still holds a private answer (`ai.guard.contains_private`) is dropped whole, because the
  private answers themselves may only be read by the form filler and the AI guard;
- e-mail addresses and phone numbers anywhere are replaced by pattern, whoever they belong to.

The person sees the text before it is saved (the page shows `preview_text`), and the file goes into
`<data>/exports/`, registered in `files` so the files route can hand it over.
"""
from __future__ import annotations

import json
import logging
import platform
import re
import secrets as pysecrets
import sqlite3
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from .. import __version__
from . import clock, paths

log = logging.getLogger(__name__)

ACTIVITY_ROWS = 60         # about a day of ordinary use; enough to see what led up to a problem
FAILED_ACTIONS = 10
MIN_ANSWER_CHARS = 4       # shorter saved answers ("Yes", "No") are everyday words, not personal details
FILE_ROOT = "exports"      # `files.root` for what Hal makes for the person to save (migration 0009)
LEFT_OUT = "(line left out: it held one of your private answers)"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<!\w)(\+?\d[\d ()-]{8,}\d)(?!\w)")
URL = re.compile(r"https?://\S+")
# Settings that name or locate the person; their values are taken out wherever they appear.
PERSONAL_KEYS = ("you.name", "you.preferred_name", "you.email", "you.phone", "you.city", "you.location_line",
                 "you.links.linkedin", "you.links.website", "you.links.github", "you.education.university",
                 "you.education.dissertation", "email.address", "notify.daily_to", "sites.contact")


def _setting_values() -> list[tuple[str, str]]:
    from . import settings

    out = []
    for key in PERSONAL_KEYS:
        try:
            value = settings.get(key)
        except KeyError:
            continue
        if isinstance(value, str) and value.strip():
            out.append((value.strip(), "[your details]"))
            if key == "you.name":
                out.extend((part, "[your name]") for part in value.split() if len(part) > 1)
    return out


def _contact_values(con: sqlite3.Connection) -> list[tuple[str, str]]:
    out = []
    try:
        rows = con.execute("SELECT contacts FROM applications WHERE contacts IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return out
    for (raw,) in rows:
        try:
            people = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for person in people if isinstance(people, list) else []:
            if not isinstance(person, dict):
                continue
            for field in ("name", "email", "url", "linkedin", "phone"):
                value = person.get(field)
                if isinstance(value, str) and value.strip():
                    out.append((value.strip(), "[a contact]"))
    return out


def _answer_values(con: sqlite3.Connection) -> list[tuple[str, str]]:
    out = []
    try:
        rows = con.execute("SELECT answer FROM answers2").fetchall()
    except sqlite3.OperationalError:
        return out   # a database from before saved answers had their own table
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            value = raw
        for one in value if isinstance(value, list) else [value]:
            if isinstance(one, str) and len(one.strip()) >= MIN_ANSWER_CHARS:
                out.append((one.strip(), "[a saved answer]"))
    return out


def scrubber(con: sqlite3.Connection) -> Callable[[str], str | None]:
    """A function that takes the person out of one line, or returns None when the line must be left out."""
    from ..ai import guard

    known = _setting_values() + _contact_values(con) + _answer_values(con)
    # Longest first, so a full name is replaced before the first name inside it.
    known.sort(key=lambda pair: len(pair[0]), reverse=True)
    patterns = [(re.compile(re.escape(value), re.IGNORECASE), word) for value, word in known]

    def scrub(line: str) -> str | None:
        if guard.contains_private(line):
            return None
        for pattern, word in patterns:
            line = pattern.sub(word, line)
        line = EMAIL.sub("[an email address]", line)
        line = URL.sub("[a link]", line)
        return PHONE.sub("[a phone number]", line)
    return scrub


def _checks() -> list[str]:
    from . import health_routes

    out = []
    for c in health_routes.run_checks():
        mark = "fine" if c["ok"] else c["level"]
        out.append(f"- {c['name']}: {mark}. {c['text']}")
    return out or ["- No checks ran."]


def _activity(con: sqlite3.Connection) -> list[str]:
    try:
        rows = con.execute("SELECT at, area, text, why FROM activity ORDER BY id DESC LIMIT ?",
                           (ACTIVITY_ROWS,)).fetchall()
    except sqlite3.OperationalError:
        return ["- Activity could not be read."]
    return [f"- {r[0]} [{r[1]}] {r[2]} (why: {r[3]})" for r in rows] or ["- Nothing yet."]


def _failures(con: sqlite3.Connection) -> list[str]:
    try:
        rows = con.execute("SELECT created, kind, state, error FROM actions WHERE state IN ('error', 'interrupted')"
                           " ORDER BY created DESC LIMIT ?", (FAILED_ACTIONS,)).fetchall()
    except sqlite3.OperationalError:
        return ["- Actions could not be read."]
    return [f"- {r[0]} {r[1]} {r[2]}: {(r[3] or '').strip()}" for r in rows] or ["- None."]


def text(con: sqlite3.Connection) -> str:
    """The help file's words, already scrubbed."""
    from .db import migrate

    try:
        version = migrate.user_version(con)
    except (sqlite3.Error, AttributeError):
        version = "unknown"
    sections: list[tuple[str, Iterable[str]]] = [
        ("Versions", [f"- Hal Employment {__version__}", f"- Records version {version}",
                      f"- Python {sys.version.split()[0]} on {platform.system()} {platform.release()}"]),
        ("Is Hal working?", _checks()),
        ("Things that did not finish", _failures(con)),
        ("Recent activity, newest first", _activity(con)),
    ]
    scrub = scrubber(con)
    lines = [f"Hal Employment help file, made {clock.iso()}",
             "Names, contact details, answers and private answers are taken out.", ""]
    for title, body in sections:
        lines.append(title)
        for line in body:
            clean = scrub(line)
            lines.append(LEFT_OUT if clean is None else clean)
        lines.append("")
    return "\n".join(lines)


def exports_dir() -> Path:
    """Where files Hal makes for the person to save are kept."""
    return paths.data_dir() / FILE_ROOT


def register_file(con: sqlite3.Connection, path: Path, kind: str, label: str) -> str:
    """Adds a file under `exports_dir()` to the `files` table and returns its id."""
    import hashlib

    data = path.read_bytes()
    file_id = pysecrets.token_hex(8)
    rel = path.relative_to(exports_dir()).as_posix()
    con.execute("INSERT OR REPLACE INTO files (id, root, rel, kind, sha256, size, label, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, FILE_ROOT, rel, kind, hashlib.sha256(data).hexdigest(), len(data), label, clock.iso()))
    con.commit()
    return file_id


def make(con: sqlite3.Connection) -> dict:
    """Writes a new help file and returns `{preview_text, file_id}`."""
    words = text(con)
    folder = exports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = clock.iso().replace(":", "").replace("-", "")
    path = folder / f"Hal help file {stamp}.txt"
    path.write_text(words, encoding="utf-8")
    file_id = register_file(con, path, "help_file", "Help file")
    log.info("made a help file (%d characters)", len(words))
    return {"preview_text": words, "file_id": file_id}

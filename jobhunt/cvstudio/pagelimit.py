"""A CV's page limit ("Keep this CV to": no limit, one page, two pages) and the real page counts held to it.

- One source of truth for the limit: the CV's own `cv_documents.page_limit` once the person chooses one, else
  its CV type's `pages` rule (`limit_of`). Every reader goes through `limit_of`: the Checks tab's length check
  and the warning on My CV. The type's rule is only the default, so a change of type moves the limit until the
  person chooses one.
- A count is the number of pages of a real PDF, never a word estimate: the CV printed in its look by the
  browser printer, as Download PDF makes it (`measure`). Printing takes a second or more, so a version is
  printed once per printed page (`cv_versions.pages`, keyed by the page's hash in `pages_key`, so a new look or
  new contact details count again): after the editor saves, else the first time My CV asks. When no browser
  can print the count is not known (None). Hal's simple PDF is not counted: its pages are its own layout's,
  not the look's.
- The person is only warned (`words`); nothing they do is refused for its length.
- A test run (`HAL_TEST_MODE=1`) prints for a count only with `HAL_TEST_REAL_PRINT=1`; tests give a stand-in
  printer through `PRINT`, so saving a CV in a test never starts a browser.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from jobhunt.cvstudio import store

log = logging.getLogger(__name__)

KEY_CHARS = 20                  # as the export cache's file names, so a count's PDF serves Download PDF too
TEST_ENV, REAL_PRINT_ENV = "HAL_TEST_MODE", "HAL_TEST_REAL_PRINT"
CHOICE_WORDS = {0: "No limit", 1: "One page", 2: "Two pages"}
OVER = "This CV is {now}{pages} pages. You asked for {limit}."
UNKNOWN = "Hal cannot count this CV's pages: no browser on this computer can print it."

# (html, out, expect_name, expect_text) -> pages; raises `printer.PrintError` when it cannot print. None: the
# browser printer (`_browser`), the default.
PRINT: Callable[[str, Path, str, list[str]], int] | None = None


@contextlib.contextmanager
def _conn(con: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if con is not None:
        yield con
        return
    from jobhunt import db

    own = db.connect()
    try:
        yield own
    finally:
        own.close()


# ------------------------------------------------------------------------------------------------ the limit

def limit_of(doc_id: str | None = None, con: sqlite3.Connection | None = None) -> tuple[int, bool]:
    """(the CV's page limit, whether the person chose it); 0 is no limit. The lead CV when `doc_id` is None.

    Until the person chooses, the CV type in use sets it (its `pages`); a type that cannot be read sets none.
    """
    from jobhunt.cvstudio import types

    with _conn(con) as c:
        d = store.doc(c, doc_id)
        if d.get("page_limit") is not None:
            return int(d["page_limit"]), True
        type_id = store.type_in_use(c, d["id"])
        try:
            return int(types.load(type_id, c).pages or 0), False
        except LookupError as e:
            log.warning("CV %s: type %s is gone, so it has no page limit: %s", d["id"], type_id, e)
            return 0, False


def words(pages: int | None, limit: int, *, now: bool = False) -> str:
    """The warning in plain words, "" when the CV keeps to its limit or has none.

    `now` after a save ("This CV is now 3 pages. You asked for 2.").
    """
    if not limit:
        return ""
    if pages is None:
        return UNKNOWN
    if pages <= limit:
        return ""
    return OVER.format(now="now " if now else "", pages=pages, limit=limit)


# ---------------------------------------------------------------------------------- counting a printed CV

def _printing_allowed() -> bool:
    return PRINT is not None or os.environ.get(TEST_ENV) != "1" or os.environ.get(REAL_PRINT_ENV) == "1"


def _browser(html: str, out: Path, expect_name: str, expect_text: list[str]) -> int:
    """Prints with the browser printer only (no simple PDF) and returns the page count."""
    from jobhunt.cvstudio import printer

    return printer.to_pdf(html, out, expect_name=expect_name, max_pages=0, expect_text=expect_text)["pages"]


def _page(doc: Any, doc_id: str, con: sqlite3.Connection) -> tuple[str, str, str, str]:
    """(the printed page, its look, the name and first heading it must hold) as Download PDF prints `doc`."""
    from jobhunt.cvstudio import render, types

    d = store.doc(con, doc_id)
    try:
        type_ = types.load(d["type_id"], con) if d["type_id"] else None
    except (LookupError, ValueError) as e:
        log.warning("CV type %r could not be loaded: %s", d["type_id"], e)
        type_ = None
    try:
        layout = store.layout(con, doc_id) or {}
    except (LookupError, ValueError):
        layout = {}
    look_id = d["look_id"] or render.DEFAULT_LOOK
    v = render.view(doc, type_, render.profile_from_settings(), layout=layout)
    first = v.sections[0].title if v.sections else ""
    return render.html_of(v, look_id), look_id, v.name, first


def _key(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()[:KEY_CHARS]


def _print_count(html: str, look_id: str, name: str, first: str) -> int | None:
    """Pages of the page printed by the browser; None when it cannot print. The PDF joins the export cache."""
    from jobhunt.core import paths
    from jobhunt.cvstudio import printer

    if not _printing_allowed():
        return None
    with tempfile.TemporaryDirectory(prefix="hal-count-", ignore_cleanup_errors=True) as tmp:
        out = Path(tmp) / "cv.pdf"
        try:
            pages = (PRINT or _browser)(html, out, name, [first] if first else [])
        except printer.PrintError as e:
            log.info("could not print the CV to count its pages: %s", e)
            return None
        cached = paths.cache_dir() / "cv-print" / f"{_key(html)}-{look_id}.pdf"
        if out.exists() and not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, cached)
    return pages


def count_doc(doc: Any, doc_id: str, con: sqlite3.Connection | None = None) -> int | None:
    """Pages of a document not saved yet, printed as a version of CV `doc_id` would be; None when not known."""
    with _conn(con) as c:
        html, look_id, name, first = _page(doc, doc_id, c)
    return _print_count(html, look_id, name, first)


def known(version_id: str, con: sqlite3.Connection | None = None) -> tuple[int | None, bool]:
    """(the version's page count, whether it was counted for the page as it prints now), without printing."""
    with _conn(con) as c:
        v = store.version(version_id, con=c)
        html, _, _, _ = _page(store.get(version_id, con=c), v["doc_id"], c)
        row = c.execute("SELECT pages, pages_key FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    if row is None or row["pages_key"] != _key(html):
        return None, False
    return row["pages"], True


def measure(version_id: str, con: sqlite3.Connection | None = None) -> int | None:
    """The version's page count, printed once per printed page and kept with the version; None when no
    browser can print it."""
    with _conn(con) as c:
        v = store.version(version_id, con=c)
        html, look_id, name, first = _page(store.get(version_id, con=c), v["doc_id"], c)
        key = _key(html)
        row = c.execute("SELECT pages, pages_key FROM cv_versions WHERE id = ?", (version_id,)).fetchone()
    if row is not None and row["pages_key"] == key:
        return row["pages"]
    if not _printing_allowed():
        return None
    pages = _print_count(html, look_id, name, first)
    with _conn(con) as c, store.writing(c):
        c.execute("UPDATE cv_versions SET pages = ?, pages_key = ? WHERE id = ?", (pages, key, version_id))
    log.info("CV version %s prints on %s pages", version_id, pages)
    return pages


def state(doc_id: str | None = None, con: sqlite3.Connection | None = None, *, count: bool = True,
          now: bool = False) -> dict:
    """A CV's limit and its version in use's pages: {limit, chosen, choices, pages, counted, over, words}.

    With `count` the version is printed when it has no count yet (`measure`); without, only a kept count is
    used. `counted` is False while the count is not known; `words` is the warning, "" when there is none, and
    says "now" with `now` (after a save).
    """
    with _conn(con) as c:
        limit, chosen = limit_of(doc_id, c)
        vid = store.current(c, doc_id)[0]
        pages = measure(vid, c) if count else known(vid, c)[0]
    return {"limit": limit, "chosen": chosen, "choices": [[k, v] for k, v in CHOICE_WORDS.items()],
            "pages": pages, "counted": pages is not None,
            "over": pages is not None and bool(limit) and pages > limit, "words": words(pages, limit, now=now)}


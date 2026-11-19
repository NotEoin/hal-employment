"""What a preview shows of a CV that sends the person's own file: the file's pages when it is a PDF.

A CV set to "Your original file" (`send` "original") sends its kept file (`baseline_file_id`) byte for byte,
so a preview of Hal's page there showed something employers never get (reported 2026-11-19). `view` says
what each preview of such a CV shows instead:

- a PDF: pictures of its own pages, from `GET /cv/original/page/{n}` (`parse_routes.page`), one address per
  page. Each address names the CV and the file, so a new file gets new pictures rather than the browser's
  copy of the old ones;
- a Word file, Markdown or pasted text: no pictures (Hal does not print those), so the preview stays Hal's
  reading of the file and says so;
- a file Hal can no longer read (changed or gone on disk): the reason, since it cannot be sent either.

The kind comes from the bytes, as the page route decides it (`parse.kind_of`), not from the stored MIME
type, so a PDF saved without its ending still shows its pages.
"""
from __future__ import annotations

import sqlite3
from urllib.parse import quote

from jobhunt.cvstudio import parse, pdfread, store
from jobhunt.cvstudio.model import CVError

PAGE_URL = "/api/v2/cv/original/page/{n}?cv={cv}&f={file}"
GONE_WORDS = "Hal cannot find your file {name} any more, so it cannot be shown or sent."


def view(con: sqlite3.Connection, doc_id: str) -> dict | None:
    """What a preview of the CV shows of its own file, or None when the CV sends Hal's copy (or has no file).

    {"kind", "name", "pages", "page_urls", "why", "version_id"}: `kind` is "pdf", "docx", "md" or "txt", None
    with `why` when the file cannot be read; `page_urls` holds one picture address per page of a PDF, else is
    empty; `version_id` is the CV's version in use, Hal's reading to show when there are no pictures.
    """
    d = store.doc(con, doc_id)
    file_id = d["baseline_file_id"]
    if d["send"] != "original" or not file_id:
        return None
    row = store.file_row(file_id, con=con)
    base = {"name": row["name"], "version_id": store.current(con, d["id"])[0]}
    unread = {**base, "kind": None, "pages": row["pages"], "page_urls": []}
    try:
        data = store.file_bytes(file_id, con=con)
        kind = parse.kind_of(row["name"] or "", data)
    except (CVError, parse.CVReadError) as e:
        return {**unread, "why": str(e)}
    except OSError:
        return {**unread, "why": GONE_WORDS.format(name=row["name"])}
    if kind != "pdf":
        return {**base, "kind": kind, "pages": row["pages"], "page_urls": [], "why": ""}
    # `pages` is filled in by the reading (`parse_actions`), so a file still being read is counted here
    pages = row["pages"] or pdfread.pages(data)
    urls = [PAGE_URL.format(n=n, cv=quote(d["id"]), file=quote(file_id)) for n in range(1, pages + 1)]
    return {**base, "kind": kind, "pages": pages, "page_urls": urls, "why": ""}

"""A stand-in for the profile store: takes the facts the ingest finds and keeps none of them yet.

The real store (profile tables, merging and conflicts) replaces this module and keeps the signature of
`add_found`, which is all the ingest calls. Until then a fact is logged at debug level by kind and field
names only, never by value, because the values are about the person.
"""
from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)

KINDS = ("job", "education", "skill", "project", "achievement", "certificate", "basic", "link")


def add_found(con: sqlite3.Connection, kind: str, fields: dict, evidence: dict) -> int | None:
    """Takes one fact Hal found (`state` hal) with where it came from; returns its item id, None here.

    `evidence` is {source_rel, quote, field, doc_type}: the note inside the record, the verbatim words, the
    field they support and the kind of document. Raises ValueError for a kind the profile does not hold.
    """
    if kind not in KINDS:
        raise ValueError(f"the profile holds no {kind!r} facts")
    log.debug("profile fact found: %s with %s from a %s", kind, ", ".join(sorted(fields)),
              evidence.get("doc_type") or "document")
    return None

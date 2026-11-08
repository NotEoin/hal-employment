"""Which project card each file of the record speaks for, as writing reads it.

`Files.of(con)` reads the visible project cards' evidence once. A file is a listing (`Files.listings`) when
its evidence backs `LISTING_CARDS` or more visible project cards, or its project facts name several projects
(`duplicates.listing_files`): a CV, a dossier of all the person's work. `Files.ties(rel)` is False for one.

Design decisions, each with its reason:

- A listing ties nothing (2026-11-08): a note describing about ten projects backed a hidden copy of one
  project and two other visible cards, so the copy's name became an alias of both, and a letter placed the
  copy's network sniffer in an autonomous navigation project. So a listing never makes an alias, never ties
  a passage to a card by its file, and never gives a card a quote that names another card and not it
  (`profile.writing`, `writing.plan.sources`). Only its passages and quotes that name one card count for
  that card.
- This is a view for writing, never a change: the record and the review page keep every piece of evidence
  as it was stored.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from jobhunt.profile import duplicates, store

LISTING_CARDS = 2          # visible project cards one file backs that make it a listing rather than one's own


@dataclass(frozen=True)
class Files:
    """The record's files as writing reads them: which list several projects (`listings`)."""

    listings: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, con: sqlite3.Connection) -> Files:
        """The view of the record's files from the visible project cards and their evidence."""
        backers: dict[str, set[int]] = {}
        for it in store.items(con, "project", hidden=False):
            for e in it["evidence"]:
                backers.setdefault(e["source_rel"], set()).add(it["id"])
        shared = {rel for rel, ids in backers.items() if len(ids) >= LISTING_CARDS}
        return cls(frozenset(shared | duplicates.listing_files(con)))

    def ties(self, rel: str) -> bool:
        """True when a file may tie a card to an alias, a passage or another card: it is no listing."""
        return rel not in self.listings

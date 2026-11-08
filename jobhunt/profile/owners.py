"""Which project card each file of the record speaks for, as writing reads it.

`Files.of(con)` reads the visible project cards' evidence once. A repository or project file whose name is a
visible card's name, the end of its link or the name of a card merged into it, case and separators aside
("hal-employment" for "Hal Employment", `key`), is that card's own (`Files.owners`), and
`Files.belongs(card, rel)` is False for any other card. A file no card owns is a listing (`Files.listings`)
when its evidence backs `LISTING_CARDS` or more visible project cards, or its project facts name several
projects (`duplicates.listing_files`): a CV, a dossier of all the person's work. `Files.ties(rel)` is False
for one.

Design decisions, each with its reason:

- A listing ties nothing (2026-11-08): a note describing about ten projects backed a hidden copy of one
  project and two other visible cards, so the copy's name became an alias of both, and a letter placed the
  copy's network sniffer in an autonomous navigation project. So a listing never makes an alias, never ties
  a passage to a card by its file, and never gives a card a quote that names another card and not it
  (`profile.writing`, `writing.plan.sources`). Only its passages and quotes that name one card count for
  that card.
- A project's own file belongs to it (2026-11-08): a card of an earlier project carried evidence quoted from
  the repository of a later one with a longer name, so its tools took the later one's database and front
  end, its aliases took the repository's name, and letters said the earlier project used them. Only a whole
  name counts (`key`), never one name inside another, as a dossier named "Portfolio ..." would otherwise
  belong to a card named "Portfolio".
- This is a view for writing, never a change: the record and the review page keep every piece of evidence
  as it was stored.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from jobhunt.profile import duplicates, store

LISTING_CARDS = 2          # visible project cards one file backs that make it a listing rather than one's own
# Document types that hold one project; `profile.writing.PROJECT_FILES` is this.
PROJECT_FILES = ("repository", "project", "coursework", "dissertation")
# File names that name no project: a repository's note is named after its folder, but these are not.
GENERIC_FILES = {"readme", "index", "notes", "main", "untitled", "project", "code", "src", "app", "cv"}
NAME_MIN = 3               # a file name this short ("ui", "v2") names no project
FOLDER_DEPTH = 4           # "Sources/<category>/<folder>/<file>": a file inside a project's own folder


def file_name(source_rel: str) -> str:
    """The name a project's file goes by: its note's name, or its folder's when the note is a README inside
    one ("Sources/Code/<repository>/README.md"); "" when it names no project."""
    parts = [p for p in source_rel.replace("\\", "/").split("/") if p]
    stems = [p.rsplit(".", 1)[0] if "." in p else p for p in parts[-1:]]
    if len(parts) >= FOLDER_DEPTH:
        stems.append(parts[-2])
    return next((s for s in stems if s.lower() not in GENERIC_FILES and len(s) >= NAME_MIN), "")


def key(name: str) -> str:
    """A name with case and separators aside: "hal-employment", "Hal_Employment" and "Hal Employment" are one."""
    return " ".join(w for w in re.split(r"[\W_]+", (name or "").lower()) if w)


def _names(item: dict, merged: list[dict]) -> set[str]:
    """The keys of every name a card goes by for owning a file: its own, its link's end, its merged cards'."""
    link = str(item["fields"].get("link") or "").rstrip("/")
    said = [item["fields"].get("name", ""), link.rsplit("/", 1)[-1] if link else "",
            *(m["fields"].get("name", "") for m in merged if m.get("merged_into") == item["id"])]
    return {key(s) for s in said if key(s)}


@dataclass(frozen=True)
class Files:
    """The record's files as writing reads them: which list several projects (`listings`) and which are one
    card's own (`owners`, {file: card id})."""

    listings: frozenset[str] = field(default_factory=frozenset)
    owners: dict[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, con: sqlite3.Connection) -> Files:
        """The view of the record's files from the visible project cards and their evidence."""
        visible = store.items(con, "project", hidden=False)
        merged = [it for it in store.items(con, "project") if it.get("merged_into")]
        names = {it["id"]: _names(it, merged) for it in visible}
        backers: dict[str, set[int]] = {}
        owners: dict[str, int] = {}
        for it in visible:
            for e in it["evidence"]:
                backers.setdefault(e["source_rel"], set()).add(it["id"])
                named = key(file_name(e["source_rel"])) if e["doc_type"] in PROJECT_FILES else ""
                found = [i for i, own in names.items() if named and named in own]
                if len(found) == 1:
                    owners[e["source_rel"]] = found[0]
        shared = {rel for rel, ids in backers.items() if len(ids) >= LISTING_CARDS}
        return cls(frozenset((shared | duplicates.listing_files(con)) - set(owners)), owners)

    def ties(self, rel: str) -> bool:
        """True when a file may tie a card to an alias, a passage or another card: it is no listing."""
        return rel not in self.listings

    def belongs(self, item_id: int, rel: str) -> bool:
        """True when a file may speak for card `item_id`: it is no listing and no other card's own file."""
        return self.ties(rel) and self.owners.get(rel, item_id) == item_id

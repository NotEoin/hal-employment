"""The matching index: which item a new fact belongs to, without reading every item and source each time.

`store.add_found` asks `match(con, kind, fields)` for the first item, by id, whose own fields or any of
whose sources' facts `merge.same_item` calls the same thing, and tells the index with `added` what it wrote.
The answer is the one a full scan gives; only the work differs.

Design decisions, each with its reason:

- Every fact used to read every item and source of its kind (2026-11-07): an ingest of a few thousand facts
  did millions of comparisons. The index holds, per connection, each item's fields and what each source
  said, and finds candidates through `merge.match_keys` (a skill's compared name, a job's employer, a
  basic's key, a link's address). Every candidate is still checked with `merge.same_item`. Kinds matched by
  closeness (education, projects, certificates, achievements) have no key and are compared with every item
  of their kind, from memory rather than from the database.
- The index is only trusted while nothing else has written: it is kept with the connection's
  `total_changes` and SQLite's `PRAGMA data_version` (which moves when another connection commits), and is
  read afresh when either has moved since `added` last saw them. So an edit, a hide or another process's
  ingest costs one full read, never a wrong match.
- A connection is named by a token in its own temporary table, not by the object: `sqlite3.Connection`
  takes no weak reference, and holding the object would keep a closed or forgotten connection's file open.
  Indexes of at most `CONNECTIONS` connections are kept.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import OrderedDict

from jobhunt.profile import merge

CONNECTIONS = 4            # the server, its worker, an ingest and a command at once
TOKEN_TABLE = "profile_index_token"


class _Index:
    """Every item's fields, then what each of its sources said, by kind and by match key."""

    def __init__(self) -> None:
        self.mark: tuple[int, int] | None = None
        self.known: dict[int, list[dict]] = {}
        self.by_kind: dict[str, list[int]] = {}
        self.by_key: dict[tuple[str, str], set[int]] = {}

    def add(self, item_id: int, kind: str, fields: dict) -> None:
        """One more set of facts the item answers to; the first an item gets stands for its own fields."""
        if item_id not in self.known:
            self.known[item_id] = []
            self.by_kind.setdefault(kind, []).append(item_id)
        self.known[item_id].append(fields)
        self._keys(item_id, kind, fields)

    def own(self, item_id: int, kind: str, fields: dict) -> None:
        """The item's own fields, after it was worked out again from its sources."""
        self.known[item_id][0] = fields
        self._keys(item_id, kind, fields)

    def _keys(self, item_id: int, kind: str, fields: dict) -> None:
        for key in merge.match_keys(kind, fields) or ():
            self.by_key.setdefault((kind, key), set()).add(item_id)

    def candidates(self, kind: str, fields: dict) -> list[int]:
        """The items that may be the same as `fields`, by id."""
        keys = merge.match_keys(kind, fields)
        if keys is None:
            return self.by_kind.get(kind, [])
        return sorted({i for k in keys for i in self.by_key.get((kind, k), ())})


_indexes: OrderedDict[str, _Index] = OrderedDict()
_lock = threading.Lock()


def _token(con: sqlite3.Connection) -> str:
    """This connection's own name, made the first time it is asked for."""
    try:
        row = con.execute(f"SELECT token FROM temp.{TOKEN_TABLE}").fetchone()
    except sqlite3.OperationalError:     # no such table: a connection the index has not seen
        row = None
    if row is not None:
        return row[0]
    token = uuid.uuid4().hex
    con.execute(f"CREATE TEMP TABLE IF NOT EXISTS {TOKEN_TABLE} (token TEXT NOT NULL)")
    con.execute(f"INSERT INTO temp.{TOKEN_TABLE} (token) VALUES (?)", (token,))
    return token


def _mark(con: sqlite3.Connection) -> tuple[int, int]:
    return con.total_changes, con.execute("PRAGMA data_version").fetchone()[0]


def _read(con: sqlite3.Connection) -> _Index:
    """The index read whole from the database."""
    idx = _Index()
    kinds = {}
    for row in con.execute("SELECT id, kind, fields FROM profile_items ORDER BY id"):
        kinds[row[0]] = row[1]
        idx.add(row[0], row[1], json.loads(row[2]))
    for row in con.execute("SELECT item_id, said FROM profile_evidence ORDER BY id"):
        if row[0] in kinds:
            idx.add(row[0], kinds[row[0]], json.loads(row[1]))
    return idx


def _index(con: sqlite3.Connection) -> tuple[str, _Index]:
    token = _token(con)
    idx = _indexes.get(token)
    if idx is None or idx.mark != _mark(con):
        idx = _read(con)
        idx.mark = _mark(con)
        _indexes[token] = idx
    _indexes.move_to_end(token)
    while len(_indexes) > CONNECTIONS:
        _indexes.popitem(last=False)
    return token, idx


def match(con: sqlite3.Connection, kind: str, fields: dict) -> int | None:
    """The id of the first item `fields` belongs to, by its own fields or by what any of its sources said."""
    with _lock:
        _, idx = _index(con)
        for item_id in idx.candidates(kind, fields):
            if any(merge.same_item(kind, known, fields) for known in idx.known[item_id]):
                return item_id
    return None


def forget(con: sqlite3.Connection) -> None:
    """Drops this connection's index, so the next `match` reads it afresh (after a rolled-back write)."""
    with _lock:
        _indexes.pop(_token(con), None)


def added(con: sqlite3.Connection, item_id: int, kind: str, said: dict, fields: dict) -> None:
    """Tells the index that one source said `said` about the item, whose own fields are now `fields`.

    Called after the write is committed, so the index matches the database again. When another connection
    committed since `match` read the index, the index is dropped and the next `match` reads it afresh.
    """
    with _lock:
        token = _token(con)
        idx = _indexes.get(token)
        if idx is None:
            return
        if idx.mark is None or idx.mark[1] != _mark(con)[1]:
            del _indexes[token]
            return
        if item_id not in idx.known:
            idx.add(item_id, kind, fields)
        idx.add(item_id, kind, said)
        idx.own(item_id, kind, fields)
        idx.mark = _mark(con)

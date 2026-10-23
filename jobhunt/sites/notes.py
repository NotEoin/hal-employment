"""Notes about job sites: what works on each site and what to avoid, as data the engines read.

A note has a kind (one hook each in the engines, so a note acts the same way every time), a scope (global,
an application system, a site, one employer's tenant or one job's address) and words the person reads.
Built-in notes ship in `seed.toml`; a person's switch-off, wording or counters for one are a `site_notes`
row naming its `seed_id`, so an update can improve a built-in note without undoing what the person chose.
Notes the person adds, or that Hal learns, are rows of their own ("db:<n>").

Design decisions:
- The most specific active note of a kind wins: job, then tenant, then site, then application system, then
  global; among equals, the note with more conditions, then the person's own over learned over built in,
  then the newest. Two notes for the same thing that disagree are a conflict, shown on the site's card,
  except where the loser is a global built-in default, which a site note exists to override.
- Conditions on the address (`url_contains`, `host_contains` and the like) are checked once in
  `for_page`; any other condition (`label`, `field_kind`, `source`) must be named by the caller of
  `NoteSet.first` or `all`, so a note for one field never leaks onto another.
- A note used at least `STALE_AFTER_USES` times and then failed `STALE_AFTER_FAILS` times in a row
  is marked "may be out of date"; it stays on, because one bad day on a site is not proof.
- Every function opens and closes its own connection unless given one, because the engines call them from
  their own threads.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from jobhunt.core import clock

log = logging.getLogger(__name__)

SEED_PATH = Path(__file__).with_name("seed.toml")
SEED_PREFIX, ROW_PREFIX = "seed:", "db:"
SCOPE_RANK = {"global": 0, "ats": 1, "site": 2, "tenant": 3, "job": 4}
ORIGIN_RANK = {"builtin": 0, "hal": 1, "learned": 2, "you": 3}
STATUSES = ("active", "proposed", "off")
STALE_AFTER_USES = 3     # used 3 times ...
STALE_AFTER_FAILS = 3    # ... then failing 3 times
MAX_TEXT = 300           # a note's words are one or two sentences; longer is a report, not a note

# What each kind may say in `act`. `widget` has no Enter option: Enter can send a form.
KINDS: dict[str, set[str]] = {
    "open_url": {"append", "rewrite", "board"},
    "wait_redirect": {"host", "until", "max_s"},
    "press_first": {"control"},
    "form_frame": {"host_contains"},
    "ignore_frame": {"host_contains", "pattern", "except"},
    "cookie_button": {"text", "never", "patterns"},
    "needs_account": {"sign_in_url"},
    "buttons": {"apply", "next", "submit", "success", "closed", "closed_url", "applied", "bot_check", "blocked",
                "code_wall"},
    "field_transform": {"transform", "read", "search"},
    "field_answer": {"value"},
    "field_skip": {"value"},
    "widget": {"no_escape", "type_slowly", "scroll_list", "wait_suggestions_ms"},
    "upload_via": {"control", "mode", "wait_s"},
    "timing": {"settle_s", "page_timeout_s", "fill_timeout_s", "max_presses"},
    "note": {"job_board"},
}
TRANSFORMS = re.compile(r"^(city_only|digits_only|uk_phone|date:.+|max_len:\d+)$")
# Kinds where every matching note adds to the others, so several at once are expected, not a conflict.
ADDITIVE = {"buttons", "note", "form_frame", "ignore_frame", "cookie_button"}
# Conditions checked against the page in `for_page`; every other condition is named by the caller.
PAGE_CONDS = {"url_contains", "url_matches", "host_contains", "host_contains_any", "not_host_contains",
              "job_id_prefix", "step"}
FIELD_CONDS = {"label", "field_kind", "source"}

# Application systems by a part of their address, in the order the old engines tested them.
ATS_HOSTS = (("ashbyhq.com", "ashby"), ("lever.co", "lever"), ("greenhouse.io", "greenhouse"),
             ("workable.com", "workable"), ("myworkdayjobs.com", "workday"), ("smartrecruiters.com", "smartrecruiters"),
             ("recruitee.com", "recruitee"), ("personio.", "personio"), ("teamtailor.com", "teamtailor"),
             ("bamboohr.com", "bamboohr"), ("jobvite.com", "jobvite"), ("icims.com", "icims"),
             ("pinpointhq.com", "pinpoint"))
ATS_NAMES = {"ashby": "Ashby", "lever": "Lever", "greenhouse": "Greenhouse", "workable": "Workable",
             "workday": "Workday", "smartrecruiters": "SmartRecruiters", "recruitee": "Recruitee",
             "personio": "Personio", "teamtailor": "Teamtailor", "bamboohr": "BambooHR", "jobvite": "Jobvite",
             "icims": "iCIMS", "pinpoint": "Pinpoint"}
SECOND_LEVEL = ("co", "com", "org", "ac", "gov")   # reed.co.uk keeps three labels, as the old `_site` did
GREENHOUSE_BOARDS = re.compile(r"^(job-boards|boards)\.(eu\.)?greenhouse\.io$")


class NoteError(ValueError):
    """A note that cannot be saved as given, with words for the person."""


@dataclass(frozen=True)
class Note:
    """One note: what it is for, where it applies, when, what Hal does and the words shown."""

    id: str
    kind: str
    scope_kind: str
    scope_key: str
    cond: dict
    act: dict
    text: str
    origin: str
    updated: str = ""
    created: str = ""

    def rank(self) -> tuple:
        """Sort key: most specific first (scope, then the number of conditions), then origin, then newest."""
        return (-SCOPE_RANK[self.scope_kind], -len(self.cond), -ORIGIN_RANK.get(self.origin, 0), _neg(self.updated))

    def target(self) -> str:
        """What the note is about inside a page: its field conditions, so notes for one field compare."""
        return json.dumps({k: v for k, v in self.cond.items() if k in FIELD_CONDS}, sort_keys=True)


def _neg(text: str) -> str:
    """An ISO time that sorts newest first."""
    return "".join(chr(0x10FFFF - ord(c)) for c in text)


@dataclass
class NoteSet:
    """The active notes for one page, most specific first within each kind."""

    notes: list[Note]
    url: str = ""
    site: str = ""
    ats: str | None = None
    job_id: str | None = None
    off: list[Note] = field(default_factory=list)

    def all(self, kind: str, **cond) -> list[Note]:
        """Every active note of `kind` whose conditions match `cond`, most specific first."""
        return sorted((n for n in self.notes if n.kind == kind and _cond_ok(n.cond, cond)), key=Note.rank)

    def first(self, kind: str, **cond) -> Note | None:
        """The note of `kind` that wins for `cond`, or None."""
        found = self.all(kind, **cond)
        return found[0] if found else None

    def for_field(self, label: str, **cond) -> list[Note]:
        """The field notes (transform, answer, skip) whose label pattern matches `label`, winners first."""
        kinds = ("field_answer", "field_skip", "field_transform")
        return sorted((n for n in self.notes if n.kind in kinds and "label" in n.cond
                       and _cond_ok(n.cond, {"label": label, **cond})), key=Note.rank)

    def conflicts(self) -> list[dict]:
        """Notes that disagree about the same thing on this page, each with the one Hal uses."""
        groups: dict[tuple[str, str], list[Note]] = {}
        for n in self.notes:
            if n.kind not in ADDITIVE:
                groups.setdefault((n.kind, n.target()), []).append(n)
        out = []
        for (kind, _), found in groups.items():
            found.sort(key=Note.rank)
            winner = found[0]
            for loser in found[1:]:
                if loser.scope_kind == "global" and loser.origin == "builtin":
                    continue
                if _disagree(winner.act, loser.act):
                    out.append({"kind": kind, "used": winner.id, "not_used": loser.id,
                                "words": f"Two notes disagree. Hal uses \"{winner.text}\" ({scope_words(winner)}) "
                                         f"instead of \"{loser.text}\" ({scope_words(loser)})."})
        return out

    def wire(self) -> list[dict]:
        """The notes as sent to the browser helper with every command."""
        return [{"id": n.id, "kind": n.kind, "scope": n.scope_kind, "key": n.scope_key, "cond": n.cond,
                 "act": n.act, "text": n.text} for n in sorted(self.notes, key=lambda n: (n.kind, n.rank()))]


def _disagree(a: dict, b: dict) -> bool:
    """True when two acts set the same key to different values (or share no key at all)."""
    shared = set(a) & set(b)
    if not shared:
        return a != b
    return any(a[k] != b[k] for k in shared)


def _cond_ok(note_cond: dict, given: dict) -> bool:
    """True when every non-page condition of a note is named in `given` and matches it."""
    for key, want in note_cond.items():
        if key in PAGE_CONDS:
            continue
        if key not in given or given[key] is None:
            return False
        have = str(given[key])
        if key == "label":
            try:
                if not re.search(str(want), have, re.I):
                    return False
            except re.error:
                return False
        elif isinstance(want, list):
            if have.lower() not in {str(w).lower() for w in want}:
                return False
        elif have.lower() != str(want).lower():
            return False
    return True


def page_ok(cond: dict, url: str, host: str, job_id: str | None, step: int | None) -> bool:
    """True when a note's address conditions hold for this page."""
    if "url_contains" in cond and str(cond["url_contains"]) not in url:
        return False
    if "url_matches" in cond and not re.search(str(cond["url_matches"]), url, re.I):
        return False
    if "host_contains" in cond and str(cond["host_contains"]) not in host:
        return False
    if "host_contains_any" in cond and not any(str(h) in host for h in cond["host_contains_any"]):
        return False
    if "not_host_contains" in cond and str(cond["not_host_contains"]) in host:
        return False
    if "job_id_prefix" in cond and not (job_id or "").startswith(str(cond["job_id_prefix"])):
        return False
    if "step" in cond and step is not None and int(cond["step"]) != int(step):
        return False
    return True


# Sites and application systems -----------------------------------------------------------------------------

def host_of(url: str) -> str:
    """The lower-case host of an address, without its port."""
    return (urlparse(url or "").netloc or "").lower().split(":")[0]


def registrable(url: str) -> str:
    """The site an address belongs to, such as reed.co.uk for secure.reed.co.uk."""
    labels = [p for p in host_of(url).split(".") if p]
    if not labels:
        return ""
    keep = 3 if len(labels) > 2 and labels[-2] in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def site_key(url: str) -> str:
    """The key Hal keeps a site's notes and sign-in under: its registrable domain, except that each
    Workday employer (acme.wd3.myworkdayjobs.com) and each Greenhouse board (boards.greenhouse.io/acme)
    is its own, so one employer's sign-in never marks every other employer on the same system."""
    host = host_of(url).removeprefix("www.")
    if host.endswith(".myworkdayjobs.com"):
        return host
    if GREENHOUSE_BOARDS.match(host):
        u = urlparse(url)
        slug = next((p for p in u.path.split("/") if p and p not in ("embed", "job_app")), "")
        q = re.search(r"(?:^|&)for=([A-Za-z0-9_-]+)", u.query)
        slug = q.group(1) if q else slug
        return f"boards.greenhouse.io/{slug.lower()}" if slug else "boards.greenhouse.io"
    return registrable(url)


def ats_of(url: str) -> str | None:
    """The application system an address belongs to ("workday"), or None for an employer's own site."""
    host = host_of(url)
    return next((ats for part, ats in ATS_HOSTS if part in host), None)


def scope_words(note: Note) -> str:
    """Where a note applies, in plain words: "this site", "every Workable site", "every site"."""
    if note.scope_kind == "global":
        return "every site"
    if note.scope_kind == "ats":
        return f"every {ATS_NAMES.get(note.scope_key, note.scope_key)} site"
    if note.scope_kind == "job":
        return "this job only"
    return note.scope_key


# Storage -----------------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _db(con: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if con is not None:
        yield con
        return
    from jobhunt import db

    own = db.connect()
    try:
        yield own
    finally:
        own.close()


_SEED_CACHE: dict[tuple[str, float], list[Note]] = {}


def seed_notes(path: Path | None = None) -> list[Note]:
    """The built-in notes, read from `seed.toml` (cached until the file changes)."""
    path = path or SEED_PATH
    stamp = (str(path), path.stat().st_mtime)
    if stamp not in _SEED_CACHE:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        notes = []
        for item in raw.get("note", []):
            note = Note(id=SEED_PREFIX + item["id"], kind=item["kind"], scope_kind=item.get("scope", "global"),
                        scope_key=item.get("key", ""), cond=dict(item.get("cond", {})), act=dict(item.get("act", {})),
                        text=item["text"], origin="builtin")
            validate(note.kind, note.scope_kind, note.cond, note.act, note.text)
            notes.append(note)
        if len({n.id for n in notes}) != len(notes):
            raise NoteError("seed.toml repeats a note id")
        _SEED_CACHE.clear()
        _SEED_CACHE[stamp] = notes
    return list(_SEED_CACHE[stamp])


def _loads(text: str | None, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


@dataclass
class Stored:
    """One note as the listing sees it: the note, its state and its counters."""

    note: Note
    status: str
    uses: int = 0
    wins: int = 0
    fails: int = 0
    stale: bool = False
    said: str | None = None
    private: bool = False
    seed: bool = False
    evidence: list = field(default_factory=list)


def stored(con: sqlite3.Connection | None = None, seed: Path | None = None) -> list[Stored]:
    """Every note, built in or not, with its state: active, proposed or off."""
    with _db(con) as c:
        rows = c.execute("SELECT * FROM site_notes ORDER BY rowid").fetchall()
    shadows = {r["seed_id"]: r for r in rows if r["seed_id"]}
    out = []
    for n in seed_notes(seed):
        r = shadows.get(n.id.removeprefix(SEED_PREFIX))
        if r is None:
            out.append(Stored(n, "active", seed=True))
            continue
        text = r["text"] or n.text
        note = Note(n.id, n.kind, n.scope_kind, n.scope_key, n.cond, n.act, text, n.origin, r["updated"], r["created"])
        out.append(Stored(note, r["status"], r["uses"], r["wins"], r["fails"], bool(r["stale"]), r["said"],
                          bool(r["private"]), True, _loads(r["evidence"], [])))
    for r in rows:
        if r["seed_id"]:
            continue
        note = Note(r["id"], r["kind"], r["scope_kind"], r["scope_key"], _loads(r["cond_json"], {}),
                    _loads(r["act_json"], {}), r["text"], r["origin"], r["updated"], r["created"])
        out.append(Stored(note, r["status"], r["uses"], r["wins"], r["fails"], bool(r["stale"]), r["said"],
                          bool(r["private"]), False, _loads(r["evidence"], [])))
    return out


def _applies(note: Note, url: str, *, ats: str | None, job_id: str | None) -> bool:
    kind, key = note.scope_kind, note.scope_key
    if kind == "global":
        return True
    if kind == "ats":
        return bool(ats) and key == ats
    if kind == "site":
        return key == registrable(url) or key == site_key(url)
    if kind == "tenant":
        return key == site_key(url)
    if kind == "job":
        return bool(key) and (key == job_id or (url or "").startswith(key))
    return False


def for_page(url: str, *, ats: str | None = None, job_id: str | None = None, step: int | None = None,
             con: sqlite3.Connection | None = None) -> NoteSet:
    """The active notes for a page: those whose scope covers it and whose address conditions hold."""
    ats = ats or ats_of(url)
    host = host_of(url)
    active, off = [], []
    for s in stored(con):
        n = s.note
        if not _applies(n, url, ats=ats, job_id=job_id) or not page_ok(n.cond, url, host, job_id, step):
            continue
        if s.status == "active":
            active.append(n)
        elif s.status == "off":
            off.append(n)
    return NoteSet(active, url=url, site=site_key(url), ats=ats, job_id=job_id, off=off)


def validate(kind: str, scope_kind: str, cond: dict, act: dict, text: str) -> None:
    """Raises `NoteError` unless the note is one Hal can act on deterministically."""
    if kind not in KINDS:
        raise NoteError(f"Hal has no kind of note called {kind!r}.")
    if scope_kind not in SCOPE_RANK:
        raise NoteError(f"A note applies to one site, an application system or every site, not {scope_kind!r}.")
    if not isinstance(cond, dict) or not isinstance(act, dict):
        raise NoteError("A note's conditions and action must be objects.")
    if not (text or "").strip():
        raise NoteError("Say in a few words what the note is for.")
    if len(text) > MAX_TEXT:
        raise NoteError(f"Keep a note under {MAX_TEXT} characters.")
    extra = set(act) - KINDS[kind]
    if extra:
        raise NoteError(f"A {kind} note cannot say {', '.join(sorted(extra))}.")
    unknown = set(cond) - PAGE_CONDS - FIELD_CONDS
    if unknown:
        raise NoteError(f"Hal cannot check {', '.join(sorted(unknown))} on a page.")
    for key in ("label", "url_matches"):
        if key in cond:
            try:
                re.compile(str(cond[key]))
            except re.error as e:
                raise NoteError(f"The pattern for {key} does not work: {e}") from e
    if kind in ("field_transform", "field_answer", "field_skip") and not ({"label", "field_kind"} & set(cond)):
        raise NoteError("Say which question the note is for.")
    if kind == "field_transform" and "transform" in act and not TRANSFORMS.match(str(act["transform"])):
        raise NoteError("Hal can type only the city, only the digits, a UK phone number, a date or a shorter answer.")
    if kind == "field_answer" and not str(act.get("value", "")).strip():
        raise NoteError("Say what Hal should answer.")
    if kind == "press_first" and not str(act.get("control", "")).strip():
        raise NoteError("Say which button Hal should press first.")
    if kind == "upload_via" and act.get("mode", "select") not in ("select", "drop"):
        raise NoteError("An upload is by its button (select) or by dropping the file (drop).")


def _private(kind: str, cond: dict) -> bool:
    """A fixed answer is the person's own: never exported or shared."""
    return kind in ("field_answer", "field_skip")


def _private_label(cond: dict) -> bool:
    """True when the note's question is one of the private questions, whose answers notes never give."""
    from jobhunt import autofill

    label = str(cond.get("label") or "")
    plain = re.sub(r"[\\^$()|?*+\[\]{}.]", " ", label)
    return bool(label) and bool(autofill.private_question({"label": label})
                                or autofill.private_question({"label": plain}))


def _next_id(con: sqlite3.Connection) -> str:
    n = con.execute("SELECT COALESCE(MAX(rowid), 0) + 1 FROM site_notes").fetchone()[0]
    while con.execute("SELECT 1 FROM site_notes WHERE id = ?", (f"{ROW_PREFIX}{n}",)).fetchone():
        n += 1
    return f"{ROW_PREFIX}{n}"


def add(scope_kind: str, scope_key: str, kind: str, cond: dict, act: dict, text: str, *, said: str | None = None,
        origin: str = "you", status: str = "active", evidence: list | None = None,
        con: sqlite3.Connection | None = None) -> str:
    """Saves a note and returns its id; `NoteError` when it is not one Hal can act on."""
    text = (text or "").strip()
    validate(kind, scope_kind, cond, act, text)
    if origin not in ORIGIN_RANK or origin == "builtin":
        raise NoteError("Built-in notes come only from Hal's own list.")
    if status not in STATUSES:
        raise NoteError(f"A note is active, proposed or off, not {status!r}.")
    if kind in ("field_answer", "field_skip") and _private_label(cond):
        raise NoteError("Private questions are answered only from your private answers, never by a note.")
    now = clock.iso()
    with _db(con) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            note_id = _next_id(c)
            c.execute("""INSERT INTO site_notes (id, seed_id, scope_kind, scope_key, kind, cond_json, act_json, said,
                                                 text, origin, status, private, evidence, created, updated)
                         VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (note_id, scope_kind, scope_key or "", kind, json.dumps(cond), json.dumps(act), said, text,
                       origin, status, int(_private(kind, cond)), json.dumps(evidence or []), now, now))
            c.commit()
        except BaseException:
            c.rollback()
            raise
    log.info("site note %s added (%s, %s %s)", note_id, kind, scope_kind, scope_key)
    return note_id


def propose(site: str, kind: str, cond: dict, act: dict, text: str, evidence: list) -> str:
    """Records a note Hal learned as a proposal for `site`; it is used only after the person's yes.
    The same proposal made again gathers its evidence on one row and returns that row's id."""
    scope_kind = "tenant" if site_key("https://" + site) != registrable("https://" + site) else "site"
    with _db(None) as c:
        for r in c.execute("""SELECT id, cond_json, act_json, evidence FROM site_notes
                              WHERE status = 'proposed' AND kind = ? AND scope_kind = ? AND scope_key = ?""",
                           (kind, scope_kind, site)).fetchall():
            if _loads(r["cond_json"], {}) == cond and _loads(r["act_json"], {}) == act:
                merged = [*_loads(r["evidence"], []), *[e for e in evidence if e not in _loads(r["evidence"], [])]]
                c.execute("UPDATE site_notes SET evidence = ?, updated = ? WHERE id = ?",
                          (json.dumps(merged), clock.iso(), r["id"]))
                c.commit()
                return r["id"]
        return add(scope_kind, site, kind, cond, act, text, origin="learned", status="proposed",
                   evidence=list(evidence), con=c)


def _find(c: sqlite3.Connection, note_id: str) -> Stored:
    for s in stored(c):
        if s.note.id == note_id:
            return s
    raise LookupError("That note is not in Hal's list.")


def _shadow(c: sqlite3.Connection, s: Stored) -> str:
    """The row that holds a built-in note's state, made on first need; returns its id."""
    seed_id = s.note.id.removeprefix(SEED_PREFIX)
    row = c.execute("SELECT id FROM site_notes WHERE seed_id = ?", (seed_id,)).fetchone()
    if row:
        return row["id"]
    now = clock.iso()
    rid = _next_id(c)
    n = s.note
    # text '' means "the built-in words", so an update's better wording reaches a note the person never reworded
    c.execute("""INSERT INTO site_notes (id, seed_id, scope_kind, scope_key, kind, cond_json, act_json, text, origin,
                                         status, created, updated)
                 VALUES (?, ?, ?, ?, ?, '{}', '{}', '', 'builtin', 'active', ?, ?)""",
              (rid, seed_id, n.scope_kind, n.scope_key, n.kind, now, now))
    return rid


def _row_id(c: sqlite3.Connection, s: Stored) -> str:
    return _shadow(c, s) if s.seed else s.note.id


def switch(note_id: str, on: bool, *, con: sqlite3.Connection | None = None) -> None:
    """Switches a note on or off; a built-in note switched off stays off after updates."""
    with _db(con) as c:
        s = _find(c, note_id)
        if s.status == "proposed" and on:
            raise NoteError("Press \"Use this\" to start using a note Hal suggested.")
        c.execute("UPDATE site_notes SET status = ?, updated = ? WHERE id = ?",
                  ("active" if on else "off", clock.iso(), _row_id(c, s)))
        c.commit()


def accept(note_id: str, *, con: sqlite3.Connection | None = None) -> None:
    """The person's yes to a note Hal suggested: it becomes active."""
    with _db(con) as c:
        s = _find(c, note_id)
        if s.status != "proposed":
            raise NoteError("That note is not waiting for your yes.")
        c.execute("UPDATE site_notes SET status = 'active', updated = ? WHERE id = ?", (clock.iso(), note_id))
        c.commit()


def edit(note_id: str, text: str, *, con: sqlite3.Connection | None = None) -> None:
    """Rewords a note; a built-in note keeps what Hal does and takes the person's words."""
    text = (text or "").strip()
    if not text:
        raise NoteError("Say in a few words what the note is for.")
    if len(text) > MAX_TEXT:
        raise NoteError(f"Keep a note under {MAX_TEXT} characters.")
    with _db(con) as c:
        s = _find(c, note_id)
        c.execute("UPDATE site_notes SET text = ?, updated = ? WHERE id = ?", (text, clock.iso(), _row_id(c, s)))
        c.commit()


def delete(note_id: str, *, con: sqlite3.Connection | None = None) -> None:
    """Deletes a note the person or Hal added; built-in notes can only be switched off."""
    with _db(con) as c:
        s = _find(c, note_id)
        if s.seed:
            raise PermissionError("Built-in notes cannot be deleted. Switch it off instead.")
        c.execute("DELETE FROM site_notes WHERE id = ?", (note_id,))
        c.commit()


def share_text(note_id: str, *, con: sqlite3.Connection | None = None) -> str:
    """"Hal note for <site>: <text>", for the person who looks after Hal to build in for everyone."""
    with _db(con) as c:
        s = _find(c, note_id)
    if s.private:
        raise PermissionError("This note holds one of your answers, so Hal does not share it.")
    where = {"global": "every site", "ats": f"every {ATS_NAMES.get(s.note.scope_key, s.note.scope_key)} site"}
    return f"Hal note for {where.get(s.note.scope_kind, s.note.scope_key)}: {s.note.text}"


def used(note_id: str, job_id: str, ok: bool | None = None, detail: str = "", *, url: str = "",
         step: int | None = None) -> None:
    """Counts one use of a note (a win or a failure when `ok` says so) and writes a `site_events` row.
    A note used `STALE_AFTER_USES` times that then failed `STALE_AFTER_FAILS` times in a row is marked
    "may be out of date"; a win clears the mark."""
    with _db(None) as c:
        s = _find(c, note_id)
        if not url and job_id:
            row = c.execute("SELECT url FROM jobs WHERE id = ?", (job_id,)).fetchone()
            url = row["url"] if row else ""
        c.execute("BEGIN IMMEDIATE")
        try:
            rid = _row_id(c, s)
            what = "note_failed" if ok is False else "note_used"
            c.execute("UPDATE site_notes SET uses = uses + 1, wins = wins + ?, fails = fails + ?, updated = ? "
                      "WHERE id = ?", (int(ok is True), int(ok is False), clock.iso(), rid))
            c.execute("INSERT INTO site_events (at, job_id, site, url, step, what, note_id, detail) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (clock.iso(), job_id, site_key(url) if url else None, url or None, step, what, note_id,
                       detail or ("worked" if ok else "did not work" if ok is False else "")))
            if ok is True:
                c.execute("UPDATE site_notes SET stale = 0 WHERE id = ?", (rid,))
            elif ok is False:
                c.execute("UPDATE site_notes SET stale = ? WHERE id = ?", (int(_is_stale(c, note_id)), rid))
            if url:
                c.execute("INSERT INTO sites (key, kind, ats, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET last_seen = excluded.last_seen", _site_row(url))
            c.commit()
        except BaseException:
            c.rollback()
            raise


def _is_stale(c: sqlite3.Connection, note_id: str) -> bool:
    """True when the latest uses are a run of failures long enough, after enough uses before it."""
    outcomes = [r["what"] for r in c.execute(
        "SELECT what FROM site_events WHERE note_id = ? AND what IN ('note_used', 'note_failed') ORDER BY id DESC",
        (note_id,))]
    run = next((i for i, w in enumerate(outcomes) if w != "note_failed"), len(outcomes))
    return run >= STALE_AFTER_FAILS and len(outcomes) - run >= STALE_AFTER_USES


def _site_row(url: str) -> tuple:
    key = site_key(url)
    now = clock.iso()
    return (key, "tenant" if key != registrable(url) else "site", ats_of(url), now, now)


def seen(url: str, *, account: str | None = None, con: sqlite3.Connection | None = None) -> str:
    """Records a visit to a site (and its sign-in state when known); returns the site's key."""
    if account is not None and account not in ("unknown", "none", "needed", "signed_in"):
        raise ValueError(f"unknown account state {account!r}")
    with _db(con) as c:
        c.execute("INSERT INTO sites (key, kind, ats, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET last_seen = excluded.last_seen", _site_row(url))
        if account is not None:
            c.execute("UPDATE sites SET account = ? WHERE key = ?", (account, site_key(url)))
        c.commit()
    return site_key(url)


def because(note: Note) -> str:
    """Why Hal did something, for a field's or step's trace: "because of your note from 20 Oct (acme.org)"."""
    where = scope_words(note)
    if note.origin == "builtin":
        return f"because of a built-in note for {where}"
    whose = "your note" if note.origin == "you" else "a note Hal learned"
    return f"because of {whose}{' from ' + day_words(note.created) if note.created else ''} ({where})"


def day_words(iso: str) -> str:
    """"20 Oct" from an ISO time; '' when it is not one."""
    try:
        return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}"
    except (ValueError, IndexError):
        return ""


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

"""The profile as words: the "Profile" block that document prompts get, and one line per item.

`context_block(con, limit)` lists only what the person checked or edited, best first: a fact Hal found
that nobody has checked yet, and a hidden one, never reach a prompt. `with_record(queries)` puts that block
above the record passages `record.context` finds, and is what the pack's letter, fit map and answers read
(`pack._kept`).

Design decisions, each with its reason:

- Only checked facts are used (2026-11-05): Hal reads hundreds of facts out of a person's files, and some are
  wrong (a lecturer's course as the person's own skill, a guessed degree). An application must say only what
  the person has seen and agreed to, so the Check your record page asks them first.
- A skill only course material names (`store.is_studied`) is never used: a lecture's list of topics is what
  the person studied, not what they can show. The checked-only rule already keeps it out, as a studied
  skill is one nobody checked; once the person confirms it, it is theirs and is used. How strongly each
  usable skill is shown (`skill_strengths`, `profile.strength`: shown, coursework, cv, studied) is there for
  the writer to tell a skill only studied as knowledge, never as something the person did: each skill line of
  the block says it (`strength.PROMPT_WORDS`), and `studied_skills` names the ones a draft's check holds to
  that (`writing.strengths`).
- Experiences lead with the best (`tiers`): core jobs, courses, projects, certificates and achievements
  come before supporting ones and those not sorted yet, and an archived one is never used, even when the
  person confirmed it, because they or Hal said it is not worth telling an employer. Within a tier, the
  ones whose words are closest to the job's come first, so the block holds the best when it is cut.
- Contact details (email, phone) are left out: no document a prompt writes needs them, and the CV and the
  person's settings already hold them where they belong.
- No line of the block is a bracketed "[file > heading]" label, because `pack.passages` counts those lines
  as record passages the letter used.
- A project's line names its context ("degree work", "university team project", "at work", "personal
  project"), the person's choice or else what its evidence shows (`profile.contexts`), since letters called a
  personal project degree work (2026-11-06) and a team project a personal one (2026-11-07). Another card
  names its context only when it is not its kind's own (a job done as part of a degree). A record passage
  from a project card's file says the card's context on its "(Kind: ...)" line (`passage_contexts`).
- A project card carries the other names a draft may call it by (`aliases`): the names of hidden cards
  resting on one of its project files, its repository's or folder's name and the end of its link. A letter
  placed a personal project in a degree under a hidden copy's shorter name, and the check, reading only the
  card's own name, missed it (2026-11-07). A CV names every project, so only a file of one project ties
  two cards together, and a file that lists projects (`owners.Files.listings`: it backs two or more visible
  project cards, or names several projects) ties none: it is in no card's `files`, gives no alias and no
  passage context, and a quote from it that names another card and not this one is not this card's
  (`quotes`). A dossier of every project gave a hidden sniffer's name to two other cards (2026-11-08).
- The block is cut at a whole line when it reaches `limit`, kind by kind in `KIND_ORDER`, so the basics and
  jobs are the last to go.
- The person's newest CV speaks for their CVs (`CvAuthority`): a card's tool, duty or field that only older
  CV drafts give (not the newest CV, a repository, the person's own project documents, or the person's own
  typing) is left out of the block and the cards (`for_writing`), and the profile page says "only in an older
  CV" beside it (`older_cv_only`). An older draft gave a team project tools the newest CV does not claim and
  its code never used, and every pack said the person used them (2026-11-08). The newest CV is the current CV
  of the CVs section when the person added any there (`profile.cvs`, which folding uses too), else the one
  whose own period (its note's `period`) ends latest, then the newest file (`ingest_items.mtime`); only CVs
  whose role is `own` count, and with one CV or none nothing is left out.
- Planned is not done (`planned_only`, `writing.planned`): a project's tool, or a skill, that only plans
  give (plan words, or a design document, specification or proposal saying nothing was done) is left out
  of the block, the cards' tools and the skill names, kept as the card's `planned`, and the profile page
  says "planned in <document>". A team's design document planned Docker and PostgreSQL, the app used
  SQLite, and every pack said the person used them (2026-11-08).
- Project cards with similar names (`similar`: the same first word, or one name inside the other) are each
  named with a few words of their own summary (`tag`: "Quill (offline assistant over personal notes)",
  "Quill Employment (job-search helper)") on their Profile line and in `cards` (`tag`, `similar`): drafts gave
  one card's tools and purpose to its namesake (2026-11-08).
- A course's modules and a job's duties and achievements are one line each (`entry_lines`), under the line of
  the course or job they belong to and naming it, at most `LINE_MAX` characters of them per item: one line of
  every module was one piece of evidence, so a sentence about one module could cite another's words
  (2026-11-08).
- A project's own file belongs to it (`owners.Files.owners`: a repository or project file named as a visible
  card is named, case and separators aside): evidence from it is left out of every other card's writing
  view, its tools (`elsewhere_only`, `for_writing`), quotes, files, aliases and passage context. An earlier
  project's card carried quotes from a later project's repository, and letters gave it the later one's
  database (2026-11-08). The record and the review page keep the evidence as stored.
- A card carries its own evidence quotes (`quotes`) for the paragraph that tells it (`writing.plan.Story`):
  only the person's own sources, never an older CV's, a plan's, or one naming a tool the card's writing
  fields leave out, so a quote never brings back what `for_writing` took out (2026-11-08). The sentences of the
  card's own repository or project notes join them, and only sentences about the work are kept, those with a
  result or a number first (`writing.quotes`): a card's quotes held table rows and credit lines while its
  README's test suite was missing (2026-11-08).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field

from jobhunt import db, record
from jobhunt.ingest import frontmatter
from jobhunt.profile import contexts, merge, owners, store, strength, tiers
from jobhunt.profile import cvs as section_cvs
from jobhunt.writing import planned, stage
from jobhunt.writing import quotes as writing_quotes

log = logging.getLogger(__name__)

KIND_ORDER = ("basic", "job", "education", "project", "skill", "certificate", "achievement", "link")
CONTACT_KEYS = {"email", "phone", "mobile", "telephone", "address"}
LIMIT = 5000               # below the record part's 6000 (`record.context`); a 64K-token writer has room for both
# One item's line at most: a degree with every module, or a job with every duty from five CVs, filled the
# block alone and left no room for education or skills (2026-11-05). Cut at a whole list item.
LINE_MAX = 450
QUOTE_CHARS = 160          # a skill's evidence quote, cut so it never crowds out the other facts
HEADING = "Profile (facts about the applicant from their files, each checked by them):"
USED_STATES = ("confirmed", "edited")
CARD_KINDS = ("project", "job")    # the cards whose tools and work the fit map sees in full (`cards`)
CARDS_HEADING = "Profile cards (every project and job the applicant checked, with its tools):"
CARD_SUMMARY = 200        # characters of a card's summary: enough to say what it was, short enough for all
CARD_QUOTES = 12          # a card's own quotes a paragraph about it is given (`writing.plan.STORY_FACTS`)
# A passage's "[file > heading]" line and its "(Kind: ...)" line, as `record.context` writes them.
PASSAGE_HEAD = re.compile(r"^(\[(.+?) > [^\n]*\]\n)(\(Kind: [^\n]*\)\n)?", re.M)


def _period(fields: dict) -> str:
    words = store.when(fields)
    return f" ({words})" if words else ""


def line(kind: str, fields: dict, quote: str = "", context: str = "", entries: bool = True, tag: str = "") -> str:
    """One item as a line of plain words for a prompt; a project names its `context` (`writing.stage.CONTEXTS`)
    when known, and another card when it is not its kind's own. `entries=False` leaves out a course's modules
    and a job's duties and achievements, which `entry_lines` gives one a line; a project's `tag` follows its
    name in brackets, to tell it from a card of a similar name (`similar`)."""
    if tag and kind == "project":
        fields = {**fields, "name": f"{fields.get('name', '')} ({tag})"}
    said = _plain(kind, fields, quote, stage.CONTEXT_WORDS.get(context, "") if kind == "project" else "", entries)
    if context and kind != "project" and context != contexts.BY_KIND.get(kind):
        label, _, rest = said.partition(": ")
        said = f"{label} ({stage.CONTEXT_WORDS[context]}): {rest}"
    return said


def entry_lines(kind: str, fields: dict) -> list[str]:
    """The lines of a course's modules and of a job's duties and achievements, one each and naming the course
    or job ("Module (BA History): Oral History (68)", "Job duty (Guide at Fenwick Museum): Ran tours"), at
    most `LINE_MAX` characters in all; [] for any other kind."""
    f = fields
    if kind == "education":
        course = " ".join(p for p in (f.get("qualification"), f.get("subject")) if p) or "a course"
        said = [f"Module ({course}): " + m["name"] + (f" ({m['mark']})" if m.get("mark") else "")
                for m in f.get("modules") or [] if m.get("name")]
    elif kind == "job":
        job = " at ".join(p for p in (f.get("title"), f.get("employer")) if p) or "a role"
        said = [f"Job {word} ({job}): {x}" for word, key in (("duty", "duties"), ("achievement", "achievements"))
                for x in f.get(key) or [] if str(x).strip()]
    else:
        return []
    out, size = [], 0
    for x in said:
        if size + len(x) > LINE_MAX:
            break
        out.append(x)
        size += len(x)
    return out


def skill_line(fields: dict, quote: str = "", shown: str = "") -> str:
    """A skill as a line of plain words for a prompt, saying how strongly it is `shown` (`profile.strength`), so
    a studied one is told only as knowledge."""
    level = ", ".join(p for p in (fields.get("level"), fields.get("category")) if p)
    out = f"Skill: {fields.get('name', '')}" + (f" ({level})" if level else "")
    out += f". {strength.PROMPT_WORDS[shown]}" if shown in strength.PROMPT_WORDS else ""
    return out + (f'. Shown by: "{quote[:QUOTE_CHARS]}"' if quote else "")


def _plain(kind: str, fields: dict, quote: str, project_kind: str, entries: bool = True) -> str:
    f = fields
    if kind == "job":
        where = ", ".join(p for p in (f.get("employer"), f.get("location")) if p)
        out = f"Job: {f.get('title') or 'a role'}" + (f" at {where}" if where else "") + _period(f)
        if f.get("hours") == "part_time":
            out += ", part-time"
        if not entries:
            return out
        if f.get("duties"):
            out += ". Duties: " + "; ".join(f["duties"])
        if f.get("achievements"):
            out += ". Achievements: " + "; ".join(f["achievements"])
        return out
    if kind == "education":
        course = " ".join(p for p in (f.get("qualification"), f.get("subject")) if p) or "a course"
        out = f"Education: {course}" + (f" at {f['institution']}" if f.get("institution") else "") + _period(f)
        if f.get("grade"):
            out += f", grade {f['grade']}"
        if f.get("modules") and entries:
            out += ". Modules: " + "; ".join(m["name"] + (f" ({m['mark']})" if m.get("mark") else "")
                                             for m in f["modules"])
        return out
    if kind == "skill":
        return skill_line(f, quote)
    if kind == "project":
        out = f"Project{f' ({project_kind})' if project_kind else ''}: {f.get('name', '')}" + (
            f" ({f['period']})" if f.get("period") else "")
        for label, key in (("", "summary"), ("Role", "role")):
            if f.get(key):
                out += f". {label + ': ' if label else ''}{f[key]}"
        return out + (". Tools: " + ", ".join(f["tools"]) if f.get("tools") else "")
    if kind == "certificate":
        out = f"Certificate: {f.get('name', '')}" + (f" from {f['issuer']}" if f.get("issuer") else "")
        if f.get("date"):
            out += f" ({merge.date_words(f['date'])})"
        return out + (f", grade {f['grade']}" if f.get("grade") else "")
    if kind == "achievement":
        out = f"Achievement: {f.get('title', '')}" + (f" ({merge.date_words(f['date'])})" if f.get("date") else "")
        return out + (f". {f['detail']}" if f.get("detail") else "")
    if kind == "basic":
        return f"{f.get('key', '').capitalize()}: {f.get('value', '')}"
    return f"Link: {f.get('label') or f.get('url', '')}" + (f" {f['url']}" if f.get("label") else "")


# Kinds a person has many of: shown most relevant to the job first, as the block cannot hold them all
# (89 projects filled it in a real profile, 2026-11-05). Jobs and education are ranked the same way, within
# their tier (2026-11-07).
RANKED_KINDS = ("job", "education", "project", "skill", "certificate", "achievement")


def _relevance(item: dict, wanted: set[str]) -> int:
    words = set(merge.text_key(" ".join(str(v) for v in item["fields"].values())).split())
    return len(words & wanted)


# Similar names ----------------------------------------------------------------------------------------------

STEM_MIN = 3               # a first word this short ("AI", "My") says nothing of a name's stem
TAG_WORDS = 6              # words of a card's own summary that tell it from a namesake
NOT_STEMS = {"the", "and", "for", "our", "project", "app", "tool", "website", "personal", "team", "group"}
# Where a summary's naming words end: punctuation, or a word that starts saying how, with what or for whom.
TAG_END = re.compile(r"[,.;:(]|\s(?:that|which|who|built|written|made|using|used|with|for|from|in|to|on|by)\s",
                     re.I)
LEADING_ARTICLE = re.compile(r"^(?:an?|the|my|our)\s+", re.I)


def _stem(name: str) -> str:
    """A name's first word, lower case, when it is long and distinctive enough to stand for the name."""
    first = next(iter(re.split(r"[\s_-]+", name.strip().lower())), "")
    return first if len(first) >= STEM_MIN and first not in NOT_STEMS else ""


def _inside(name: str, other: str) -> bool:
    return bool(name.strip()) and re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", other, re.I) is not None


def similar(names: list[str]) -> dict[str, list[str]]:
    """For each name, the other names it could be mixed up with: the same first word (`_stem`), or one name
    inside the other ("Quill" and "Quill Employment"); names with none are left out."""
    out: dict[str, list[str]] = {}
    for a in names:
        for b in names:
            if a.strip().lower() == b.strip().lower():
                continue
            if (_stem(a) and _stem(a) == _stem(b)) or _inside(a, b) or _inside(b, a):
                out.setdefault(a, []).append(b)
    return out


def tag(summary: str) -> str:
    """A few words of a card's summary that say what it is ("An offline assistant over personal notes, built
    with Lua" is "offline assistant over personal notes"); "" when it has no summary."""
    first = TAG_END.split(" ".join(str(summary or "").split()), 1)[0]
    words = LEADING_ARTICLE.sub("", first.strip()).split()[:TAG_WORDS]
    return " ".join(words).rstrip(".,;:")


def _tags(projects: list[dict]) -> dict[str, str]:
    """The tag of each project card whose name is like another's (`similar`), by name."""
    names = [p["fields"].get("name", "") for p in projects]
    alike = similar(names)
    return {n: tag(p["fields"].get("summary", "")) for n, p in zip(names, projects, strict=True) if n in alike}


# The newest CV ------------------------------------------------------------------------------------------------

CV_TYPE, OWN_ROLE = "cv", "own"
YEAR = re.compile(r"\b(?:19|20)\d\d\b")
OLDER_CV = "only in an older CV"
# The fields of a card a CV may give, each a text or a list (`store.KINDS`); never the name, which is the card.
CV_FIELDS = {"project": ("summary", "role", "period", "link", "tools"), "job": ("duties", "achievements")}


def _cv_key(con: sqlite3.Connection, source_rel: str) -> tuple[int, str]:
    """How new a CV is: the last year of its note's own `period`, else of its file's date, then its file's
    date (`ingest_items.mtime`)."""
    row = con.execute("SELECT mtime FROM ingest_items WHERE note = ? AND mtime IS NOT NULL ORDER BY updated DESC",
                      (source_rel,)).fetchone()
    mtime = str(row["mtime"]) if row else ""
    try:
        fm, _ = frontmatter.parse(record.file_path(source_rel).read_text(encoding="utf-8", errors="replace"))
    except (LookupError, OSError):
        fm = {}
    period = fm.get("period") or ""
    years = [int(y) for y in YEAR.findall(" ".join(period) if isinstance(period, list) else str(period))]
    year = max(years) if years else (int(mtime[:4]) if mtime[:4].isdigit() else 0)
    return year, mtime


@dataclass(frozen=True)
class CvAuthority:
    """The person's CVs as writing reads them: the newest (`newest`) speaks for them all, so a card's field
    that only older ones (`older`) give is left out."""

    newest: str = ""
    older: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, con: sqlite3.Connection) -> CvAuthority:
        """The person's own CVs in the record, the newest apart; empty when there are none. With CVs in the CVs
        section of Your record, the current one (`profile.cvs`) is the newest and every other CV is older."""
        rels = [r[0] for r in con.execute("SELECT DISTINCT source_rel FROM profile_evidence WHERE doc_type = ? "
                                          "AND role = ? AND source_rel != ''", (CV_TYPE, OWN_ROLE))]
        lead, others = section_cvs.notes(con)
        if lead:
            return cls(lead, frozenset((set(rels) | others) - {lead}))
        if not rels:
            return cls()
        ranked = sorted(rels, key=lambda rel: (*_cv_key(con, rel), rel), reverse=True)
        return cls(ranked[0], frozenset(ranked[1:]))

    def older_only(self, said: list[dict], value) -> bool:
        """True when the sources that give `value` (`said`: [{source_rel, value}]) are all older CVs: none is
        the newest CV or another document of the person's own, and at least one is there."""
        givers = [e for e in said if _gives(e["value"], value)]
        return bool(givers) and all(e["source_rel"] in self.older for e in givers)


def _key(value) -> str:
    return merge.text_key(str(value or ""))


def _gives(given, value) -> bool:
    """True when a source's value of a field (`given`, a text or a list) holds `value`."""
    if isinstance(given, list):
        return any(_key(g) == _key(value) for g in given)
    return bool(given) and _key(given) == _key(value)


def _said(con: sqlite3.Connection, item_id: int, name: str) -> list[dict]:
    """What each source of an item says of one field: [{source_rel, value}]. A source that is not the person's
    own (another's document, course material) is left out: it never speaks for a field here."""
    out = []
    for e in con.execute("SELECT source_rel, doc_type, role, said FROM profile_evidence WHERE item_id = ?",
                         (item_id,)):
        if e["role"] not in (OWN_ROLE, ""):
            continue
        try:
            fields = json.loads(e["said"] or "{}")
        except ValueError:
            log.warning("profile writing: a source of item %s holds fields that do not read as JSON", item_id)
            continue
        out.append({"source_rel": e["source_rel"], "value": fields.get(name) if isinstance(fields, dict) else None})
    return out


def older_cv_only(con: sqlite3.Connection, item: dict, cvs: CvAuthority | None = None) -> dict[str, list[str]]:
    """The values of a card's fields that only older CVs give (`CvAuthority`), {field: [value, ...]}; {} for an
    item the person added themselves, or one no older CV alone gives anything to."""
    cvs = cvs if cvs is not None else CvAuthority.of(con)
    if not cvs.older or item.get("origin") == "you":
        return {}
    out: dict[str, list[str]] = {}
    for name in CV_FIELDS.get(item["kind"], ()):
        value = item["fields"].get(name)
        values = value if isinstance(value, list) else [value] if value else []
        said = _said(con, item["id"], name)
        dropped = [str(v) for v in values if cvs.older_only(said, v)]
        if dropped:
            out[name] = dropped
    return out


# Planned, not done -----------------------------------------------------------------------------------------

# The field of each kind whose values a plan may give: a project's tools, a skill's name (the whole skill).
PLANNED_FIELDS = {"project": "tools", "skill": "name"}


def _sources(con: sqlite3.Connection, item_id: int) -> list[dict]:
    """Each source of an item: {source_rel, quote, said} with `said` the fields it gives."""
    out = []
    for e in con.execute("SELECT source_rel, quote, said FROM profile_evidence WHERE item_id = ?", (item_id,)):
        try:
            said = json.loads(e["said"] or "{}")
        except ValueError:
            log.warning("profile writing: a source of item %s holds fields that do not read as JSON", item_id)
            said = {}
        out.append({"source_rel": e["source_rel"], "quote": e["quote"] or "",
                    "said": said if isinstance(said, dict) else {}})
    return out


def _source_gives(source: dict, name: str, value: str) -> bool:
    """True when a source gives `value` for field `name`: in the fields it gives, or as a word of its quote."""
    if _gives(source["said"].get(name), value):
        return True
    word = rf"(?<![\w+#]){re.escape(value.strip())}(?![\w+#])"
    return bool(value.strip()) and re.search(word, source["quote"], re.I) is not None


def planned_only(con: sqlite3.Connection, item: dict) -> dict[str, dict[str, str]]:
    """The values of a card's field that only plans give (`writing.planned`), each with the document that plans
    it: {field: {value: document}}; {} for an item the person added themselves, or one with none."""
    name = PLANNED_FIELDS.get(item["kind"])
    if not name or item.get("origin") == "you":
        return {}
    value = item["fields"].get(name)
    values = value if isinstance(value, list) else [value] if value else []
    sources = _sources(con, item["id"])
    out: dict[str, str] = {}
    for v in (str(v) for v in values):
        givers = [s for s in sources if _source_gives(s, name, v)]
        if givers and all(planned.planned(s["quote"], s["source_rel"]) for s in givers):
            out[v] = planned.document_name(givers[0]["source_rel"])
    return {name: out} if out else {}


def elsewhere_only(con: sqlite3.Connection, item: dict, view: owners.Files | None = None) -> dict[str, list[str]]:
    """The tools of a project card that only another card's own files give (`owners.Files.belongs`), {"tools":
    [tool, ...]}; {} for a card the person added themselves, or one with none."""
    if item["kind"] != "project" or item.get("origin") == "you":
        return {}
    view = view if view is not None else owners.Files.of(con)
    sources = _sources(con, item["id"])
    out = []
    for tool in (str(t) for t in item["fields"].get("tools") or []):
        givers = [s for s in sources if _source_gives(s, "tools", tool)]
        if givers and not any(view.belongs(item["id"], s["source_rel"]) for s in givers):
            out.append(tool)
    return {"tools": out} if out else {}


def for_writing(con: sqlite3.Connection, item: dict, cvs: CvAuthority | None = None,
                view: owners.Files | None = None) -> dict:
    """A card's fields as writing uses them: without what only older CVs give (`older_cv_only`), the tools
    only a plan gives (`planned_only`) and the tools only another card's own files give (`elsewhere_only`)."""
    dropped = older_cv_only(con, item, cvs)
    for field_name, values in [*planned_only(con, item).items(), *elsewhere_only(con, item, view).items()]:
        dropped[field_name] = [*dropped.get(field_name, []), *values]
    if not dropped:
        return item["fields"]
    fields = dict(item["fields"])
    for name, values in dropped.items():
        if isinstance(fields.get(name), list):
            fields[name] = [v for v in fields[name] if str(v) not in values]
        else:
            fields.pop(name, None)
    return fields


def employers(con: sqlite3.Connection) -> list[str]:
    """The employers of the jobs the person checked, which make a project that names one work."""
    return [it["fields"]["employer"] for it in store.items(con, "job", hidden=False)
            if it["state"] in USED_STATES and it["fields"].get("employer")]


def _lines(con: sqlite3.Connection, queries: list[str] | None = None) -> list[str]:
    out = []
    wanted = set(merge.text_key(" ".join(queries or [])).split()) - QUERY_STOP
    worked_for = employers(con)
    cvs, view = CvAuthority.of(con), owners.Files.of(con)
    tags = _tags([it for it in store.items(con, "project", hidden=False)
                  if it["state"] in USED_STATES and it["tier"] != tiers.ARCHIVE])
    for kind in KIND_ORDER:
        found = store.items(con, kind, hidden=False)
        if kind == "basic":
            found = _one_location(found)
        if kind in RANKED_KINDS and wanted:
            found = sorted(found, key=lambda it: -_relevance(it, wanted))
        if kind in tiers.TIERED_KINDS:
            # stable: within a tier the most relevant, or for jobs and education the newest, comes first
            found = sorted((it for it in found if it["tier"] != tiers.ARCHIVE), key=tiers.order)
        for it in found:
            if it["state"] not in USED_STATES:
                continue
            if kind == "basic" and it["fields"].get("key", "").lower() in CONTACT_KEYS:
                continue
            if kind == "skill" and planned_only(con, it):
                continue                   # a skill only a plan names is not one the person has shown
            quote = next((e["quote"] for e in it["evidence"] if e["quote"]), "") if kind == "skill" else ""
            made = contexts.current(it, worked_for) if kind in contexts.CONTEXT_KINDS else ""
            fields = for_writing(con, it, cvs, view)
            out.append("- " + (skill_line(fields, quote, strength.of_skill(it)) if kind == "skill" else
                               line(kind, fields, quote, made, entries=False,
                                    tag=tags.get(fields.get("name", "")) if kind == "project" else "")))
            out += ["- " + x for x in entry_lines(kind, fields)]
    return out


def _one_location(basics: list[dict]) -> list[dict]:
    """The basics with one location: the person's own if they set one, else the one most files give.

    A letter needs where the person lives once; seven found places (a home town, a university, an old
    address) read as seven claims (2026-11-05).
    """
    places = [b for b in basics if b["fields"].get("key") == "location"]
    if len(places) < 2:
        return basics
    best = max(places, key=lambda b: (b["state"] in ("confirmed", "edited"), len(b["evidence"])))
    return [b for b in basics if b["fields"].get("key") != "location" or b is best]


QUERY_STOP = {"and", "or", "the", "a", "an", "of", "to", "in", "for", "with", "on", "at", "by", "is", "are", "be",
              "you", "your", "we", "our", "will", "experience", "ability", "able", "skills", "knowledge", "good",
              "strong", "excellent", "work", "working", "role"}


def context_block(con: sqlite3.Connection, limit: int = LIMIT, queries: list[str] | None = None) -> str:
    """The "Profile" block for a writing prompt: the facts the person confirmed or edited, never others.

    Experiences come best tier first and never archived (see the module notes). "" when the person has
    checked nothing yet.
    """
    checked = _lines(con, queries)
    if not checked:
        return ""
    parts = [HEADING, *checked]
    out, size = [], 0
    for part in parts:
        if len(part) > LINE_MAX:
            part = part[:LINE_MAX].rsplit("; ", 1)[0] + " ..."
        if size + len(part) + 1 > limit:
            continue                       # a later, shorter fact may still fit
        out.append(part)
        size += len(part) + 1
    if out == [HEADING]:
        return ""                          # a heading with no line under it says nothing
    return "\n".join(out)


def block(queries: list[str] | None = None) -> str:
    """The Profile block from Hal's own database, for callers that hold no connection."""
    with closing(db.connect()) as con:
        return context_block(con, queries=queries)


def passage_contexts(con: sqlite3.Connection) -> dict[str, str]:
    """The context, in a prompt's words, of each file a project card rests on: {file in the record: words}; a
    file that lists projects (`owners.Files.listings`) says none, as its passages are about many, and a
    project's own file says its owner's."""
    worked_for = employers(con)
    view = owners.Files.of(con)
    out: dict[str, str] = {}
    for it in store.items(con, "project", hidden=False):
        context = contexts.current(it, worked_for)
        for e in it["evidence"] if context else []:
            if view.belongs(it["id"], e["source_rel"]):
                out.setdefault(e["source_rel"], stage.CONTEXT_WORDS[context])
    return out


def listings(con: sqlite3.Connection | None = None) -> frozenset[str]:
    """The files that list projects (`owners.Files.listings`), Hal's own database unless `con`."""
    if con is None:
        with closing(db.connect()) as own:
            return listings(own)
    return owners.Files.of(con).listings


def with_contexts(passages: str, by_file: dict[str, str]) -> str:
    """The record's passages, each one from a project card's file saying that card's context."""
    def kind_line(m: re.Match) -> str:
        words = by_file.get(m.group(2))
        return f"{m.group(1)}(Kind: {words})\n" if words else m.group(0)

    return PASSAGE_HEAD.sub(kind_line, passages or "")


def with_record(queries: list[str]) -> str:
    """The Profile block above the record's passages for `queries`, each passage from a project card's file
    saying its context; "" when both are empty."""
    with closing(db.connect()) as con:
        by_file = passage_contexts(con)
    return "\n\n".join(p for p in (block(queries), with_contexts(record.context(queries), by_file)) if p)


# Document types that hold one project: `writing.stage` PERSONAL_TYPES and DEGREE_TYPES, written out (in
# `owners`) because `stage` is still loading when the profile package is.
PROJECT_FILES = owners.PROJECT_FILES


def aliases(item: dict, hidden: list[dict], view: owners.Files | None = None) -> list[str]:
    """The other names a project card goes by: the names of hidden project cards (`hidden`) resting on one of
    its project files or merged into it (`profile.duplicates`), the name of its repository or project file, and
    the last part of its link.

    Only a file of one project (`PROJECT_FILES`, never a listing of `view` nor another card's own file) ties
    two cards together: a CV names every project, a dossier of every project gave a hidden copy's name to two
    other cards, and a later project's repository gave its name to an earlier project (2026-11-08).
    """
    view = view or owners.Files()

    def files(it: dict) -> set[str]:
        return {e["source_rel"] for e in it["evidence"]
                if e["doc_type"] in PROJECT_FILES and view.belongs(item["id"], e["source_rel"])}

    own = files(item)
    found = [h["fields"].get("name", "") for h in hidden if own & files(h) or h.get("merged_into") == item["id"]]
    found += [owners.file_name(e["source_rel"]) for e in item["evidence"]
              if e["doc_type"] in stage.PERSONAL_TYPES and view.belongs(item["id"], e["source_rel"])]
    link = str(item["fields"].get("link") or "").rstrip("/")
    if link:
        found.append(owners.file_name(link.rsplit("/", 1)[-1]))
    name = item["fields"].get("name", "").lower()
    return [a for a in dict.fromkeys(a.strip() for a in found) if a and a.lower() != name]


def cards(con: sqlite3.Connection | None = None) -> list[dict]:
    """Every project and job card the person checked, for the fit map and the checks on drafts: {kind, name,
    tools, summary, planned, context, tier, aliases, role, files, quotes, tag, similar, star}; `star` is the
    person's showcase mark (`store.set_star`), which chooses the projects told as stories
    (`writing.plan.story_cards`); `tier` is its
    `profile.tiers` tier ("core", "supporting", "archive", "" unsorted); `planned` holds the tools only
    a plan gives (`planned_only`), left out of `tools`; `role` is the person's part (`card_role`),
    `files` the record files it rests on, `quotes` its own evidence for a paragraph about it (`quotes`), and a
    project whose name is like another's (`similar`) has those names and its `tag`.

    All of them, never ranked or cut like the Profile block: a project listing AWS was cut from the block,
    and the fit table said there was no evidence of AWS (2026-11-06). A project's `aliases` are the other
    names a draft may call it by (`aliases`). Hal's own database unless `con`.
    """
    if con is None:
        with closing(db.connect()) as own:
            return cards(own)
    worked_for = employers(con)
    hidden = [it for it in store.items(con, "project") if it["state"] == "hidden"]
    cvs, view = CvAuthority.of(con), owners.Files.of(con)
    names = [it["fields"].get("name", "") for it in store.items(con, "project", hidden=False)]
    out = []
    for kind in CARD_KINDS:
        for it in store.items(con, kind, hidden=False):
            if it["state"] not in USED_STATES:
                continue
            f, context = for_writing(con, it, cvs, view), contexts.current(it, worked_for)
            if kind == "project":
                said = stage.CONTEXT_WORDS.get(context, "")
                out.append({"kind": f"project{f' ({said})' if said else ''}", "name": f.get("name", ""),
                            "tools": list(f.get("tools") or []), "summary": f.get("summary") or "",
                            "planned": list(planned_only(con, it).get("tools", {})),
                            "context": context, "tier": it.get("tier") or "", "aliases": aliases(it, hidden, view),
                            "role": card_role(it),
                            "files": list(dict.fromkeys(e["source_rel"] for e in it["evidence"]
                                                        if e["source_rel"]
                                                        and view.belongs(it["id"], e["source_rel"]))),
                            "quotes": quotes(it, f, cvs, view, names), "tag": "", "similar": [],
                            "star": bool(it.get("star"))})
            else:
                out.append({"kind": "job", "name": " at ".join(p for p in (f.get("title"), f.get("employer")) if p),
                            "tools": [], "summary": "; ".join(f.get("duties") or []), "planned": [],
                            "context": context, "tier": it.get("tier") or "", "role": "", "files": [],
                            "quotes": quotes(it, f, cvs, view, names), "star": bool(it.get("star")),
                            "tag": "", "similar": []})
    projects = [c for c in out if c["kind"].startswith("project")]
    alike = similar([c["name"] for c in projects])
    for c in projects:
        if c["name"] in alike:
            c["tag"], c["similar"] = tag(c["summary"]), alike[c["name"]]
    return out


def _about_another(quote: str, item: dict, names: list[str]) -> bool:
    """True when a quote names another card (`names`) and not this one: from a listing, it is that card's."""
    own = str(item["fields"].get("name") or "")
    return not _inside(own, quote) and any(_inside(n, quote) for n in names if n.strip() and n != own)


def quotes(item: dict, fields: dict, cvs: CvAuthority, view: owners.Files | None = None,
           names: list[str] = ()) -> list[str]:
    """A card's own evidence quotes a paragraph about it may rest on, at most `CARD_QUOTES`: from the person's own
    sources only, never an older CV's (`CvAuthority`), never a plan (`writing.planned`), never one naming a
    tool its writing fields (`fields`, from `for_writing`) leave out, never one from another card's own file,
    and never one from a listing (`view`) that names another card (`names`) and not this one."""
    view = view or owners.Files()
    tools = item["fields"].get("tools") or []
    dropped = [str(t) for t in tools if str(t) not in {str(k) for k in fields.get("tools") or []}]
    out: list[str] = []
    for e in item["evidence"]:
        said = " ".join(str(e.get("quote") or "").split())
        if not said or e.get("role", "") not in (OWN_ROLE, "") or e["source_rel"] in cvs.older \
                or planned.planned(said, e["source_rel"]) \
                or view.owners.get(e["source_rel"], item["id"]) != item["id"] \
                or (not view.ties(e["source_rel"]) and _about_another(said, item, list(names))) \
                or any(re.search(rf"(?<![\w+#]){re.escape(t)}(?![\w+#])", said, re.I) for t in dropped):
            continue
        out.append(said)
    own_files = {e["source_rel"] for e in item["evidence"] if e["doc_type"] in PROJECT_FILES
                 and e.get("role", "") in (OWN_ROLE, "") and view.belongs(item["id"], e["source_rel"])}
    out += [s for rel in sorted(own_files) for s in _note_sentences(rel)
            if not planned.planned(s, rel)
            and not any(re.search(rf"(?<![\w+#]){re.escape(t)}(?![\w+#])", s, re.I) for t in dropped)]
    kept = [q for q in dict.fromkeys(out) if writing_quotes.about_work(q)]
    return writing_quotes.ranked(kept)[:CARD_QUOTES]


def _note_sentences(source_rel: str) -> list[str]:
    """The sentences about the work of one note of the record (`writing.quotes.sentences`); [] when it cannot
    be read."""
    try:
        _, body = frontmatter.parse(record.file_path(source_rel).read_text(encoding="utf-8", errors="replace"))
    except (LookupError, OSError):
        return []
    return writing_quotes.sentences(body)


def card_role(item: dict) -> str:
    """The person's part in a project, as its card or evidence says it: its Role field, and the words that say
    it was a team's ("team of 7"); "" when neither says. A letter wrote "I built the React frontend and Flask
    backend" of a seven-person project where the person built the statistics features (2026-11-07)."""
    f = item["fields"]
    said = [str(f.get("role") or "").strip()]
    texts = [str(f.get("summary") or ""), said[0], *(str(e.get("quote") or "") for e in item["evidence"])]
    team = next((t for t in map(stage.team_words, texts) if t), "")
    if team and team.lower() not in said[0].lower():
        said.append(team)
    return "; ".join(s for s in said if s)


def module_names(con: sqlite3.Connection | None = None) -> list[str]:
    """The names of the modules on the education facts the person checked (Hal's own database unless `con`)."""
    if con is None:
        with closing(db.connect()) as own:
            return module_names(own)
    return [m["name"] for it in store.items(con, "education", hidden=False) if it["state"] in USED_STATES
            for m in it["fields"].get("modules") or [] if m.get("name")]


def marked_modules(con: sqlite3.Connection | None = None) -> list[tuple[str, str]]:
    """The (name, mark) of each module on the education facts the person checked; mark "" when none is given
    (Hal's own database unless `con`)."""
    if con is None:
        with closing(db.connect()) as own:
            return marked_modules(own)
    return [(m["name"], str(m.get("mark") or "")) for it in store.items(con, "education", hidden=False)
            if it["state"] in USED_STATES for m in it["fields"].get("modules") or [] if m.get("name")]


def skill_names(con: sqlite3.Connection | None = None) -> list[str]:
    """The names of the skills the person checked (Hal's own database unless `con`)."""
    if con is None:
        with closing(db.connect()) as own:
            return skill_names(own)
    return [it["fields"].get("name", "") for it in store.items(con, "skill", hidden=False)
            if it["state"] in USED_STATES and it["fields"].get("name") and not planned_only(con, it)]


def skill_strengths(con: sqlite3.Connection | None = None) -> dict[str, str]:
    """How strongly each skill the writer may use is shown, by name (`profile.strength`: "shown", "coursework",
    "cv", "studied" or ""); Hal's own database unless `con`. A skill the person confirmed from lectures alone is
    "studied": theirs to mention, as knowledge."""
    if con is None:
        with closing(db.connect()) as own:
            return skill_strengths(own)
    return {it["fields"]["name"]: strength.of_skill(it) for it in store.items(con, "skill", hidden=False)
            if it["state"] in USED_STATES and it["fields"].get("name") and not planned_only(con, it)}


def studied_skills(con: sqlite3.Connection | None = None) -> list[str]:
    """The names of the skills Hal found that only course material shows (`profile.strength.STUDIED`), checked
    or not and hidden or not: a draft may say the person studied them, never that they used them
    (`writing.strengths`). A skill the person added themselves is theirs, so never one. Hal's own database
    unless `con`."""
    if con is None:
        with closing(db.connect()) as own:
            return studied_skills(own)
    return [it["fields"]["name"] for it in store.items(con, "skill")
            if it.get("origin") != "you" and it["fields"].get("name") and strength.of_skill(it) == strength.STUDIED]


def cards_block(found: list[dict]) -> str:
    """The cards as a block for the fit request, one line each with its tools; "" when there are none."""
    if not found:
        return ""
    lines = [CARDS_HEADING]
    for c in found:
        summary = c["summary"][:CARD_SUMMARY].rsplit(" ", 1)[0] if len(c["summary"]) > CARD_SUMMARY else c["summary"]
        lines.append(f"- {c['kind'].capitalize()}: {c['name']}" + (f". Tools: {', '.join(c['tools'])}" if c["tools"]
                                                                  else "") + (f". {summary}" if summary else ""))
    return "\n".join(lines)

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
  skill is one nobody checked; once the person confirms it, it is theirs and is used.
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
  two cards together.
- The block is cut at a whole line when it reaches `limit`, kind by kind in `KIND_ORDER`, so the basics and
  jobs are the last to go.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing

from jobhunt import db, record
from jobhunt.profile import contexts, merge, store, tiers
from jobhunt.writing import stage

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
# A passage's "[file > heading]" line and its "(Kind: ...)" line, as `record.context` writes them.
PASSAGE_HEAD = re.compile(r"^(\[(.+?) > [^\n]*\]\n)(\(Kind: [^\n]*\)\n)?", re.M)


def _period(fields: dict) -> str:
    words = store.when(fields)
    return f" ({words})" if words else ""


def line(kind: str, fields: dict, quote: str = "", context: str = "") -> str:
    """One item as a line of plain words for a prompt; a project names its `context` (`writing.stage.CONTEXTS`)
    when known, and another card when it is not its kind's own."""
    said = _plain(kind, fields, quote, stage.CONTEXT_WORDS.get(context, "") if kind == "project" else "")
    if context and kind != "project" and context != contexts.BY_KIND.get(kind):
        label, _, rest = said.partition(": ")
        said = f"{label} ({stage.CONTEXT_WORDS[context]}): {rest}"
    return said


def _plain(kind: str, fields: dict, quote: str, project_kind: str) -> str:
    f = fields
    if kind == "job":
        where = ", ".join(p for p in (f.get("employer"), f.get("location")) if p)
        out = f"Job: {f.get('title') or 'a role'}" + (f" at {where}" if where else "") + _period(f)
        if f.get("hours") == "part_time":
            out += ", part-time"
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
        if f.get("modules"):
            out += ". Modules: " + "; ".join(m["name"] + (f" ({m['mark']})" if m.get("mark") else "")
                                             for m in f["modules"])
        return out
    if kind == "skill":
        level = ", ".join(p for p in (f.get("level"), f.get("category")) if p)
        out = f"Skill: {f.get('name', '')}" + (f" ({level})" if level else "")
        return out + (f'. Shown by: "{quote[:QUOTE_CHARS]}"' if quote else "")
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


def employers(con: sqlite3.Connection) -> list[str]:
    """The employers of the jobs the person checked, which make a project that names one work."""
    return [it["fields"]["employer"] for it in store.items(con, "job", hidden=False)
            if it["state"] in USED_STATES and it["fields"].get("employer")]


def _lines(con: sqlite3.Connection, queries: list[str] | None = None) -> list[str]:
    out = []
    wanted = set(merge.text_key(" ".join(queries or [])).split()) - QUERY_STOP
    worked_for = employers(con)
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
            quote = next((e["quote"] for e in it["evidence"] if e["quote"]), "") if kind == "skill" else ""
            made = contexts.current(it, worked_for) if kind in contexts.CONTEXT_KINDS else ""
            out.append("- " + line(kind, it["fields"], quote, made))
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
    """The context, in a prompt's words, of each file a project card rests on: {file in the record: words}."""
    worked_for = employers(con)
    out: dict[str, str] = {}
    for it in store.items(con, "project", hidden=False):
        context = contexts.current(it, worked_for)
        for e in it["evidence"] if context else []:
            out.setdefault(e["source_rel"], stage.CONTEXT_WORDS[context])
    return out


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


# File names that name no project: a repository's note is named after its folder, but these are not.
GENERIC_FILES = {"readme", "index", "notes", "main", "untitled", "project", "code", "src", "app", "cv"}
ALIAS_MIN = 3              # a file name this short ("ui", "v2") names no project
# Document types that hold one project: `writing.stage` PERSONAL_TYPES and DEGREE_TYPES, written out because
# `stage` is still loading when the profile package is.
PROJECT_FILES = ("repository", "project", "coursework", "dissertation")
FOLDER_DEPTH = 4           # "Sources/<category>/<folder>/<file>": a file inside a project's own folder


def _file_name(source_rel: str) -> str:
    """The name a project's file goes by: its note's name, or its folder's when the note is a README inside
    one ("Sources/Code/<repository>/README.md"); "" when it names no project."""
    parts = [p for p in source_rel.replace("\\", "/").split("/") if p]
    stems = [p.rsplit(".", 1)[0] if "." in p else p for p in parts[-1:]]
    if len(parts) >= FOLDER_DEPTH:
        stems.append(parts[-2])
    return next((s for s in stems if s.lower() not in GENERIC_FILES and len(s) >= ALIAS_MIN), "")


def aliases(item: dict, hidden: list[dict]) -> list[str]:
    """The other names a project card goes by: the names of hidden project cards (`hidden`) resting on one of
    its project files, the name of its repository or project file, and the last part of its link.

    Only a file of one project (`PROJECT_FILES`) ties two cards together: a CV names every project.
    """
    def files(it: dict) -> set[str]:
        return {e["source_rel"] for e in it["evidence"] if e["doc_type"] in PROJECT_FILES}

    own = files(item)
    found = [h["fields"].get("name", "") for h in hidden if own & files(h)]
    found += [_file_name(e["source_rel"]) for e in item["evidence"] if e["doc_type"] in stage.PERSONAL_TYPES]
    link = str(item["fields"].get("link") or "").rstrip("/")
    if link:
        found.append(_file_name(link.rsplit("/", 1)[-1]))
    name = item["fields"].get("name", "").lower()
    return [a for a in dict.fromkeys(a.strip() for a in found) if a and a.lower() != name]


def cards(con: sqlite3.Connection | None = None) -> list[dict]:
    """Every project and job card the person checked, for the fit map and the checks on drafts: {kind, name,
    tools, summary, context, aliases}.

    All of them, never ranked or cut like the Profile block: a project listing AWS was cut from the block,
    and the fit table said there was no evidence of AWS (2026-11-06). A project's `aliases` are the other
    names a draft may call it by (`aliases`). Hal's own database unless `con`.
    """
    if con is None:
        with closing(db.connect()) as own:
            return cards(own)
    worked_for = employers(con)
    hidden = [it for it in store.items(con, "project") if it["state"] == "hidden"]
    out = []
    for kind in CARD_KINDS:
        for it in store.items(con, kind, hidden=False):
            if it["state"] not in USED_STATES:
                continue
            f, context = it["fields"], contexts.current(it, worked_for)
            if kind == "project":
                said = stage.CONTEXT_WORDS.get(context, "")
                out.append({"kind": f"project{f' ({said})' if said else ''}", "name": f.get("name", ""),
                            "tools": list(f.get("tools") or []), "summary": f.get("summary") or "",
                            "context": context, "aliases": aliases(it, hidden)})
            else:
                out.append({"kind": "job", "name": " at ".join(p for p in (f.get("title"), f.get("employer")) if p),
                            "tools": [], "summary": "; ".join(f.get("duties") or []), "context": context})
    return out


def module_names(con: sqlite3.Connection | None = None) -> list[str]:
    """The names of the modules on the education facts the person checked (Hal's own database unless `con`)."""
    if con is None:
        with closing(db.connect()) as own:
            return module_names(own)
    return [m["name"] for it in store.items(con, "education", hidden=False) if it["state"] in USED_STATES
            for m in it["fields"].get("modules") or [] if m.get("name")]


def skill_names(con: sqlite3.Connection | None = None) -> list[str]:
    """The names of the skills the person checked (Hal's own database unless `con`)."""
    if con is None:
        with closing(db.connect()) as own:
            return skill_names(own)
    return [it["fields"].get("name", "") for it in store.items(con, "skill", hidden=False)
            if it["state"] in USED_STATES and it["fields"].get("name")]


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

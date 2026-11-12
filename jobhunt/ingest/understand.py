"""What a document is and what it says about the person: by the model for rich documents, by rules for light.

- Rich documents get one `ingest_doc` prompt (role checker). The reply is cleaned in code: categories and
  doc types from fixed lists, tags lowercase-hyphenated and topped up to `TAGS_MIN` from the text. The AI's
  answer on whose the document is stays in `said`: the engine judges the role against the person's names
  and keeps only the facts that role may give (`roles`), as it alone holds the database those names need.
- Every fact carries a verbatim quote, checked here against the document's own text after evening out
  spaces, case and typographic quotes. A fact whose quote is not found is kept, marked "Check this".
- Light documents (notes, slides, files of a course, email, pictures) and any document read while the AI is
  unavailable get tags from their path, headings, frequent words and years, with no model at all.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from pathlib import PurePosixPath

from jobhunt import record
from jobhunt.ai.base import PrivateLeak
from jobhunt.ingest import convert, rank

log = logging.getLogger(__name__)

PURPOSE = "ingest_doc"
TAGS_MIN, TAGS_MAX = 12, 30
TAG_FOLDERS = 2              # a note's own folder and the one above it name its subject
PROMPT_CHARS = 60_000        # about 15K tokens: a CV whole, a long report's first chapters, inside a 64K context
TIMEOUT_S = 300
# Waits before asking again when the AI itself is down mid-ingest (a gateway restart, a model loading); a
# long ingest otherwise read every document of those minutes by rules alone (2026-11-05).
# Only once the AI has answered in this process: with no AI set up, or none reachable from the start, a
# document goes straight to rules instead of waiting through every retry.
RETRY_WAITS_S = (30, 120, 300)
_answered = False
QUOTE_MIN = 3                # characters; a shorter quote ("BA") would be found in almost any text
QUOTE_MAX_WORDS = 40         # the prompt asks for 25; a longer quote is a paragraph pasted back
FREQUENT_TERMS = 8
TAG_CHARS = 40
SUMMARY_CHARS = 1200
CHECK_THIS = "Check this: these words are not in the document"
CATEGORIES = ("CVs and letters", "Education", "Work", "Certificates", "Projects", "Code", "Writing", "Study notes",
              "Slides", "Coursework", "Creative work", "Email", "Media", "Other")
DOC_TYPES = ("cv", "transcript", "letter", "personal_statement", "certificate", "award", "reference",
             "job_description", "contract", "report", "coursework", "dissertation", "project", "repository", "essay",
             "writing", "notes", "slides", "email", "artwork", "other", "course", "image", "media", "code")
ENTITY_KINDS = ("organisations", "places", "people", "projects", "courses", "skills")
NAMED_KINDS = ("organisations", "places")  # named in the document itself; skills and projects may be described
FACT_KINDS = ("job", "education", "skill", "project", "achievement", "certificate", "basic", "link")
# A fact's organisation must be named in the document or its path, like an entity's: a module report naming
# no university gave a degree from "Trinity College Dublin" (2026-11-06).
ORG_FIELDS = ("employer", "institution", "issuer")
YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*#*$", re.M)
# Typographic marks a converter or the model may use in place of plain ones; matched by code point.
FOLD = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-", 0x00A0: " ", 0x2026: "..."}


def tag(text: str) -> str:
    """`text` as a tag: lowercase ASCII words joined by hyphens, "" when nothing is left."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:TAG_CHARS].strip("-")


def _fold(text: str) -> str:
    """Text evened out for comparing a quote: plain marks, lower case, one space between words."""
    return " ".join(str(text).translate(FOLD).lower().split())


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"\w+", _fold(text))) + " "


def quote_found(quote: str, text: str) -> bool:
    """True when `quote`'s words are in `text` in the same order, ignoring case, spacing and punctuation.

    Only the words count: a CV's "#### Bartender, The Old George Inn" with "**November 2024 - January 2025**"
    on the next line was quoted as one line, and every job of a Markdown CV came out "Check this"
    (2026-11-05).
    """
    q = _fold(quote).strip(" .,;:\"'")
    if len(q) < QUOTE_MIN or len(q.split()) > QUOTE_MAX_WORDS:
        return False
    words = _words(q)
    return words.strip() != "" and words in _words(text)


def light_tags(rel: str, text: str, rule: rank.Rule) -> list[str]:
    """Tags without a model: the kind, the folders, headings, frequent words and years of a document."""
    out = [tag(rule.doc_type), tag(rule.category)]
    # Only the nearest folders: the ones above say where the files were dumped ("Downloads", "OneDrive"),
    # which tagged every note in a dump alike (2026-11-05).
    out += [tag(part) for part in PurePosixPath(rel).parent.parts[-TAG_FOLDERS:]]
    out += [tag(h) for h in HEADING.findall(text)[:FREQUENT_TERMS]]
    counts = Counter(w for w in re.findall(r"[a-z][a-z-]{3,}", text.lower()) if w not in record.STOP)
    out += [w for w, _ in counts.most_common(FREQUENT_TERMS)]
    out += sorted({f"year-{y}" for y in YEAR.findall(text)})[:FREQUENT_TERMS]
    return _tags(out)


def _tags(found) -> list[str]:
    seen = []
    for t in found:
        t = tag(t)
        if t and t not in seen:
            seen.append(t)
    return seen[:TAGS_MAX]


def _period(text: str) -> str:
    years = sorted(set(YEAR.findall(text)))
    if not years:
        return ""
    return years[0] if len(years) == 1 else f"{years[0]}..{years[-1]}"


def _title(rel: str, text: str) -> str:
    found = HEADING.search(text)
    return found.group(1).strip() if found else PurePosixPath(rel).stem


def _note_answered() -> None:
    global _answered
    _answered = True


def light(rel: str, text: str, rule: rank.Rule) -> dict:
    """A document's description without a model: no facts, and no answer on whose it is (`said` None)."""
    # the lines naming who saved the file (`convert.author_lines`) are no summary of what it says
    first = next((p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()
                  and not p.startswith(("#", convert.AUTHOR_LINE, convert.SAVED_LINE))), "")
    return {"doc_type": rule.doc_type, "category": rule.category, "title": _title(rel, text),
            "period": _period(text), "summary": first[:SUMMARY_CHARS], "tags": light_tags(rel, text, rule),
            "entities": {k: [] for k in ENTITY_KINDS}, "said": None, "author": "", "role": "unsure",
            "about_owner": "unsure", "sensitive": "", "facts": [], "by": "rules"}


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        s = " ".join(str(v.get("name", "") if isinstance(v, dict) else v).split())
        if s and s not in out:
            out.append(s)
    return out


def clean(reply: dict, rel: str, text: str, rule: rank.Rule) -> dict:
    """The model's reply held to the shapes the vault and profile take, with each quote checked."""
    base = light(rel, text, rule)
    if not isinstance(reply, dict) or not reply:
        return base
    doc_type = str(reply.get("doc_type") or "").strip().lower()
    category = str(reply.get("category") or "").strip()
    entities = reply.get("entities") if isinstance(reply.get("entities"), dict) else {}
    out = {"doc_type": doc_type if doc_type in DOC_TYPES else rule.doc_type,
           "category": category if category in CATEGORIES else rule.category,
           "title": " ".join(str(reply.get("title") or "").split()) or base["title"],
           "period": " ".join(str(reply.get("period") or "").split()) or base["period"],
           "summary": " ".join(str(reply.get("summary") or "").split())[:SUMMARY_CHARS] or base["summary"],
           "entities": {k: _strings(entities.get(k)) for k in ENTITY_KINDS},
           "said": reply.get("role", reply.get("about_owner", "")), "role": "unsure", "about_owner": "unsure",
           "sensitive": " ".join(str(reply.get("sensitive") or "").split()),
           "author": " ".join(str(reply.get("author") or "").split()), "by": "model"}
    # People are named in a note only when the document names them; a role the model made up stays a role.
    out["entities"]["people"] = [p for p in out["entities"]["people"] if quote_found(p, text) or " " not in p]
    # An organisation or place gets a note and a tag only when the document or its path names it: a module
    # report naming no university came back from "Trinity College Dublin" (2026-11-05).
    named = f"{rel}\n{text}"
    guessed = {tag(n) for k in NAMED_KINDS for n in out["entities"][k] if not quote_found(n, named)}
    for k in NAMED_KINDS:
        out["entities"][k] = [n for n in out["entities"][k] if tag(n) not in guessed]
    tags = [t for t in _tags(_strings(reply.get("tags"))) if t not in guessed]
    if len(tags) < TAGS_MIN:
        tags = _tags(tags + [tag(e) for k in ("skills", "organisations", "places") for e in out["entities"][k]]
                     + base["tags"])
    out["tags"] = tags
    out["facts"] = facts(reply.get("facts"), text, rel)
    return out


BASIC_KEYS = ("name", "email", "phone", "location")


def shaped(kind: str, fields: dict) -> list[dict]:
    """A model's fact in the profile's shape: only fields the profile keeps, a basics fact one per key.

    The model wrote skills with an "evidence" field and basics as {name, email, phone, location}, and the
    profile refused every one of them, so a whole ingest found no skills at all (2026-11-05).
    """
    from jobhunt.profile import store

    if kind == "basic" and "key" not in fields:
        return [{"key": k, "value": fields[k]} for k in BASIC_KEYS if str(fields.get(k) or "").strip()]
    known = set(store.KINDS[kind]) | ({"period"} if kind in ("job", "education") else set())
    kept = {k: v for k, v in fields.items() if k in known and v not in (None, "", [])}
    return [kept] if kept else []


def facts(raw, text: str, rel: str = "") -> list[dict]:
    """The model's facts with their quotes checked: each {kind, fields, field, quote, verified}.

    An employer, institution or issuer the document and its path (`rel`) never name is left out.
    """
    named = f"{rel}\n{text}"
    out = []
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict) or f.get("kind") not in FACT_KINDS or not isinstance(f.get("fields"), dict):
            continue
        quote = " ".join(str(f.get("quote") or "").split())
        for fields in shaped(f["kind"], f["fields"]):
            fields = {k: v for k, v in fields.items()
                      if k not in ORG_FIELDS or quote_found(str(v), named)}
            out.append({"kind": f["kind"], "fields": fields, "field": str(f.get("field") or ""), "quote": quote,
                        "verified": quote_found(quote, text)})
    return out


def owner() -> str:
    """The person's name from their settings; "" when they have not given it."""
    from jobhunt.core import settings

    return str(settings.get("you.name") or "").strip()


def ask(rel: str, text: str, rule: rank.Rule, *, role: str = "checker") -> dict:
    """A rich document described by the model, or by rules when the AI is unavailable or says nothing.

    Never called with empty text: the caller marks such a file "no text" first, as a model given no text
    invents a document and an author.
    """
    from jobhunt import ai
    from jobhunt.ai import prompts

    if not text.strip():
        raise ValueError("a document with no text is never sent to the AI")
    name = owner()
    system = prompts.text(PURPOSE, name=name or "the person whose files these are")
    user = f"File: {rel}\n\nText:\n{text[:PROMPT_CHARS]}"
    for wait in (*RETRY_WAITS_S, None):
        try:
            reply = ai.chat_json(system, user, role=role, timeout=TIMEOUT_S, purpose=PURPOSE, priority=2)
        except ai.LLMUnavailable as e:
            # This request failing while the AI answers (too long, refused) is not worth waiting for.
            if wait is None or not _answered or e.kind == "local_only" or ai.ready(priority=2) is None:
                log.info("read %s by rules: %s", rel, e)
                return light(rel, text, rule)
            log.info("the AI is not answering (%s); asking about %s again in %d s", e, rel, wait)
            time.sleep(wait)
            continue
        except PrivateLeak:
            # The guard stopped the request before anything was sent; the file is still noted, by rules, and
            # said to hold a private answer. Raising would stop the whole ingest at this file on every carry-on.
            return {**light(rel, text, rule), "private": True}
        _note_answered()
        return clean(reply, rel, text, rule)
    raise AssertionError("unreachable: the last try returns")

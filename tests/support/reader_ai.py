"""The made-up AI that answers the record's readers in tests, put in place when `support` is imported.

The readers of Your record read with the AI only (`sections.pipeline`), so a test that adds a CV, a piece of
coursework, lecture material or a project needs an answer. This one gives the CV's facts as the CV studio's
rules read them (`cv_reply`), and an empty answer to the other readers, so a test of the pipeline gets the
facts its made-up CV states without writing the reply by hand. A test that needs other answers puts its own
`fake_ai` in place. Any other request is told the AI is not set up (kind `not_installed`), so a read that
waits for the AI ends at once instead of waiting for it to come back.
"""
from __future__ import annotations

import json
import re

from jobhunt import ai
from jobhunt.ai.base import LLMUnavailable

EMPTY = "{}"
READER_PURPOSES = ("coursework_read", "lecture_topics", "lecture_note", "project_read")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EMPHASIS = re.compile(r"\*\*|__")
BULLET = "•"            # a bullet a converted Word file keeps at a line's start
JOB_SECTIONS = ("experience", "volunteering", "research", "teaching")
PROJECT_SECTIONS = ("projects", "portfolio")
SKILL_SECTIONS = ("skills", "languages")
CONTACT_BASICS = (("name", "name"), ("email", "email"), ("phone", "phone"), ("city", "location"))


def _lines(text: str) -> list:
    """The CV's lines as the CV studio's rules take them; the first heading (the name) is plain text."""
    from jobhunt.cvstudio import parse
    from jobhunt.ingest import convert

    out, first = [], True
    for n, raw in enumerate(text.splitlines()):
        if raw.startswith((convert.AUTHOR_LINE, convert.SAVED_LINE)):
            continue
        style = ""
        if m := MD_HEADING.match(raw):
            raw = m.group(2)
            style = "" if first else "heading"
        raw = EMPHASIS.sub("", re.sub(r" {3,}", "\t", raw.rstrip()))
        body, bullet = parse._bulleted(raw)
        if body:
            first = False
            out.append(parse.Line(body, bullet=bullet, style=style, index=n))
    return out


def _quote(text: str, *parts: str) -> str:
    """The CV's own line holding the first of `parts` found in it, as written; else the parts joined."""
    for part in (p for p in parts if p and p.strip()):
        for line in text.splitlines():
            if part.lower() in line.lower():
                return EMPHASIS.sub("", line.lstrip("#-*" + BULLET + " \t")).strip()
    return ", ".join(p for p in parts if p)


def _education(entry) -> dict:
    from jobhunt.cvstudio import parse

    split = parse.DEGREE_SPLIT.match(entry.heading)
    return {"qualification": split.group("degree") if split else entry.heading,
            "subject": split.group("subject") if split else "", "institution": entry.org, "start": entry.start,
            "end": entry.end, "grade": entry.grade,
            "modules": [{"name": m.name, "mark": m.mark} for m in entry.modules]}


def _entry_facts(kind: str, entry) -> list[tuple[str, dict, str]]:
    """(kind, fields, field the quote names) for one entry of a CV section of `kind`."""
    lines = [b.text for b in entry.bullets] or ([entry.text] if entry.text else [])
    if kind in JOB_SECTIONS:
        return [("job", {"title": entry.heading, "employer": entry.org, "location": entry.location,
                         "start": entry.start, "end": entry.end, "duties": lines}, "title")]
    if kind == "education":
        return [("education", _education(entry), "qualification")]
    if kind in PROJECT_SECTIONS:
        summary = entry.text or (lines[0] if lines else "") or entry.meta
        return [("project", {"name": entry.heading, "summary": summary, "link": entry.link}, "name")]
    if kind in SKILL_SECTIONS:
        return [("skill", {"name": item, "category": entry.heading}, "name") for item in entry.items]
    if kind == "certifications":
        return [("certificate", {"name": entry.heading, "date": entry.start}, "name")]
    if kind == "achievements":
        return [("achievement", {"title": entry.heading, "date": entry.start}, "title")]
    return []


def _site(url: str) -> str:
    from urllib.parse import urlsplit

    host = urlsplit(url if "://" in url else f"https://{url}").hostname or ""
    return host.removeprefix("www.") or url


def cv_reply(req) -> str:
    """The `record_cv` answer for the CV in `req`: its facts as the CV studio's rules read them."""
    from jobhunt.cvstudio import parse

    text = req.user.split("\n\nText:\n", 1)[-1]
    doc = parse.read_rules(_lines(text)).doc
    facts = []
    contact = doc.contact_snapshot
    for key, basic in CONTACT_BASICS:
        if value := str(contact.get(key) or "").strip():
            facts.append({"kind": "basic", "fields": {"key": basic, "value": value}, "field": "value",
                          "quote": value})
    for url in contact.get("links") or []:
        facts.append({"kind": "link", "fields": {"label": _site(url), "url": url}, "field": "url", "quote": url})
    for sec in doc.sections:
        for entry in sec.entries:
            for kind, fields, field in _entry_facts(sec.kind, entry):
                kept = {k: v for k, v in fields.items() if v not in (None, "", [])}
                if kept.get(field) or (kind == "education" and kept):
                    said = kept.get(field) or next(iter(kept.values()))
                    facts.append({"kind": kind, "fields": kept, "field": field,
                                  "quote": _quote(text, str(said), entry.org)})
    return json.dumps({"facts": facts})


def install() -> None:
    """Gives the tests' AI its answers for the readers, and `not_installed` for anything else."""
    backend = ai._test_backend()
    backend.default = LLMUnavailable(ai.NOT_IN_TESTS, kind="not_installed")
    backend.replies.update({"record_cv": cv_reply, **dict.fromkeys(READER_PURPOSES, EMPTY)})

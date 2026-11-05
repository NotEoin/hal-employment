"""Whose a document is, which decides what Hal may learn from it.

Every document read into the record gets one role:

- `own`: the person's own work or papers: a CV, a letter, coursework, a report, a project, a repository.
  It can give any fact, each with its quote; contact details and links only from a CV or a letter
  (`CONTACT_DOCS`), as a report's title page or a team's README names other people's emails too.
- `taught`: course material, whoever wrote it: slides, handouts, a module page, a lab sheet, lecture notes.
  It shows what the person studied, so it gives skills and nothing else. A lecturer's email on a slide was
  filed as the person's, and a lecture's examples as their projects (2026-11-05).
- `others`: someone else's document: another person's CV, a downloaded paper, a classmate's poster, a model
  from a sharing site. It is not filed in the record at all and gives nothing.
- `unsure`: read by rules alone, or the AI could not tell. It is filed and gives nothing.

The AI says which role it thinks (`ingest_doc`'s "role"). A document it calls the person's own must name
them (`identity`): their name, another name they go by (`you.other_names`, such as a username or student
number), a username from their own links, or their email. A model asked "is this theirs?" says yes too
often, and other people's dissertations gave the person projects they never did (2026-11-05). A
repository's text names who made its commits (`convert.repo_text`), so the person's own code counts.
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlparse

OWN, TAUGHT, OTHERS, UNSURE = "own", "taught", "others", "unsure"
ROLES = (OWN, TAUGHT, OTHERS, UNSURE)
# The facts each role may give. Taught material shows what was studied: the skills, never jobs or contacts.
FACTS_BY_ROLE = {OWN: ("job", "education", "skill", "project", "achievement", "certificate", "basic", "link"),
                 TAUGHT: ("skill",), OTHERS: (), UNSURE: ()}
CONTACT_KINDS = ("basic", "link")
CONTACT_DOCS = ("cv", "letter", "personal_statement")
# Course material by its kind when no AI has read it: a deck of slides or a course's own page.
TAUGHT_TYPES = ("slides", "course")
# The old yes/no answer, kept in notes for anything that reads it.
ABOUT_OWNER = {OWN: "true", OTHERS: "false", TAUGHT: "false", UNSURE: "unsure"}
SAID = {"own": OWN, "mine": OWN, "theirs": OWN, "true": OWN, "yes": OWN, "taught": TAUGHT, "course": TAUGHT,
        "course_material": TAUGHT, "others": OTHERS, "other": OTHERS, "someone_else": OTHERS, "false": OTHERS,
        "no": OTHERS}
NAME_PART_MIN = 2          # "Al" is a name part; a lone initial is not
OTHER_NAME_MIN = 4         # a username or student number shorter than this matches ordinary words
HANDLE_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "linkedin.com", "huggingface.co", "kaggle.com"}
NOT_HANDLES = {"in", "u", "users", "user", "profile"}


def said_role(said) -> str:
    """The AI's answer as a role; `unsure` for anything else."""
    return SAID.get(str(said).strip().lower().replace(" ", "_").replace("'", ""), UNSURE)


def _handle(url: str) -> str:
    """The username in a profile link ("https://github.com/alexdoe" gives "alexdoe"); "" when none."""
    parsed = urlparse(url if "//" in url else f"https://{url}")
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in HANDLE_HOSTS:
        return ""
    parts = [p for p in parsed.path.split("/") if p and p.lower() not in NOT_HANDLES]
    return parts[0] if parts else ""


def identity(owner: str, other_names: str = "", con: sqlite3.Connection | None = None) -> list[str]:
    """The words that name the person in a document: their name's parts, the other names they gave, and the
    usernames and email addresses their own CVs and letters list in the profile."""
    words = [w for w in re.findall(r"[^\W\d_]+", owner or "") if len(w) >= NAME_PART_MIN]
    words += [n.strip() for n in re.split(r"[,;\n]", other_names or "") if len(n.strip()) >= OTHER_NAME_MIN]
    if con is not None:
        rows = con.execute("""SELECT DISTINCT i.kind, i.fields FROM profile_items i JOIN profile_evidence e
                              ON e.item_id = i.id WHERE i.kind IN ('link', 'basic') AND i.state != 'hidden'
                              AND e.doc_type IN ('cv', 'letter')""").fetchall()
        for r in rows:
            fields = json.loads(r["fields"])
            if r["kind"] == "link" and (handle := _handle(str(fields.get("url") or ""))):
                words.append(handle)
            elif fields.get("key") == "email" and "@" in str(fields.get("value") or ""):
                words.append(str(fields["value"]).strip())
    seen: list[str] = []
    for w in words:
        if w.lower() not in (s.lower() for s in seen):
            seen.append(w)
    return seen


def names_person(text: str, words: list[str]) -> bool:
    """True when `text` holds one of `words` as a whole word, ignoring case."""
    folded = text.lower()
    return any(re.search(rf"(?<![\w.@-]){re.escape(w.lower())}(?![\w@-])", folded) for w in words)


def decide(said, *, doc_type: str, text: str, words: list[str], in_course: bool = False) -> str:
    """The document's role from the AI's answer (`said`, or None when no AI read it) and the rules.

    Taught and someone else's are taken as the AI says; the person's own only when the text names them.
    Without the AI, slides and the files of a course are taught, and the rest unsure.
    """
    if said is None:
        return TAUGHT if doc_type in TAUGHT_TYPES or in_course else UNSURE
    role = said_role(said)
    if role == OWN and not (words and names_person(text, words)):
        return UNSURE
    return role


def allowed(role: str, kind: str, doc_type: str) -> bool:
    """True when a document of `role` and `doc_type` may give a fact of `kind`."""
    if kind not in FACTS_BY_ROLE.get(role, ()):
        return False
    return kind not in CONTACT_KINDS or doc_type in CONTACT_DOCS

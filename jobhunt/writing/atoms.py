"""List-shaped lines taken apart: one fact per entry, each keeping its list's heading.

`split(line)` turns a line that is a heading and a list ("Second year modules (overall 73.3): Ferry Studies 86,
River Trade 79", "Languages: Lua, SQL, Python", "Achievements: Dean's list 2024, Hackathon winner 2025") into
one line per entry, the heading kept on each ("Second year modules (overall 73.3): Ferry Studies 86"); any
other line comes back as it is. `module_entries(line)` gives the (module, line) pairs of a module list.

Design decisions, each with its reason:

- A list is one fact per entry, never one fact: a CV line of every module and its mark was one evidence item,
  so "I achieved 86 in <module>, where I contributed to a team project" cited a line that held "Team Project"
  as another module's name, and every check passed it (2026-11-08). Taken apart, a sentence citing a module
  and its mark can only cite that module.
- The heading stays on each entry: "86" means nothing without "Second year modules", and a skills list's
  label ("Languages") says what the entries are.
- Only a short heading with no comma or sentence in it is a list's heading, so "Project, Ferry planner (Lua,
  SQL): wrote ..." and "Barista, Old Mill Inn (2024): ..." stay whole: their commas are part of the work's
  description. An entry longer than `ENTRY_WORDS` words or holding a sentence's end makes the line prose.
- A comma list needs `LIST_MIN` entries, as "Volunteering: Guide at a heritage centre, 2024 to 2026" is a fact
  and its date; two entries are enough under a heading that names modules or skills, or when every entry ends
  in a mark.
- Entries split on semicolons when the line has any, else on commas, never inside brackets, so "Python
  (advanced, five years)" is one entry.
"""
from __future__ import annotations

import re

HEAD_MAX = 60              # characters of a list's heading: "Second year modules (overall 73.3)" is 34
ENTRY_WORDS = 10           # words of one entry: a long module name and its mark is about 8
LIST_MIN = 3               # entries a comma list needs under any heading (see the module notes)
HEADED = re.compile(rf"^(?P<head>[^:,;\n]{{1,{HEAD_MAX}}}?):\s+(?P<rest>\S.*)$")
# Headings whose lists are always lists: modules, and the labels of a skills list (`writing.plan.SKILLS_LABEL`).
LIST_HEAD = re.compile(r"\b(modules?|skills?|languages?|tools?|technologies|frameworks?|libraries|software|"
                       r"achievements?|awards?|interests?|certificat\w*)\b", re.I)
MARK = re.compile(r"\s+(?P<mark>\d{1,3}(?:\.\d+)?\s*%?|\(\d{1,3}(?:\.\d+)?\s*%?\))$")
MODULES = re.compile(r"\bmodules?\b", re.I)
SENTENCE_END = re.compile(r"[.!?]\s")
JOINING = re.compile(r"^(?:and|or|&)\s+", re.I)


def _entries(rest: str) -> list[str]:
    """The entries of a list's body: split on semicolons when there are any, else on commas, outside
    brackets."""
    sep = ";" if ";" in rest else ","
    out, depth, start = [], 0, 0
    for n, ch in enumerate(rest):
        depth += (ch in "([") - (ch in ")]")
        if ch == sep and depth <= 0:
            out.append(rest[start:n])
            start = n + 1
    out.append(rest[start:])
    return [JOINING.sub("", e.strip()).rstrip(".").strip() for e in out]


def _parts(line: str) -> tuple[str, list[str]] | None:
    """A list-shaped line's (heading, entries), or None for any other line."""
    found = HEADED.match(line.strip())
    if not found or SENTENCE_END.search(found.group("head") + " "):
        return None
    head, entries = found.group("head").strip(), _entries(found.group("rest"))
    if len(entries) < 2 or not all(entries) or any(len(e.split()) > ENTRY_WORDS or SENTENCE_END.search(e)
                                                   for e in entries):
        return None
    if len(entries) < LIST_MIN and not (LIST_HEAD.search(head) or all(MARK.search(e) for e in entries)):
        return None
    return head, entries


def split(line: str) -> list[str]:
    """One line per entry of a list-shaped line, each "<heading>: <entry>"; [line] for any other line."""
    parts = _parts(line)
    return [f"{parts[0]}: {e}" for e in parts[1]] if parts else [line]


def module_entries(line: str) -> list[tuple[str, str]]:
    """The modules of a module list ("Second year modules: Ferry Studies 86, ..."), each as (its name without
    its mark, its own line with the heading); [] for any other line."""
    parts = _parts(line)
    if not parts or not MODULES.search(parts[0]):
        return []
    return [(MARK.sub("", e).strip(), f"{parts[0]}: {e}") for e in parts[1]]

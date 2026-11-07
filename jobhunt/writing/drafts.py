"""Checks on a finished draft that need no AI: a reply cut off, a greeting to the wrong person, a tool put on
the wrong project.

Each is a rule a pattern can see, so the same draft always gets the same answer (as in `writing.check`).

Design decisions, each with its reason:

- A reply is cut off when its last line is a sentence (more than `SIGNOFF_WORDS` words) that ends without
  punctuation: a letter ended "distingu" when the model ran out of room (2026-11-07). A short last line is a
  sign-off ("Best wishes," or a name), not a cut. The AI layer does not say why a reply stopped, so the
  text is the only sign.
- A greeting names the applicant when its first line starts with a greeting word and holds their first,
  full or preferred name: one message opened "Hi <the applicant's first name>," (2026-11-07).
- A tool is on the wrong project when a sentence names one project card and a tool some checked card or
  skill lists, but that project's own tools, name and summary never name it: a letter said a Lua project
  "required C++ and multithreading" (2026-11-07). A sentence naming two projects is left alone, as the tool
  may be the other's.
"""
from __future__ import annotations

import re

from jobhunt.writing import check

SIGNOFF_WORDS = 4          # a last line this short is a sign-off or a name, not a sentence
ENDS = re.compile(r"[.!?:;)\]\"'\u2019]\s*$")
FULL_STOP = re.compile(r"[.!?][\"')\u2019]?(?=\s|$)")
GREETING = re.compile(r"^\s*(dear|hi|hello|hey|good (morning|afternoon|evening))\b[^\n]*", re.I)
SHORT_TOOL = 2             # a tool this short ("R", "Go") is matched only with its own capitals


def cut_off(text: str) -> bool:
    """True when a draft stops mid-sentence: its last line is a sentence with no closing punctuation."""
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    if not lines:
        return False
    last = lines[-1]
    return len(last.split()) > SIGNOFF_WORDS and not ENDS.search(last)


def last_full_sentence(text: str) -> str:
    """The draft up to the end of its last whole sentence; the draft as it is when it has none."""
    ends = list(FULL_STOP.finditer(text or ""))
    return text[:ends[-1].end()].rstrip() if ends else text


def greets_applicant(message: str, names: list[str]) -> bool:
    """True when the message's greeting line holds one of the applicant's `names`."""
    m = GREETING.match(message or "")
    return bool(m) and any(n and re.search(rf"\b{re.escape(n)}\b", m.group(0), re.I) for n in names)


def regreet(message: str, greeting: str) -> str:
    """The message with its greeting line replaced by `greeting`, or `greeting` above it when it has none."""
    m = GREETING.match(message or "")
    return greeting + (message[m.end():] if m else "\n\n" + message.lstrip())


def names_tool(text: str, tool: str) -> bool:
    """True when `text` names `tool` as a word ("C++" and "C#" included); short tools only in their capitals."""
    tool = tool.strip()
    flags = 0 if len(tool) <= SHORT_TOOL else re.I
    return bool(tool) and re.search(rf"(?<![\w+#]){re.escape(tool)}(?![\w+#])", text or "", flags) is not None


def misplaced_tools(draft: str, cards: list[dict], known: list[str]) -> list[str]:
    """Sentences that give a project card a tool from `known` that the card's own facts never name, each
    with the tool and the project: "The Lua tide gauge needed C++. (C++ is not among Tide gauge's tools)"."""
    projects = [c for c in cards if c["kind"].startswith("project") and c["name"].strip()]
    out = []
    for sentence in check.sentences(draft):
        named = [c for c in projects if names_tool(sentence, c["name"])]
        if len(named) != 1:
            continue
        card = named[0]
        own = " ".join([card["name"], card["summary"], *card["tools"]])
        for tool in dict.fromkeys(known):
            if names_tool(sentence, tool) and not names_tool(own, tool):
                out.append(f"{sentence} ({tool} is not among {card['name']}'s tools)")
                break
    return out

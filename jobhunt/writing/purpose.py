"""What a tool was for: a sentence that says a tool served a purpose ("Docker to package the application",
"FastAPI for the backend", "the parser in C++") must find that purpose in the facts that name the tool.

`unbacked(sentence, tools, facts, about, known)` finds, with no AI, each tool of `tools` the sentence gives a
purpose that no fact naming the tool holds, nor what the work is (`about`: its name and summary).

Design decisions, each with its reason:

- A local model reframed a fact into a claim the record never makes: "used Docker to package the application
  ... consistent deployment" when the record's Docker was an agent sandbox, and frontend work called backend
  work (2026-11-08). A tool's facts say what it did; a purpose that none of them says is the model's.
- The purpose is read in two forms: a tool followed by "to" or "for" (`AFTER`, with at most `GAP_WORDS` words
  between, "Docker containers to ...") and up to `PURPOSE_WORDS` words after it, and "the <thing> in, with or
  using <tool>" (`BEFORE`, "the backend in FastAPI"). Other wordings are left to the AI check of the
  paragraph, as a pattern reading every clause would flag plain sentences.
- Only its content words count (`purpose_words`): little words, general words any work says (`GENERAL`:
  "build", "application", "tool"), tool names and the work's own name are left out, so "Lua to build Quill"
  states no purpose and "moved from Flask to FastAPI" is a change of tools, not a purpose. A word counts when
  its first `STEM` letters match ("packaging" meets "package"), and the purpose is backed when at least
  `PURPOSE_SHARE` of its words are in the facts naming the tool or in what the work is: "Python to build a
  pathfinding autopilot" restates the work, which is no new claim.
"""
from __future__ import annotations

import re

from jobhunt.writing import drafts

GAP_WORDS = 2              # words between a tool and its "to"/"for": "Docker containers to", "a React app for"
PURPOSE_WORDS = 5          # words after "to"/"for" read as the purpose; past that a sentence has moved on
STEM = 6                   # letters compared, as `writing.taught.STEM`: "packaging" and "package" share "packag"
PURPOSE_SHARE = 0.5        # share of the purpose's words the tool's facts must hold
# Words that end a purpose: what follows is how or why, not what the tool was for.
STOP = frozenset("""with using by so which that while because when where as via through and or but then""".split())
NEXT = frozenset({"to", "for", "in", "on"})    # inside a purpose, these start the next one: "to X for Y"
# Words any piece of work says of a tool's use, so they state no purpose of their own.
GENERAL = frozenset("""build built building make made making create created creating write wrote written writing
develop developed developing implement implemented implementing use used using run ran running work worked help
helped handle handled app apps application applications project projects tool tools code program programs software
system systems thing things part parts it them this that""".split())
AFTER = re.compile(rf"^(?P<gap>(?:\s+[\w'-]+){{0,{GAP_WORDS}}}?)\s+(?:to|for)\s+(?P<what>[^,.;:!?()]+)", re.I)
SAID = 'says {tool} was for "{purpose}", which no fact naming {tool} says; say only what its facts say it did'


def _tool(tool: str) -> re.Pattern:
    """A tool as a word, short tools only in their own capitals (as `drafts.names_tool`)."""
    flags = 0 if len(tool) <= drafts.SHORT_TOOL else re.I
    return re.compile(rf"(?<![\w+#]){re.escape(tool)}(?![\w+#])", flags)


def _stems(text: str) -> set[str]:
    return {w[:STEM] for w in re.findall(r"[a-z0-9+#]+", (text or "").lower())}


def purpose_words(phrase: str, known: list[str], about: str = "") -> list[str]:
    """The content words of a purpose: little words, `GENERAL` words, tool names and the work's own name's
    words left out."""
    left = {w.lower() for t in known for w in re.findall(r"[\w+#]+", t)}
    left |= {w.lower() for w in re.findall(r"[\w+#]+", about.splitlines()[0] if about else "")}
    words = re.findall(r"[a-z0-9+#]+", phrase.lower())
    return [w for w in words if w not in drafts.LITTLE_WORDS and w not in GENERAL and w not in left and len(w) > 1]


def _purposes(sentence: str, tool: str) -> list[str]:
    """The phrases a sentence gives as what `tool` was for, in the forms of the module notes."""
    out = []
    for m in _tool(tool).finditer(sentence):
        after = AFTER.match(sentence[m.end():])
        if after and not set(after.group("gap").lower().split()) & (STOP | drafts.LITTLE_WORDS - {"a", "an", "the"}):
            words = after.group("what").split()
            cut = next((k for k, w in enumerate(words) if w.lower() in STOP | NEXT), len(words))
            out.append(" ".join(words[:min(cut, PURPOSE_WORDS)]))
    before = re.compile(r"\b(?:the|a|an|my|its|our|their)\s+(?P<what>(?:[\w-]+\s+){1,3}?)(?:in|with|using)\s+"
                        + _tool(tool).pattern, _tool(tool).flags)
    out += [m.group("what").strip() for m in before.finditer(sentence)]
    return out


def unbacked(sentence: str, tools: list[str], facts: list[str], about: str, known: list[str]) -> list[str]:
    """What is wrong with each tool of `tools` that `sentence` gives a purpose its facts do not (see the module
    notes): `facts` are the story's facts, `about` its name and what it is, `known` every tool name."""
    out = []
    for tool in dict.fromkeys(t for t in tools if t.strip()):
        held = _stems("\n".join([about, *(f for f in facts if drafts.names_tool(f, tool))]))
        for phrase in _purposes(sentence, tool):
            wanted = {w[:STEM] for w in purpose_words(phrase, known, about)}
            if wanted and len(wanted & held) < PURPOSE_SHARE * len(wanted):
                out.append(SAID.format(tool=tool, purpose=phrase))
                break
    return out

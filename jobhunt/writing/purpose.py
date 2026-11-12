"""What a tool was for: a sentence that says a tool served a purpose ("Docker to package the application",
"FastAPI for the backend", "the parser in C++") must find that purpose in the work's facts.

`unbacked(sentence, tools, facts, about, known)` finds, with no AI, each tool of `tools` the sentence gives a
purpose that the work's facts and what it is (`about`: its name and summary) do not hold; a part of a system
(`LAYER`: backend, deployment, database ...) must be in a fact naming the tool itself.

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
  `PURPOSE_SHARE` of its words are in the work's facts or in what the work is: "Python to build a pathfinding
  autopilot" restates the work, which is no new claim.
- Ordinary purpose words may come from any of the work's facts, as a project's facts rarely name its language
  in the sentence that says what it does: holding them to the facts naming the tool flagged "the daemon in
  Python" and "aircrack-ng to count devices" and dropped true paragraphs (2026-11-08). A part of a system
  (`LAYER`) is the claim that went wrong, frontend work called backend and a sandbox called deployment, so
  those words need a fact naming the tool.
- A purpose the facts give to another tool by name fails, citing that tool (`doer`, `CROSSED`): a voice
  assistant was drafted "GPT-SoVITS to transcribe utterances" when faster-whisper did the transcribing and
  GPT-SoVITS the voice, and an Electron app "the simulation logic in Python" when its Python was a sidecar
  (2026-11-08). The mapping is read from the facts with no AI: a fact holding `PURPOSE_SHARE` of the
  purpose's words that names another known tool, while no fact naming the sentence's tool holds them.
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
# Parts of a system: said of a tool, they claim where it sits, so a fact naming the tool must hold them.
LAYER = frozenset("""backend frontend front-end back-end server database api apis ui interface deploy deployed
deploying deployment deployments production infrastructure container containers containerise containerize
package packaging packaged hosting hosted cloud""".split())
AFTER = re.compile(rf"^(?P<gap>(?:\s+[\w'-]+){{0,{GAP_WORDS}}}?)\s+(?:to|for)\s+(?P<what>[^,.;:!?()]+)", re.I)
SAID = 'says {tool} was for "{purpose}", which no fact naming {tool} says; say only what its facts say it did'
CROSSED = 'says {tool} did "{purpose}", which its facts say {other} did; name {other}, or say only what {tool} did'


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


def _holds(fact: str, wanted: set[str]) -> bool:
    """True when a fact holds at least `PURPOSE_SHARE` of a purpose's word stems."""
    return bool(wanted) and len(wanted & _stems(fact)) >= PURPOSE_SHARE * len(wanted)


def doer(tool: str, wanted: set[str], facts: list[str], known: list[str]) -> str:
    """Another tool the facts name as doing the purpose `wanted` (its word stems), when no fact naming `tool`
    holds that purpose; "" else (see the module notes)."""
    if any(_holds(f, wanted) for f in facts if drafts.names_tool(f, tool)):
        return ""
    for fact in (f for f in facts if _holds(f, wanted)):
        other = next((t for t in known if t.strip() and t != tool and drafts.names_tool(fact, t)
                      and not drafts.names_tool(t, tool)), "")
        if other:
            return other
    return ""


def unbacked(sentence: str, tools: list[str], facts: list[str], about: str, known: list[str]) -> list[str]:
    """What is wrong with each tool of `tools` that `sentence` gives a purpose its facts do not, or give to
    another tool by name (see the module notes): `facts` are the story's facts, `about` its name and what it
    is, `known` every tool name."""
    out = []
    anywhere = _stems("\n".join([about, *facts]))
    for tool in dict.fromkeys(t for t in tools if t.strip()):
        own = _stems("\n".join([about, *(f for f in facts if drafts.names_tool(f, tool))]))
        for phrase in _purposes(sentence, tool):
            words = purpose_words(phrase, known, about)
            wanted = {w[:STEM] for w in words}
            layers = {w[:STEM] for w in words if w in LAYER}
            other = doer(tool, wanted, facts, known)
            if other:
                out.append(CROSSED.format(tool=tool, purpose=phrase, other=other))
                break
            if (wanted and len(wanted & anywhere) < PURPOSE_SHARE * len(wanted)) or layers - own:
                out.append(SAID.format(tool=tool, purpose=phrase))
                break
    return out

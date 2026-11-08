"""Skills only studied are told as knowledge: the check, with no AI, that a draft never says the person used one.

A skill whose every source is course material (`profile.strength.STUDIED`: lecture slides, seminar notes) is
what the person studied, not what they did (sections plan, 2026-11-09). `studied_claims(text, studied)` finds
each sentence that names such a skill and says it was used, applied, run or done ("I used thematic analysis",
"using Haskell", "I carried out titration") with no words that make it knowledge ("I studied", "covered in my
degree", "learned about"); a paragraph (`writing.paragraphs`) or a whole draft (`pack.unsupported_claims`) that
holds one is drafted again once with the reason, as for any other claim the facts do not support.

Design decisions, each with its reason:

- The skill names are the record's own, so the check works for any field: a research method, a lab
  technique, a kind of writing or a language counts as a tool does.
- A sentence whose own story's facts name the skill is never flagged (`own`): a paragraph about coursework that
  used the method may say so, as that piece of work shows it.
- Knowledge words in the same sentence clear it, so "I studied regression and used it in my dissertation"
  is left to the paragraph's other checks rather than read as a claim of use with no source.
"""
from __future__ import annotations

import re

from jobhunt.writing import check as writing_check
from jobhunt.writing import drafts

# Words that say something was done with a skill: "I used", "we applied", "using", "carried out", "built with".
USE = re.compile(r"\b(?:I|we)\s+(?:have\s+|had\s+|also\s+|then\s+)?(?:used|applied|ran|run|performed|"
                 r"carried out|conducted|built|wrote|developed|programmed|coded|analysed|analyzed|designed|"
                 r"implemented|employed|practised|practiced|did|made|produced|tested|measured|mapped|modelled|"
                 r"modeled)\b|\b(?:using|applying|with the help of|by means of)\b|\b(?:built|written|made|"
                 r"done|carried out|analysed|analyzed) (?:with|in|using)\b", re.I)
# Words that make a sentence about a skill a statement of knowledge, not of use.
KNOWLEDGE = re.compile(r"\b(?:stud(?:ied|y|ying)|covered|learn(?:ed|t|ing)|taught|lectures?|seminars?|modules?|"
                       r"knowledge of|introduced to|read about|familiar with|understanding of|theory of)\b", re.I)
STUDIED_SAID = "{skill} was only studied in your course; say you studied it, never that you used it"


def claims_use(sentence: str, skill: str) -> bool:
    """True when a sentence names `skill` and says it was used or done, with no words that make it knowledge."""
    return bool(skill.strip()) and drafts.names_tool(sentence, skill) and bool(USE.search(sentence)) \
        and not KNOWLEDGE.search(sentence)


def problems(sentence: str, studied: list[str] | tuple[str, ...], own: str = "") -> list[str]:
    """The reasons one sentence fails: each studied-only skill (`studied`) it says was used that its own story's
    facts (`own`) never name."""
    return [STUDIED_SAID.format(skill=s) for s in dict.fromkeys(studied)
            if claims_use(sentence, s) and not (own and drafts.names_tool(own, s))]


def studied_claims(text: str, studied: list[str] | tuple[str, ...]) -> list[str]:
    """Each sentence of a draft that says a studied-only skill was used, with the reason in brackets:
    "I used palaeography to date the letters. (palaeography was only studied ...)"."""
    out = []
    for sentence in writing_check.sentences(text):
        found = problems(sentence, studied)
        if found:
            out.append(f"{sentence} ({found[0]})")
    return out

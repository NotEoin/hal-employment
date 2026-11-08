"""Planned is not done: words and documents that say what a project was going to use, not what it used.

`said(text)` is True for words of a plan ("will use", "we plan to", "proposed", "to be deployed"), and
`plan_document(source)` for a file that is a plan by its name (a design document, a specification, a
proposal). `planned(text, source)` puts them together for one piece of evidence: plan words, or a plan
document's words that say nothing was done. `profile.writing.planned_only` leaves a card's tool out of writing
when every source giving it is planned, and `writing.plan.Item.planned` marks an evidence item, which
`writing.grounded` never lets support a sentence saying the person used something.

Design decisions, each with its reason:

- A team project card listed Docker and PostgreSQL because the team's design document planned them; the app
  that was built used SQLite, and every pack said the person used them (2026-11-08). Words decide first: plan
  words make any source planned.
- A plan document (by its file name, `PLAN_FILE`) is planned unless its words say something was done ("we
  built", "was deployed", `DONE`): a design document's "The app uses Docker" describes the design.
- The checks are words, with no AI, so the same evidence always gets the same answer.
"""
from __future__ import annotations

import re

# Words of a plan: what was going to be used or done. "planned" alone is not one, as "I planned rotas" is work
# done; "was planned", "the proposed stack" and "planned to use" are.
PLAN_WORDS = re.compile(
    r"\b(?:will (?:be )?(?:use[ds]?|built|build|run|deployed|deploy|hosted|host|stored|store|adopt(?:ed)?|"
    r"integrated?|implement(?:ed)?|containeri[sz]ed?)|(?:we|I|the team) (?:plan|intend|propose|aim|hope)s? to|"
    r"(?:is|are|was|were|been) (?:planned|proposed|intended|going to)|(?:planned|proposed|intended) to|"
    r"(?:the|a|an|our|its|their) (?:planned|proposed|intended)|to be (?:deployed|used|built|implemented|hosted|"
    r"containeri[sz]ed|added)|would use|plans? to (?:use|build|deploy|host)|future work|(?:shall|should) use)\b|"
    r"\((?:planned|proposed)\)", re.I)
# Words that say something was done: a plan document saying them reports, rather than plans.
DONE = re.compile(r"\b(?:we|I|the team) (?:used|built|deployed|implemented|wrote|ran|shipped|delivered|switched)\b|"
                  r"\b(?:was|were) (?:used|built|deployed|implemented|written|shipped|delivered)\b", re.I)
# A file that is a plan by its name: a design document, a specification, a proposal, a project plan.
# "requirements" alone is not one: a requirements.txt lists what the code does use.
PLAN_FILE = re.compile(r"(?:^|[\s_./-])(?:design(?:[\s_-]?(?:doc|document|spec))?|spec|specs|specification|"
                       r"proposal|project[\s_-]?plan|plan|requirements?[\s_-]?(?:doc|document|spec|"
                       r"specification))(?:$|[\s_.-])", re.I)


def said(text: str) -> bool:
    """True when `text` speaks of a plan ("will use", "we plan to", "proposed")."""
    return bool(PLAN_WORDS.search(text or ""))


def plan_document(source: str) -> bool:
    """True when a file is a plan by its name (a design document, a specification, a proposal)."""
    name = (source or "").replace("\\", "/").rsplit("/", 1)[-1]
    return bool(PLAN_FILE.search(name.rsplit(".", 1)[0] if "." in name else name))


def planned(text: str, source: str = "") -> bool:
    """True when one piece of evidence (its words, from the file `source`) says what was planned, not done:
    plan words, or a plan document's words that say nothing was done."""
    return said(text) or (plan_document(source) and not DONE.search(text or ""))


def document_name(source: str) -> str:
    """The name a file is shown by: its last part without the extension."""
    name = (source or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name

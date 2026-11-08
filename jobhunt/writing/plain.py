"""Plain first-person writing: what a draft must never say about how it was made, and the last tidy before saving.

`problems(sentence, names, labels)` finds, with no AI, a sentence that opens with the advert's requirement
("For the requirement to ...") or frames itself as one ("The role asks for ..."), points at the CV instead
of saying the thing ("My CV lists ..."), speaks of the person in the third person ("The applicant developed
...", "<name> has ...") or pastes the wording of a setting (`labels`, from `settings_labels`);
`writing.grounded` lists them with a sentence's other problems, so each such sentence is rewritten once.
`tidy(text, cards)` is the draft as it is saved: straight quotes and apostrophes, no stray space at the start
of a line, and each project named as its card spells it.

Design decisions, each with its reason:

- A local model wrote lead-ins copied from the advert ("For the requirement to demonstrate strong coding
  skills, ...", "Regarding the need for ..."), "My CV lists C++ as key skills", "The applicant developed
  this assistant", a settings choice pasted as it stands ("as a British or Irish citizen" for a person who
  is a UK and Irish citizen), a stray leading space, curly apostrophes and a project name capitalised two
  ways (15 graded packs, 2026-11-07). The wording problems need a rewrite; the rest have one right form, so
  they are fixed in place, as spelling is (`writing.spelling`).
- Framing a sentence as the advert's requirement and its evidence anywhere in it ("The role asks for ...",
  "as requested in the advert", "provides strong evidence for the requirement of ...") is the commonest
  thing that held graded packs at 2 of 5 for readability: the fit table's framing leaking into prose (30
  packs, 2026-11-08). `REQUIREMENT_TALK` finds it, and `writing.readability` lists it for the polish pass.
- The settings' own wording reaches a request only as the person's plain values (`cv.status_line`); a
  label still found in a draft is a problem. `you.work_status` is how forms should word the right to work,
  so it is a label too unless it is already a sentence in the first person.
- A project name is respelt only when it is distinctive (two words or more, or a capital, an acronym or a
  digit inside a word): "Tide gauge" for "tide Gauge", but never "website" for a card named "Website".
"""
from __future__ import annotations

import re

LEAD_IN = re.compile(
    r"^(?:(?:for|regarding|concerning|with regard to|in regard to|in response to|addressing|on|about) the "
    r"(?:requirements?|need|criteri(?:on|a)|request|ask|point)\b|as (?:required|requested|specified|stated|"
    r"outlined)\b|to (?:meet|address|demonstrate|show|satisfy) the (?:requirements?|need|criteri(?:on|a))\b)", re.I)
# The fit table's requirement-to-evidence framing anywhere in a sentence (`LEAD_IN` only reads its start).
REQUIREMENT_TALK = re.compile(
    r"\b(?:the|this) (?:role|position|job|advert|vacancy|post|job description) (?:asks|calls|looks) for\b|"
    r"\b(?:the|this) (?:role|position|job|advert|vacancy|post|job description) (?:requires|wants|specifies|"
    r"lists|stresses|emphasi[sz]es)\b|"
    r"\bas (?:requested|required|specified|stated|outlined|mentioned|listed) (?:in|by) (?:the|your) (?:advert|role|"
    r"job description|job posting|posting|vacancy|person specification)\b|"
    r"\b(?:evidence|proof|example) (?:for|of) (?:the|your|this) requirements?\b|"
    r"\bthe requirements? (?:of|for|to)\b|"
    r"\b(?:meets?|meeting|satisf(?:y|ies|ying)|address(?:es|ing)?|fulfils?|fulfills?|fulfilling) (?:the|your|this|"
    r"each|every) (?:requirements?|criteri(?:on|a))\b", re.I)
CV_SAYS = re.compile(r"\bmy (?:CV|resume)\s+(?:lists|shows|states|mentions|says|notes|includes|highlights)\b|"
                     r"\b(?:as )?(?:listed|stated|shown|mentioned|noted|detailed|outlined) (?:in|on) my "
                     r"(?:CV|resume)\b", re.I)
THIRD_PERSON = re.compile(r"\bthe (?:applicant|candidate)(?:'s\b|\s+(?:has|is|was|had|will|\w+ed|built|led|made|"
                          r"wrote|ran|took|won|can)\b)|^(?:the|this) (?:applicant|candidate)\b", re.I)
SAID_BY = r"(?:'s\b|\s+(?:has|is|was|had|will|can|\w+ed|built|led|made|wrote|ran|took|won)\b)"
NAMED_SELF = re.compile(r"\b(?:my name is|I am|I'm|name:)\s*$", re.I)
CURLY = {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
         "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"', "\u2033": '"'}
CURLY_RX = re.compile("[" + "".join(CURLY) + "]")
LEADING = re.compile(r"(?m)^[ \t\u00a0]+(?![-*] )(?=\S)")
DISTINCT = re.compile(r"\s|[a-z][A-Z]|[A-Z]{2}|\d")
LEAD_IN_SAID = "opens with the advert's requirement; say what you did in your own words"
REQUIREMENT_SAID = "frames itself as the advert's requirement and its evidence; say what you did instead"
CV_SAID = "points at your CV instead of saying it"
THIRD_SAID = "speaks of you in the third person; write as I"
LABEL_SAID = 'pastes the settings wording "{label}"; say it in your own words'


def settings_labels() -> list[str]:
    """The wording of the settings a draft must never paste: the right-to-work choices' labels, and how forms
    should word it (`you.work_status`) unless that is a sentence in the first person."""
    from jobhunt.core import settings

    labels = [label for _, label in settings.setting("you.right_to_work").choices or ()]
    status = str(settings.get("you.work_status") or "").strip()
    if status and not re.match(r"I\b", status):
        labels.append(status)
    return labels


def _third_person(sentence: str, names: list[str]) -> bool:
    if THIRD_PERSON.search(sentence):
        return True
    for name in (n for n in names if n and n.strip()):
        for m in re.finditer(rf"\b{re.escape(name.strip())}{SAID_BY}", sentence):
            if not NAMED_SELF.search(sentence[:m.start()]):
                return True
    return False


def problems(sentence: str, names: list[str] = (), labels: list[str] = ()) -> list[str]:
    """What a sentence says about how it was made rather than about the person (see the module notes); []
    when nothing. `names` are the person's names, `labels` the settings wording (`settings_labels`)."""
    text = sentence.strip()
    out = []
    if LEAD_IN.search(text):
        out.append(LEAD_IN_SAID)
    elif REQUIREMENT_TALK.search(text):
        out.append(REQUIREMENT_SAID)
    if CV_SAYS.search(text):
        out.append(CV_SAID)
    if _third_person(text, list(names)):
        out.append(THIRD_SAID)
    out += [LABEL_SAID.format(label=label) for label in labels
            if label.strip() and re.search(rf"(?<!\w){re.escape(label.strip())}(?!\w)", text, re.I)]
    return out


def _distinct(name: str) -> bool:
    return bool(DISTINCT.search(name.strip()))


def tidy(text: str, cards: list[dict] = ()) -> str:
    """A draft as it is saved: curly quotes and apostrophes straight, no space before a line's first word (a
    bullet's indent aside), and each distinctive project name spelt as its card spells it."""
    out = LEADING.sub("", CURLY_RX.sub(lambda m: CURLY[m.group(0)], text or ""))
    for card in cards:
        name = str(card.get("name") or "").strip()
        if name and _distinct(name):
            out = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", name, out, flags=re.I)
    return out

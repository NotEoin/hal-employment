"""Plain first-person writing: what a draft must never say about how it was made, and the last tidy before saving.

`problems(sentence, names, labels)` finds, with no AI, a sentence that opens with the advert's requirement
("For the requirement to ..."), ties the work to the job with a stock phrase ("aligns with", `STOCK`),
points at the CV instead of saying the thing ("My CV lists ..."), speaks of
the person in the third person ("The applicant developed ...", "<name> has ...") or pastes the wording of a
setting (`labels`, from `settings_labels`); `writing.paragraphs` lists them with a sentence's other problems,
so its paragraph is drafted again. `tidy(text, cards)` is the draft as it is saved: no stray evidence id,
straight quotes and apostrophes, no stray space at the start of a line, and each project named as its card
spells it.

Design decisions, each with its reason:

- A local model wrote lead-ins copied from the advert ("For the requirement to demonstrate strong coding
  skills, ...", "Regarding the need for ..."), "My CV lists C++ as key skills", "The applicant developed
  this assistant", a settings choice pasted as it stands ("as a British or Irish citizen" for a person who
  is a UK and Irish citizen), a stray leading space, curly apostrophes and a project name capitalised two
  ways (15 graded packs, 2026-11-07). The wording problems need a rewrite; the rest have one right form, so
  they are fixed in place, as spelling is (`writing.spelling`).
- Lead-ins mapping the advert kept coming in every graded pack ("The advert requires ...", "The role asks for
  ...", "Regarding <X>, ...", "In terms of <X>, ...", "As required, ...", 2026-11-08). They are never cut out
  with no AI: cutting them left garbled text ("applying for the ... role in London, where I achieved a top
  score of 96", 2026-11-08), so a paragraph holding one is drafted again instead.
- Openings led in with the advert as the employer's: "Your requirement to write and improve SQL queries ...",
  "Your work on ... Reinforcement Learning Agents builds on ..." (2026-11-08), so "your requirement", "your
  need" and "your work on" open a lead-in too, and "your requirement for" or "your need to" is stock anywhere.
- The same move mid-sentence is a problem too (`STOCK`): "satisfies the requirement for", "directly supports
  the requirement for", "addresses the need for", "aligns with", "resonates with", "the role requires",
  "matters for the role", "demonstrates my ability to", and every closing sentence of a prototype's paragraphs
  was "This experience matters for the role because it demonstrates my ability to ..." (2026-11-08). The
  sentence that ties a piece of work to the job should name what in the job it helps with instead. Letter
  openings said the role "matches my experience" or "fits my recent work" (2026-11-08), so "matches my",
  "fits my", "suits my", "in line with my" and their kin are stock too. Every paragraph of a graded letter
  closed by calling the work "useful for the technical challenges at <employer>" or "applicable to the
  engineering challenges" (2026-11-12): work said to be useful, applicable or valuable for the employer's
  challenges, environment or demands is stock too.
- What the person is looking for ("I am seeking a role in backend, systems or applied AI", `GOAL`) is a
  problem: a letter is for one role, and the search settings and a CV's summary leaked into one (2026-11-08).
- The settings' own wording reaches a request only as the person's plain values (`cv.status_line`); a
  label still found in a draft is a problem. `you.work_status` is how forms should word the right to work,
  so it is a label too unless it is already a sentence in the first person.
- An evidence id such as "[E4,M]" is taken out as the draft is saved: one reached a form answer when drafts
  marked their evidence (2026-11-08). No draft is asked for ids now, so this only catches a model's habit.
- A project name is respelt only when it is distinctive (two words or more, or a capital, an acronym or a
  digit inside a word): "Tide gauge" for "tide Gauge", but never "website" for a card named "Website".
"""
from __future__ import annotations

import re

LEAD_IN = re.compile(
    r"^\W*(?:(?:for|regarding|concerning|with regard to|in regard to|in response to|addressing|on|about) the "
    r"(?:requirements?|need|criteri(?:on|a)|request|ask|point)\b|as (?:required|requested|specified|stated|"
    r"outlined)\b|to (?:meet|address|demonstrate|show|satisfy) the (?:requirements?|need|criteri(?:on|a))\b|"
    r"(?:the|this|your) (?:advert(?:isement)?|role|job(?: description)?|position|post|vacancy|person "
    r"specification|listing) (?:requires|asks for|calls for|specifies|states|mentions|lists|seeks|wants|"
    r"is looking for|emphasi[sz]es|highlights)\b|you (?:require|ask for|are looking for|are seeking)\b|"
    r"your (?:requirements?|needs?|work on)\b|"
    r"(?:regarding|concerning|in terms of|with (?:regard|respect) to|in relation to|as for|when it comes to)"
    r"\b[^,.;]{1,80},)", re.I)
# A stray evidence id and the space before it: "[E4]", "[E2,E5]", "[M]", "[E4,M]".
STRAY_ID = re.compile(r"[ \t]*\[\s*(?:M|E\d+)(?:\s*[,;]\s*(?:M|E?\d+))*\s*\]", re.I)
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
# Stock ways of tying a piece of work to the job, in any part of a sentence (see the module notes).
STOCK = re.compile(
    r"\b(?:(?:the|this) (?:role|job|position|post) requires|"
    r"(?:satisf|meet|address|fulfil)\w* (?:the|your|this|its|their) (?:[\w-]+ )?(?:requirements?|needs?|"
    r"criteri(?:on|a))|your (?:requirements?|needs?) (?:for|to|of)|"
    r"aligns? (?:directly |closely |well |strongly |perfectly )?with|resonates? with|"
    r"directly (?:supports?|relevant|applicable|relates)|matters? (?:for|to) (?:the|this|your) (?:role|job|"
    r"position|post|team)|demonstrates? my (?:ability|capacity|capability|skills?)|(?:this|that|the) experience "
    r"matters|(?:match(?:es)?|fits?|suits?|reflects?|mirrors?|complements?|(?:is |are )?in (?:line|keeping) with|"
    r"ties? in with|aligns? (?:directly |closely |well )?with|speaks? to|connects? (?:directly |well )?(?:to|with))"
    r" (?:well |closely |directly |perfectly )?(?:with )?my|"
    r"(?:is|are|was|will be|would be|could be) (?:\w+ ){0,2}(?:useful|applicable|relevant|valuable|helpful|"
    r"transferable|beneficial) (?:to|for|in|at) (?:[\w'-]+[ -]){0,4}?(?:challenges?|environment|demands|setting)|"
    r"meet the challenges of (?:the|this|your) (?:role|job|position|post|team))\b", re.I)
# What the person is looking for, as a CV's summary or the search settings say it: "seeking a role in ...".
GOAL = re.compile(r"\b(?:seeking|looking for|searching for|hoping to find|aiming for)\s+(?:an?\s+|my\s+)?"
                  r"(?:[\w-]+\s+){0,3}?(?:roles?|positions?|jobs?|posts?|opportunit(?:y|ies)|placements?|"
                  r"internships?|careers?)\b|\bcareer (?:goal|objective)s?\b", re.I)
LEAD_IN_SAID = "opens with the advert's requirement; say what you did in your own words"
GOAL_SAID = "says what roles you are looking for; this letter is for this role alone"
STOCK_SAID = ('ties the work to the job with a stock phrase ("{phrase}"); name the thing in the job it helps '
              "with, in your own words")
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
    stock = STOCK.search(text)
    if LEAD_IN.search(text):
        out.append(LEAD_IN_SAID)
    elif stock:
        out.append(STOCK_SAID.format(phrase=stock.group(0)))
    if CV_SAYS.search(text):
        out.append(CV_SAID)
    if GOAL.search(text):
        out.append(GOAL_SAID)
    if _third_person(text, list(names)):
        out.append(THIRD_SAID)
    out += [LABEL_SAID.format(label=label) for label in labels
            if label.strip() and re.search(rf"(?<!\w){re.escape(label.strip())}(?!\w)", text, re.I)]
    return out


def _distinct(name: str) -> bool:
    return bool(DISTINCT.search(name.strip()))


def tidy(text: str, cards: list[dict] = ()) -> str:
    """A draft as it is saved: no stray evidence id (`STRAY_ID`), curly quotes and apostrophes straight, no space
    before a line's first word (a bullet's indent aside), and each distinctive project name spelt as its card
    spells it."""
    out = LEADING.sub("", CURLY_RX.sub(lambda m: CURLY[m.group(0)], STRAY_ID.sub("", text or "")))
    for card in cards:
        name = str(card.get("name") or "").strip()
        if name and _distinct(name):
            out = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", name, out, flags=re.I)
    return out

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
- A sentence misframes a project when it names one project card and words of a degree or of the person's own
  time that disagree with that card's context (`profile.contexts`): a letter said "My Computer Science
  degree includes building a local RAG assistant" of a personal project (2026-11-07). The words are narrow
  on purpose ("module" only as course work, "personal" only as personal work or project), as "a Lua module"
  or "personal data" say nothing about where the work was done. A sentence that also says words agreeing
  with the card ("alongside my degree, in my own time") is left alone.
- A sentence names a project card by its name, by another name the card carries (`aliases`: the names of
  hidden cards resting on the same file, and its repository's or folder's name), or by a distinctive word
  of its name used as a name ("LIDAR" in "the LIDAR Autonavigation project" for "LIDAR Autonomous
  Navigation"): a letter placed a personal project in a degree under a shorter name and was not caught
  (2026-11-07). A distinctive word is an acronym or a word with inner capitals that no other card's names
  hold, and it counts only before another capitalised word or "project", so "about LIDAR" names nothing.
- A sentence echoes the employer when at least `ECHO_SHARE` of its own words sit in runs of `ECHO_RUN` words
  found in the advert or the company brief: "I am drawn to the challenge of maintaining low default rates"
  and "the role offers full in-house training" restated the advert as a reason (2026-11-07). Runs of three
  words rarely match by chance, and a sentence half made of them restates rather than gives a reason. The
  words of the job title and the employer's name (`names`) never count, so a sentence that only names the
  role or the company is not an echo, and neither is one with fewer than `ECHO_MIN_WORDS` other words.
"""
from __future__ import annotations

import re

from jobhunt.writing import check, stage

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


# A word of a card's name distinctive enough to stand for the card: an acronym ("LIDAR") or a word with
# inner capitals ("FerryApp").
DISTINCT = re.compile(r"[A-Z]{3,}\d*|[A-Z]?[a-z]+[A-Z]\w*")


def card_names(card: dict) -> list[str]:
    """Every name a card goes by: its own and its `aliases`, each also with "-" and "_" read as spaces."""
    names = [card["name"], *card.get("aliases", [])]
    return [n for n in dict.fromkeys(f for name in names if name.strip()
                                      for f in (name.strip(), re.sub(r"[-_]+", " ", name).strip()))]


def _distinct_words(card: dict, cards: list[dict]) -> list[str]:
    """The distinctive words of a card's name that no other card's names hold."""
    others = {w for c in cards if c is not card for n in card_names(c) for w in re.findall(r"\w+", n)}
    return [w for w in re.findall(r"\w+", card["name"]) if DISTINCT.fullmatch(w) and w not in others]


def names_card(sentence: str, card: dict, cards: list[dict] = ()) -> bool:
    """True when a sentence names a project card: by a name of `card_names`, or by a distinctive word of its
    name used as a name (see the module notes); `cards` are all the cards, to tell what is distinctive."""
    if any(names_tool(sentence, n) for n in card_names(card)):
        return True
    return any(re.search(rf"(?<![\w-]){re.escape(w)}(?=\s+([A-Z]\w*|projects?\b))", sentence)
               for w in _distinct_words(card, cards))


def misplaced_tools(draft: str, cards: list[dict], known: list[str]) -> list[str]:
    """Sentences that give a project card a tool from `known` that the card's own facts never name, each
    with the tool and the project: "The Lua tide gauge needed C++. (C++ is not among Tide gauge's tools)"."""
    projects = [c for c in cards if c["kind"].startswith("project") and c["name"].strip()]
    out = []
    for sentence in check.sentences(draft):
        named = [c for c in projects if names_card(sentence, c, projects)]
        if len(named) != 1:
            continue
        card = named[0]
        own = " ".join([*card_names(card), card["summary"], *card["tools"]])
        for tool in dict.fromkeys(known):
            if names_tool(sentence, tool) and not names_tool(own, tool):
                out.append(f"{sentence} ({tool} is not among {card['name']}'s tools)")
                break
    return out


def backed(tool: str, named: list[dict], facts: str = "") -> bool:
    """True when one of the `named` project cards names `tool` in its own facts (names, summary, tools), or a
    line of `facts` (the CV) names the card and the tool together."""
    lines = (facts or "").splitlines()
    for card in named:
        if names_tool(" ".join([*card_names(card), card["summary"], *card["tools"]]), tool):
            return True
        if any(names_tool(line, tool) and any(names_tool(line, n) for n in card_names(card)) for line in lines):
            return True
    return False


def without_tools(text: str, tools: list[str]) -> str:
    """`text` with each of `tools` taken out of the list it stands in: "Lua, Pandas and SQL" without Pandas is
    "Lua and SQL"."""
    for tool in tools:
        word = rf"(?<![\w+#]){re.escape(tool)}(?![\w+#])"
        flags = 0 if len(tool.strip()) <= SHORT_TOOL else re.I
        text = re.sub(rf"\s*,\s*{word}|{word}\s*,\s*|\s+(and|or|&)\s+{word}|{word}\s+(and|or|&)\s+|{word}", "", text,
                      flags=flags)
    text = re.sub(r",\s+(and|or)\s+", r" \1 ", text)
    text = re.sub(r",(?=\s*([.;:)]|$))", "", text)
    text = re.sub(r"\(\s*(tools:\s*)?\)", "", text)
    return re.sub(r"\s+([.,;:)])", r"\1", " ".join(text.split()))


# Words a sentence uses to place a project in a degree, in the person's own time, or at work.
SAID_DEGREE = re.compile(r"\b(degree(?! of)|university|coursework|dissertation|my studies|(my|our) research|"
                         r"research project|(as|for|in) (a|my|the|one) module|module (work|assignment|project))\b",
                         re.I)
SAID_PERSONAL = re.compile(r"\b(personal (project|work|interest|initiative)|(my|in my) own time|spare time|"
                           r"side project|hobby|hobbies|for fun)\b", re.I)
SAID_WORK = re.compile(r"\b(at work|placement|internship|in my (job|role)|for my employer|client)\b", re.I)
SAID = {"degree": SAID_DEGREE, "personal": SAID_PERSONAL, "work": SAID_WORK}
# For each context: the words that disagree with it, and the words that agree with it.
CLASHES = {stage.PERSONAL: ("degree",), stage.DEGREE: ("personal",), stage.TEAM: ("personal",),
           stage.WORK: ("degree", "personal")}
AGREES = {stage.PERSONAL: ("personal",), stage.DEGREE: ("degree",), stage.TEAM: ("degree",), stage.WORK: ("work",)}
NOT_WORDS = {"degree": "part of a degree", "personal": "a personal project"}
IS_WORDS = {stage.DEGREE: "degree work", stage.TEAM: "a university team project", stage.WORK: "work done in a job",
            stage.PERSONAL: "a personal project"}


def wrong_context(draft: str, cards: list[dict]) -> list[str]:
    """Sentences that place one project card in a degree or in the person's own time against its `context`,
    each with what the card says: "My degree includes Tide gauge. (Tide gauge is a personal project, not part
    of a degree)"."""
    every = [c for c in cards if c["kind"].startswith("project") and c["name"].strip()]
    projects = [c for c in every if c.get("context") in CLASHES]
    out = []
    for sentence in check.sentences(draft):
        named = [c for c in projects if names_card(sentence, c, every)]
        if len(named) != 1:
            continue
        card = named[0]
        if any(SAID[a].search(sentence) for a in AGREES[card["context"]]):
            continue
        clash = next((w for w in CLASHES[card["context"]] if SAID[w].search(sentence)), None)
        if clash:
            out.append(f"{sentence} ({card['name']} is {IS_WORDS[card['context']]}, not {NOT_WORDS[clash]})")
    return out


def right_context(text: str, cards: list[dict]) -> str:
    """`text` with each sentence that `wrong_context` flags given its card's own context words in place of the
    clashing ones ("Ferry planner, a personal project" of a team's card reads "Ferry planner, a university
    team project"). Only a sentence with one clashing phrase is changed, as a second ("a personal project
    built in my own time") would be left saying the opposite of the first; `wrong_context` still finds it."""
    for flagged in wrong_context(text, cards):
        sentence = REASON_TAIL.sub("", flagged)
        card = next((c for c in cards if c["kind"].startswith("project") and c["name"].strip()
                     and c.get("context") in CLASHES and names_card(sentence, c, cards)), None)
        clashes = [(w, m) for w in CLASHES[card["context"]] for m in SAID[w].finditer(sentence)] if card else []
        if len(clashes) != 1:
            continue
        clash = clashes[0][0]
        fixed = ARTICLE_TWICE.sub(r"\1", SAID[clash].sub(IS_WORDS[card["context"]], sentence, count=1))
        text = text.replace(sentence, fixed, 1)
    return text


REASON_TAIL = re.compile(r" \([^()]*\)$")          # the "(... is ..., not ...)" `wrong_context` adds
ARTICLE_TWICE = re.compile(r"\b(?:a|an|the|my|our)\s+((?:a|an)\s)", re.I)   # "a a university team project"
ECHO_RUN = 3               # words in a row that must match the employer's text to count
ECHO_SHARE = 0.5           # share of a sentence's own words in such runs that makes it an echo
ECHO_MIN_WORDS = 3         # a sentence needs this many own words in runs before it can echo
# Words that carry no content of their own; they are matched in runs but never counted.
LITTLE_WORDS = frozenset("""a an the and or but of to in on at for with by from as is are was were be been it its this
that these those i me my we our you your they their which who will would can could am do does did so than
into about also very""".split())
WHY_QUESTION = re.compile(r"\bwhy\b.*\b(work|join|apply|applying|want|interested|choose|chose)\b|"
                          r"\bmotivat\w*|\b(attracts?|appeals?|interests?) you\b", re.I)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def echoes(sentence: str, advert: str, brief: str = "", names: tuple[str, ...] = ()) -> bool:
    """True when a sentence restates the advert or company brief (see the module notes); `names` are the job
    title and the employer's name, whose words never count."""
    words = _tokens(sentence)
    theirs = [*_tokens(advert), "|", *_tokens(brief)]
    runs = {tuple(theirs[i:i + ECHO_RUN]) for i in range(len(theirs) - ECHO_RUN + 1)}
    named = {w for n in names for w in _tokens(n)}
    covered = [False] * len(words)
    for i in range(len(words) - ECHO_RUN + 1):
        if tuple(words[i:i + ECHO_RUN]) in runs:
            covered[i:i + ECHO_RUN] = [True] * ECHO_RUN
    own = [c for w, c in zip(words, covered, strict=True) if w not in LITTLE_WORDS and w not in named]
    echoed = sum(own)
    return echoed >= ECHO_MIN_WORDS and echoed >= ECHO_SHARE * len(own)


def first_paragraph(text: str) -> str:
    """A letter's first paragraph, where it says why this role and this employer."""
    return next((p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()), "")


def echoed(text: str, advert: str, brief: str = "", names: tuple[str, ...] = ()) -> list[str]:
    """The sentences of `text` that echo the advert or the brief (`echoes`)."""
    return [s for s in check.sentences(text) if echoes(s, advert, brief, names)]


CV_SHARE = 0.8             # share of a claim's own words one CV line must hold for the CV to state it
CV_MIN_WORDS = 3           # a claim needs this many own words before the CV can be said to state it


def stated_in(claim: str, cv_text: str) -> bool:
    """True when one line or sentence of the CV holds at least `CV_SHARE` of a claim's own words: the CV
    states it, so it is supported (a CV sentence was flagged as unsupported, 2026-11-07)."""
    own = {w for w in _tokens(claim) if w not in LITTLE_WORDS}
    if len(own) < CV_MIN_WORDS:
        return False
    parts = [set(_tokens(p)) for p in re.split(r"\n|(?<=[.!?;])\s+", cv_text or "") if p.strip()]
    return any(len(own & part) >= CV_SHARE * len(own) for part in parts)


def why_question(question: str) -> bool:
    """True for a form question that asks why the person wants this job or employer."""
    return bool(WHY_QUESTION.search(question or ""))

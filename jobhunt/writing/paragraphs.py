"""One paragraph about one piece of work: drafted from that work's facts alone, checked against them alone, and
drafted again once when something is wrong.

`settle(story, shape, scope)` asks the AI (prompt `story_paragraph`) for one paragraph about one story of the
evidence plan (`writing.plan.Story`) in a shape (`Shape`: a letter's middle, an answer, a message's body), finds
with no AI what is wrong with it against that story (`problems`), asks the AI once whether it says anything the
story's facts do not (`verify`, prompt `paragraph_verify`), drafts it once more with the problems stated, and
returns what is left (`Paragraph`). A letter's opening and closing are `writing.frame`'s, checked with the
same pieces.

Design decisions, each with its reason:

- A paragraph is drafted from one story's facts alone, as plain sentences with no evidence ids: a grader
  capped every pack at 2 of 5 for readability across four designs of sentence-by-sentence drafting and
  checking, which retold one project in several paragraphs, gave one project's test suite and tools to
  another, and left lists of facts, one-sentence paragraphs and a stray "[E4,M]" (15 packs, 2026-11-08). A
  prototype that wrote each paragraph in its own call with one card's facts gave coherent paragraphs that
  named their project, and no fact crossed, because none was in the prompt.
- Every check is scoped to the paragraph's story (`problems`): a number, or a known tool, its facts do not
  hold; a plan's tool said to be used (`writing.planned`); words placing the work in a degree or in the
  person's own time against its context; "I built" of a team's work without the person's part; a course that
  has ended spoken of as running (`stage.ended_course`); the wording `writing.plain` finds, the stock links of
  `plain.STOCK` among them; a sentence restating the advert (`drafts.echoes`), one about the person's work
  taking an advert phrase as its fact (`drafts.lifted`, `LIFTED_SAID`), or one already said in the
  paragraph; the person's part used as a label ("As the owner, I built ...", seen in the prototype); a skill
  only studied in a course said to be used (`writing.strengths`, `Scope.studied`); and four or more tools in
  one sentence, a list rather than a story. As a paragraph holds one piece of work, a
  tool or the name of another card in it is a crossed project, named with the other card's name, and a tool
  only an older CV gives the card (`older_cv` of `profile.writing.cards`) is no claim it can make.
- At most one AI check per paragraph (`verify`), with that story's facts only, and none when the checks with
  no AI already found a problem or when every word of the paragraph is in its facts (`restates`). Words the
  AI quotes as unsupported are dropped when the story's facts or the employer's words hold them, word for
  word or by their roots (`_dropped`): most of the old check list's "unsupported" items were the facts'
  own words or the advert's (2026-11-07).
- Every story with a name is checked for it, not only a card's (`_names`, 2026-11-08): passages of a
  dissertation were drafted as "I designed a pure simulation engine ..." with no project named. A module's or
  a project file's story must name its module or file's project, and is drafted again once when it does not.
  Answers and the message's body name their work in their first sentence too, as a letter paragraph does:
  12 of 15 graded packs had an answer or a message opening "I wrote a 113-test suite ..." or "a
  reproducible PyTorch pipeline ..." with the project named later or never (2026-11-08). A "why here" answer
  is the exception (`why`): it leads with the reason about the employer (`Shape.employer`,
  `motive.addresses`, `NOT_EMPLOYER`) and may tie it to its one piece of work in a sentence, as four graded
  answers said only "because I built <project>" (2026-11-08).
- A message body's question is about the employer's own work (`question_problems`, 2026-11-08): graders saw
  "segmentation of complex, unstructured data in live trading environments" asked of a trading firm and
  "cross-platform window management" of a consultancy, each the project's subject. The question must name a
  word of the advert or the company brief (`job_terms`), and shares no content word with
  the work's facts that the advert and the brief do not hold, unless the ranking places the work in its top
  half (`Shape.fits`, `rank.fits`), when the job is about that work's area. It is drafted again once when it
  fails.
- A "why here" answer is at least `WHY_LEAST` sentences, the reason about the employer then a tie to its
  one piece of work (`untied`, 2026-11-08): graders saw answers restating the advert and others with no tie.
  Its first sentence restating the advert (`motive.restated`) or no later sentence naming its work asks for
  one redraft; neither cuts nor drops it, and a redraft that breaks another check gives way to the draft
  that lacked only its tie.
- A message body never says the degree again (`message`, `says_again`, 2026-11-08): Hal's built opening says
  "I'm <name>, a first-class <subject> graduate from <university>", and the next sentence said the degree
  again. A sentence repeating the opening, or naming a degree with its words, is drafted again as a fact of
  the work.
- A paragraph that fails is drafted again once with its problems stated (`Paragraph.redrafted`). One still
  failing loses only its failing sentences, whole, when what is left still reads (each cut sentence leaves the
  next one pointing at something, `flow.reads_without`, and the shape keeps its least number of sentences and
  its name); else it is dropped and listed with why (`Paragraph.listed`). A sentence is never cut in part: a
  cut at a comma or "and", "while", "using", "with" or "which" left sentences such as "That experience in
  building precise." in 11 of 15 graded packs (2026-11-08). A sentence that was fixed or cut is never
  listed: only what the person should act on is. Nor is a module's or the degree's story dropped for
  having nothing to say (`education`): "Hal left out the paragraph about First-Class Honours: your facts gave
  nothing" was most of the false check items (2026-11-08), and the opening and the CV give the degree.
- A paragraph that passes every check with no AI and fails only the AI check is never dropped (`_doubted`,
  2026-11-08): one letter came out at 56 words after three paragraphs were dropped, two of them over claims
  the record supports, as they failed `paragraph_verify` after their redraft. Only the sentences the AI
  flagged are cut, and only when the rest still reads as above; else the paragraph is kept whole and listed
  for the person to check (`Paragraph.checks`). When the redraft fails a check with no AI and its sentences
  cannot be cut, the first draft is kept this way if it failed only the AI check.
- A letter paragraph ends on why the work matters for this job only when a fact clearly fits: no check
  asks for that last sentence. A check that redrafted a paragraph until its last sentence shared a word with
  the advert left generic ties ("This experience in building precise.") and raised generic sentences from 7
  to 19 in 15 graded packs (2026-11-08).
- A letter's requests carry one or two short excerpts of the person's own cover letters
  (`Scope.voice`, `voice_block`, `sections.letters.voice_excerpts`) to match their tone and sentence length,
  never as facts: no check counts their words, so a fact only an excerpt holds is flagged like any other
  (sections plan, 2026-11-09).
- A letter's paragraphs share the person's letter length (`letter`): what the opening and closing leave
  (`FRAME_WORDS`) divided among the stories, never under `PARAGRAPH_WORDS`, so three project paragraphs
  reach the length setting (a letter came to 118 words against 250, 2026-11-08).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import check as writing_check
from jobhunt.writing import drafts, flow, motive, plain, planned, purpose, repeats, spelling, stage, strengths, taught
from jobhunt.writing.motive import Pairing
from jobhunt.writing.plan import DEGREE, MODULE, Story

log = logging.getLogger(__name__)

PROMPT, VERIFY_PROMPT = "story_paragraph", "paragraph_verify"
ADVERT_CHARS = 3000        # of the advert a paragraph request carries, for what the job is; never to copy from
BRIEF_CHARS = 1500         # of the company brief it carries, for the employer's work a question may name
# Drafts are asked for cooler than other prose (0.7, `ai.openai_compat.PROSE_TEMPERATURE`): at 0.7 a local model
# added plausible details to fit the advert in every graded run (2026-11-07). The Claude CLI takes no temperature,
# so there it changes nothing.
DRAFT_TEMPERATURE = 0.4
LIST_TOOLS = 4             # tools named in one sentence that make it a list rather than a story
PART_SHARE = 0.5           # share of the person's part's own words a sentence must hold to name that part
ROOT_MIN = 3               # letters a word keeps when its ending is taken off ("uses" is "use", "is" stays)
ENDINGS = ("ing", "ed", "es", "s")
UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * n for n, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
SCALES = {"hundred": 100, "thousand": 1000}
NUMBER_WORD = "|".join([*UNITS, *TENS, *SCALES])
# A number in words, its parts joined by spaces, hyphens or "and" ("twenty-three", "one hundred and thirteen"), read
# whole by `_spelt`: the check once read "twenty-three" as 20 and dropped a true paragraph (2026-11-08).
NUMBER = re.compile(rf"\d[\d,.]*\d|\d|\b(?:{NUMBER_WORD})(?:(?:\s+and\s+|[\s-]+)(?:{NUMBER_WORD}))*\b", re.I)
# "I built X" and its kin: the whole of a piece of work claimed by one person.
SOLO = re.compile(r"\bI (?:have |had )?(?:single-handedly |alone |myself |personally )?(built|designed|led|developed|"
                  r"created|wrote|made|implemented|programmed|coded|engineered|delivered|architected|produced|"
                  r"ran|launched|founded)\b", re.I)
# Words that say the work was shared, or name the person's part of it.
SHARED = re.compile(r"\b(we|our|us|team|teams|group|together|jointly|classmates|teammates|colleagues|others|"
                    r"as part of|part of a|contributed|my part|my role|my share|alongside)\b", re.I)
# "As the owner, I ...": the person's part written as a label at a sentence's start.
AS_LABEL = re.compile(r"^\W*as (?:the|a|an|my) ([\w -]{1,40}?),", re.I)

NUMBER_SAID = "the number {n} is not in the facts of {card}"
TOOL_SAID = "{tool} is not in the facts of {card}"
CROSSED_TOOL = "{tool} is {other}'s, not {card}'s"
OLDER_CV_SAID = "{tool} is only on an older CV for {card}, never in its own sources; leave it out"
CROSSED_NAME = "names {other}, which is another piece of work; this paragraph is about {card}"
PLANNED_SAID = "{tool} was only planned for {card}; say it was planned, or leave it out"
ALONE = "says you did {card} alone; it was a team's work{part}"
CONTEXT_SAID = "calls {card} {wrong}; its facts say {said}"
ECHO_SAID = "restates the advert; say it in your own words"
LIFTED_SAID = ('takes "{phrase}" from the advert as a fact of your work; say what you did in your facts\' words, '
               "or keep the advert's words for a sentence about the employer")
REPEAT_SAID = "says again what an earlier sentence of the paragraph says"
LABEL_SAID = 'uses your part as a label ("{label}"); say what you did'
LIST_SAID = "lists tools rather than saying what you did with them"
NOT_NAMED = "does not name {card} in its first sentence"
NO_QUESTION = "asks no question about the team's work"
QUESTION_OFF = ("its question names nothing of the employer's own work; ask about a task, system or product the "
                "advert or the company brief names")
QUESTION_OWN = ('its question asks about your own work ("{words}"), not the employer\'s; ask about a task, system or '
                "product of theirs")
NOT_TIED = ("does not tie the reason to {card} in a sentence after the first; name it and one concrete thing it "
            "did")
NOT_EMPLOYER = ("does not give the reason about the employer in its first sentence; say what of the employer "
                "draws you, its product or its mission, then tie it to the work in one sentence at most")
SAID_AGAIN = "says your degree again, which Hal's opening already says; tell a fact of the work instead"
TOO_SHORT = "is too short to tell what the work was, what you did and what came of it"
NOT_SUPPORTED = 'its facts do not say: "{span}"'
NOT_SUPPORTED_ANY = "says something its facts do not"
WRONG_PLACE = "places the work where its facts do not"
TOO_MUCH = "gives you more of the work than its facts do"
NOTHING = "nothing in its facts fits what this job asks for"   # the writer drafted nothing for the shape
DROPPED = "Hal left out the paragraph about {card}: {why}. Add it yourself if it is true"
DOUBTED = "Check the paragraph about {card}: Hal's check of it found that {why}"


@dataclass(frozen=True)
class Shape:
    """What a paragraph is for: its kind for the writing rules, the request's words, where it names its work
    ("first" sentence, or "" for nowhere) and the least number of sentences it keeps."""

    kind: str                  # `writing.rules` kind: "letter" or "answer" (a message reads as an answer)
    words: str
    name: str = "first"
    least: int = 1
    question: bool = False     # it must ask one question (a message's body)
    after: str = ""            # what Hal puts before it (the message's opening sentence), never to say again
    employer: bool = False     # its first sentence speaks of the employer (a "why here" answer, `motive.addresses`)
    fact: str = ""             # the employer's fact that first sentence may name its product by
    fits: bool = False         # the ranking places its work in its top half, so its question may touch its area


LETTER = Shape("letter", "One paragraph of the middle of a cover letter: 3 to 4 sentences, 60 to 90 words, one "
                         "connected story. Name the work in the first sentence. End with why it matters for this job "
                         "only when a fact clearly fits.", "first", 2)
MESSAGE = Shape("answer", "The body of a short message to the person hiring, after Hal's own greeting and opening "
                          "sentence: one or two sentences about this work, naming it in the first, then one specific "
                          "question about the employer's own work: a task, system or product the advert or the "
                          "company brief names, never this work's own subject. At most 60 words in all. No "
                          "greeting, no introduction and no sign-off.", "first", 2, True)
FITS_WORDS = " The job fits this work, so the question may touch on its area."
DEGREE_SHARED = 2          # words of the opening's course a sentence naming a degree shares to say it again


def message(after: str, fits: bool = False) -> Shape:
    """The message body's shape after Hal's opening sentence `after`, which the body must not say again; `fits`
    when the ranking places its work in its top half (`rank.fits`), so its question may touch the work's area."""
    return Shape(MESSAGE.kind, MESSAGE.words + (FITS_WORDS if fits else ""), MESSAGE.name, MESSAGE.least,
                 MESSAGE.question, after, fits=fits)


def says_again(sentence: str, after: str) -> bool:
    """True when a sentence repeats the opening before it (`repeats.same`), or names a degree with at least
    `DEGREE_SHARED` of the opening's own words ("I am a Software Studies graduate who ...")."""
    if not after:
        return False
    if repeats.same(sentence, after):
        return True
    own = {w for w in writing_check.words_of(after) if w not in drafts.LITTLE_WORDS}
    said = {w for w in writing_check.words_of(sentence) if w not in drafts.LITTLE_WORDS}
    return bool(DEGREE.search(sentence)) and len(own & said) >= DEGREE_SHARED


ANSWER_WORDS = 100         # words of a whole form answer, as the answers prompt asks
WHY_LEAST = 2              # sentences of a "why here" answer: the reason about the employer, then the work
FRAME_WORDS = 70           # words of a letter's opening and closing together: three sentences and one or two
PARAGRAPH_WORDS = (60, 90)  # least and most words a letter paragraph is asked for, whatever the length setting
PARAGRAPH_SPREAD = 15      # words either side of a paragraph's share of the letter


def letter(words: int | None, n: int) -> Shape:
    """The shape of a letter paragraph when the letter tells `n` of them in about `words` words: each a share of
    what the opening and closing leave (`FRAME_WORDS`), never under `PARAGRAPH_WORDS`. A letter of two module
    paragraphs came to 118 words against a 250-word setting (2026-11-08)."""
    if not words or n <= 0:
        return LETTER
    share = max(PARAGRAPH_WORDS[0] + PARAGRAPH_SPREAD, (int(words) - FRAME_WORDS) // n)
    low, high = max(PARAGRAPH_WORDS[0], share - PARAGRAPH_SPREAD), max(PARAGRAPH_WORDS[1], share + PARAGRAPH_SPREAD)
    return Shape("letter", LETTER.words.replace("60 to 90 words", f"{low} to {high} words"), "first", 2)


def answer(question: str, nth: int = 0, of: int = 1) -> Shape:
    """The shape of the `nth` (from 0) of `of` paragraphs answering a form question."""
    which = ("Answer the question in the first sentence, naming the work there" if nth == 0
             else "Give this as a further example, naming the work in the first sentence")
    return Shape("answer", f'One paragraph of the answer to the application question "{question}". {which}: what '
                           f"it was, what you did and what came of it, in 2 to 4 sentences and at most "
                           f"{ANSWER_WORDS // of} words.", "first", 1)


def why(question: str, pairing: Pairing | None, tied: bool = True) -> Shape:
    """The shape of an answer to a question asking why this job or employer: the reason about the employer
    first, from the motivation `pairing`'s fact, then one sentence tying it to this work, with the pairing's
    reason when the work is the pairing's own (`tied`)."""
    because = f", because {pairing.reason}," if pairing and tied else ""
    reason = (f"The reason about the employer: \"{pairing.fact}\". Say in the first sentence one concrete thing "
              f"about the employer from it (its product, its mission or this fact), as you see it, in your own "
              f"words; never restate the advert or quote an employee. Then tie it to this work{because} in one "
              f"sentence, naming the work and one concrete thing it did." if pairing else
              "No fact about the employer was found: say in the first sentence what of this role or this employer "
              "draws you, in your own words, without a fact about the employer you were not given; then tie it "
              "to this work in one sentence, naming the work and one concrete thing it did.")
    return Shape("answer", f'The answer to the application question "{question}", in 2 to 3 sentences and at most '
                           f"80 words, answering it in the first sentence. {reason}", "", WHY_LEAST,
                 employer=True, fact=pairing.fact if pairing else "")


@dataclass(frozen=True)
class Scope:
    """What every paragraph of one job's drafts is checked against, beside its own story."""

    stories: tuple[Story, ...] = ()    # every story of the plan, to name whose a crossed tool is
    cards: tuple[dict, ...] = ()       # `profile.writing.cards`
    known: tuple[str, ...] = ()        # every tool a card or checked skill names (`pack.known_tools`)
    allowed: str = ""                  # the employer's words and the stage line: numbers there are not claims
    advert: str = ""
    brief: str = ""
    names: tuple[str, ...] = ()        # the job title and the employer's name (`drafts.echoes`)
    now: stage.Stage | None = None
    modules: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()      # the CV and the passages, for what a module covered
    person: tuple[str, ...] = ()       # the person's names, never a sentence's subject (`writing.plain`)
    labels: tuple[str, ...] = ()       # the settings wording a draft must never paste
    values: dict = field(default_factory=dict)   # the prompt values (`pack.person`)
    context: str = ""                  # the job lines every request carries
    request: str = ""                  # the person's own words, when they asked for a change
    studied: tuple[str, ...] = ()      # skills only course material shows (`profile.writing.studied_skills`)
    voice: tuple[str, ...] = ()        # excerpts of the person's own letters, for their voice only, never facts


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a paragraph: the sentence it is in ("" for the whole paragraph) and why."""

    sentence: str
    reason: str

    def line(self) -> str:
        """The problem as a request or a log states it."""
        return f'"{self.sentence}": {self.reason}' if self.sentence else self.reason


@dataclass
class Paragraph:
    """A paragraph after its checks: its text ("" when dropped), why it was dropped, and what was done."""

    story: Story
    text: str = ""
    dropped: str = ""
    calls: int = 0
    redrafted: bool = False
    cut: int = 0
    doubts: tuple[str, ...] = ()   # what the AI check found in a paragraph kept whole (`_doubted`)

    @property
    def listed(self) -> str:
        """The check list's line for a dropped paragraph, "" for one that stands and for a course's story that had
        nothing to say (`education`), which the opening and the CV already give."""
        if not self.dropped or (self.dropped == NOTHING and education(self.story)):
            return ""
        return DROPPED.format(card=_called(self.story), why=self.dropped)

    @property
    def checks(self) -> list[str]:
        """The check list's line for a paragraph kept whole although the AI check doubted it; [] for none."""
        return [DOUBTED.format(card=_called(self.story), why="; ".join(self.doubts))] if self.doubts else []


def education(story: Story) -> bool:
    """True for a story about a module or the degree itself (`plan.DEGREE` in its name, or in its facts when it
    has no name)."""
    return story.kind == MODULE or bool(DEGREE.search(story.name)) or (
        not story.name and any(DEGREE.search(f) for f in story.facts))


def _called(story: Story) -> str:
    """How a check names a story: its card's name, else the start of its first fact."""
    if story.name:
        return story.name
    first = story.facts[0] if story.facts else "this evidence"
    return f'"{first[:60].rsplit(" ", 1)[0] if len(first) > 60 else first}"'


def _spelt(phrase: str) -> list[int]:
    """The numbers a run of number words says: "twenty-three" is [23], "one hundred and thirteen" is [113], and
    words that do not build one number ("two and three", "seven eight") are each their own."""
    out: list[int] = []
    total = part = 0
    last = ""                  # the kind of the word before: "unit", "teen", "tens", "scale", "and" or ""
    for word in re.split(r"[\s-]+", phrase.lower()):
        kind = ("and" if word == "and" else "scale" if word in SCALES else "tens" if word in TENS
                else "teen" if UNITS.get(word, 0) >= 10 else "unit")
        joins = (last == "tens" and kind == "unit") or (last in ("scale", "and") and kind in ("unit", "teen", "tens"))
        joins = joins or (kind == "scale" and last in ("unit", "teen", "tens")) or (kind == "and" and last == "scale")
        if last and not joins:
            out.append(total + part)
            total = part = 0
        if kind == "scale":
            part = (part or 1) * SCALES[word]
            if SCALES[word] >= SCALES["thousand"]:
                total, part = total + part, 0
        elif kind != "and":
            part += UNITS.get(word, TENS.get(word, 0))
        last = kind
    return [*out, total + part] if last and last != "and" else out


def numbers(text: str) -> set[str]:
    """The numbers of a text, in figures, with words read as figures ("seven" is "7", "twenty-three" is "23");
    a code's digits (`writing.check.without_codes`) are no number."""
    out = set()
    for m in NUMBER.finditer(writing_check.without_codes(text)):
        said = m.group(0).lower().rstrip(".,")
        out |= {str(n) for n in _spelt(said)} if said[0].isalpha() else {said.replace(",", "")}
    return out


def _root(word: str) -> str:
    """A word without its ending ("records", "recorded" and "recording" are "record"; "guide" is "guid")."""
    for end in ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= ROOT_MIN:
            word = word[:-len(end)]
            break
    return word[:-1] if word.endswith("e") and len(word) > ROOT_MIN else word


def _roots(text: str) -> set[str]:
    return {_root(w) for w in re.findall(r"[a-z0-9+#]{2,}", (text or "").lower()) if w not in drafts.LITTLE_WORDS}


def _held(span: str, where: str) -> bool:
    words = re.findall(r"\S+", span.strip().strip("\"'").rstrip(".,;:"))
    return bool(words) and re.search(r"\s+".join(map(re.escape, words)), where, re.I) is not None


def _dropped(span: str, where: str) -> bool:
    """True when quoted words are no problem: `where` holds them word for word, or holds every one's root."""
    own = _roots(span)
    return _held(span, where) or (bool(own) and own <= _roots(where))


def _projects(scope: Scope) -> list[dict]:
    return [c for c in scope.cards if c.get("name", "").strip()]


def _names(sentence: str, story: Story, scope: Scope) -> bool:
    """True when a sentence names the story's work: its card by any name, for a job its title or employer, and
    for a story with no card the name it was given (a module's, a project file's); True for a story with no
    name at all."""
    if story.card is None:
        return not story.name or drafts.names_card(sentence, {"name": story.name, "aliases": list(story.aliases)})
    if drafts.names_card(sentence, story.card, list(_projects(scope))):
        return True
    return story.card.get("kind") == "job" and any(drafts.names_tool(sentence, p) for p in story.name.split(" at "))


def _owner(tool: str, story: Story, scope: Scope) -> str:
    """The name of another story or card whose own facts hold `tool`; "" when none does."""
    for other in scope.stories:
        if other.key != story.key and other.name and drafts.names_tool(other.words(), tool):
            return other.name
    for c in _projects(scope):
        if c["name"] != story.name and drafts.names_tool(" ".join([c["summary"], *c["tools"]]), tool):
            return c["name"]
    return ""


def _tool_problems(sentence: str, story: Story, scope: Scope) -> list[str]:
    """The tools a sentence names that its story's facts do not hold, each as a plan's tool, another's or not
    its story's at all."""
    own, plans = story.words(), "\n".join([*story.planned, *story.planned_tools])
    known = dict.fromkeys([*scope.known, *(t for s in scope.stories for t in s.tools),
                           *(t for c in scope.cards for t in c.get("tools") or ())])
    out = []
    for tool in (t for t in known if t.strip()):
        if not drafts.names_tool(sentence, tool) or drafts.names_tool(own, tool):
            continue
        if drafts.names_tool(plans, tool):
            if not planned.said(sentence):
                out.append(PLANNED_SAID.format(tool=tool, card=_called(story)))
            continue
        if story.card is not None and tool in (story.card.get("older_cv") or ()):
            out.append(OLDER_CV_SAID.format(tool=tool, card=_called(story)))
            continue
        other = _owner(tool, story, scope)
        out.append(CROSSED_TOOL.format(tool=tool, other=other, card=_called(story)) if other
                   else TOOL_SAID.format(tool=tool, card=_called(story)))
    return out


def _crossed_names(sentence: str, story: Story, scope: Scope) -> list[str]:
    """Another card a sentence names, which its story's own facts never name."""
    own = story.words()
    projects = _projects(scope)
    return [CROSSED_NAME.format(other=c["name"], card=_called(story)) for c in drafts.named_cards(sentence, projects)
            if c["name"] != story.name and c not in drafts.named_cards(own, projects)]


def _context_problem(sentence: str, story: Story) -> str:
    """What is wrong with where a sentence places the work, given its story's context."""
    context = story.context
    if context not in drafts.CLASHES or any(drafts.SAID[a].search(sentence) for a in drafts.AGREES[context]):
        return ""
    for word in drafts.CLASHES[context]:
        if drafts.SAID[word].search(sentence):
            return CONTEXT_SAID.format(card=_called(story), wrong=drafts.NOT_WORDS[word],
                                       said=drafts.IS_WORDS[context])
    return ""


def _names_part(sentence: str, role: str) -> bool:
    """True when a sentence holds the person's part: at least `PART_SHARE` of the role's own words."""
    part = stage.TEAM_WORDS.sub("", role)
    own = {w for w in writing_check.words_of(part) if w not in drafts.LITTLE_WORDS}
    return bool(own) and len(own & set(writing_check.words_of(sentence))) >= PART_SHARE * len(own)


def _alone_problem(sentence: str, story: Story) -> str:
    """What is wrong when a sentence says the person did a team's work alone."""
    team = story.context == stage.TEAM or bool(stage.team_words(story.role))
    if not team or not SOLO.search(sentence) or SHARED.search(sentence) or _names_part(sentence, story.role):
        return ""
    part = next((p for p in story.role.split("; ") if p and not stage.team_words(p)), "")
    return ALONE.format(card=_called(story), part=f"; your part: {part}" if part else "")


def _label_problem(sentence: str, story: Story) -> str:
    found = AS_LABEL.match(sentence)
    if not found or not story.role:
        return ""
    words = {w for w in writing_check.words_of(found.group(1)) if w not in drafts.LITTLE_WORDS}
    role = set(writing_check.words_of(story.role))
    return LABEL_SAID.format(label=found.group(0).strip().rstrip(",")) if words & role else ""


def _reasons(found: list[str]) -> list[str]:
    """The reasons in brackets at the end of what a `writing.drafts` or `writing.taught` check finds."""
    return [m.group(1) for m in (re.search(r"\(([^()]*)\)$", f) for f in found) if m]


def sentence_problems(sentence: str, story: Story, scope: Scope, before: list[str] = ()) -> list[str]:
    """What is wrong with one sentence of a paragraph about `story`, with no AI (see the module notes);
    `before` are the paragraph's sentences ahead of it."""
    own = story.words() + ("\n" + "\n".join(story.planned) if planned.said(sentence) else "")
    out = [NUMBER_SAID.format(n=n, card=_called(story))
           for n in sorted(numbers(sentence) - numbers(f"{own}\n{scope.allowed}"))]
    out += _tool_problems(sentence, story, scope)
    held = [t for t in dict.fromkeys([*story.tools, *scope.known])
            if t.strip() and drafts.names_tool(sentence, t) and drafts.names_tool(story.words(), t)]
    out += purpose.unbacked(sentence, held, list(story.facts), f"{story.name}\n{story.summary}",
                            list(dict.fromkeys([*scope.known, *story.tools])))
    out += _crossed_names(sentence, story, scope)
    out += strengths.problems(sentence, scope.studied, story.words())
    if story.card is not None:
        out += _reasons(drafts.crossed(sentence, list(scope.cards)))
    out += [p for p in (_context_problem(sentence, story), _alone_problem(sentence, story),
                        _label_problem(sentence, story)) if p]
    if scope.now is not None and stage.ended_course(sentence, scope.now):
        out.append(stage.ended_said(scope.now))
    out += plain.problems(sentence, list(scope.person), list(scope.labels))
    if scope.advert and drafts.echoes(sentence, scope.advert, scope.brief, scope.names):
        out.append(ECHO_SAID)
    phrase = "" if not scope.advert or drafts.about_employer(sentence, scope.names) else \
        drafts.lifted(sentence, scope.advert, scope.brief, scope.names, own)
    if phrase:
        out.append(LIFTED_SAID.format(phrase=phrase))
    if any(repeats.same(sentence, b) for b in before):
        out.append(REPEAT_SAID)
    if sum(drafts.names_tool(sentence, t) for t in dict.fromkeys(scope.known)) >= LIST_TOOLS:
        out.append(LIST_SAID)
    out += _reasons(taught.unsupported(sentence, list(scope.modules), (*scope.sources, own)))
    out += _reasons(taught.joined(sentence, list(scope.modules), scope.sources, list(scope.cards)))
    out += _reasons(taught.tooled(sentence, list(scope.modules), (*scope.sources, own), list(scope.known)))
    return list(dict.fromkeys(out))


def problems(text: str, story: Story, scope: Scope, shape: Shape) -> list[Problem]:
    """What is wrong with a paragraph about `story`, with no AI: each sentence's problems, the work left unnamed
    where the shape names it, too few sentences, and the writing rules that make a draft be revised
    (`writing.check.high`, numbers aside, which are checked against the story)."""
    said = writing_check.sentences(text)
    out = [Problem(s, r) for k, s in enumerate(said) for r in sentence_problems(s, story, scope, said[:k])]
    out += [Problem(s, SAID_AGAIN) for s in said if says_again(s, shape.after)]
    if story.name and said and shape.name == "first" and not _names(said[0], story, scope):
        out.append(Problem("", NOT_NAMED.format(card=story.name)))
    if len(said) < shape.least:
        out.append(Problem("", TOO_SHORT))
    if shape.question and "?" not in text:
        out.append(Problem("", NO_QUESTION))
    out += [Problem("", r) for r in question_problems(text, story, scope, shape)]
    employer = scope.names[1] if len(scope.names) > 1 else ""
    if shape.employer and said and not motive.addresses(said[0], employer, shape.fact):
        out.append(Problem("", NOT_EMPLOYER))
    hits = writing_check.check(text, shape.kind, spelling=scope.values.get("spelling_code", ""),
                               allowed=scope.values.get("own_words") or ())
    for hit in writing_check.high(hits):
        where = next((s for s in said if hit.quote.lower() in s.lower()), "")
        out.append(Problem(where, f"{hit.message} ({hit.quote})"))
    return list(dict.fromkeys(out))


LINK_STEM = 5              # letters two words share to be one word, as `motive.STEM` ("triage" and "triaging")


def job_terms(scope: Scope) -> set[str]:
    """The word stems (`LINK_STEM` letters) of the advert and the company brief that can name a part of this
    job: little words, words any employer or piece of work uses (`motive.GENERIC`, `purpose.GENERAL`), the
    job title and the employer's name, and tool names left out, as a tool is the person's, not a task."""
    left = {w for n in (*scope.names, *scope.known) for w in writing_check.words_of(n)}
    left |= drafts.LITTLE_WORDS | motive.GENERIC | purpose.GENERAL
    return {w[:LINK_STEM] for w in writing_check.words_of(f"{scope.advert}\n{scope.brief}")
            if len(w) > 2 and w not in left}


def linked(sentence: str, story: Story, scope: Scope) -> bool:
    """True when a sentence names a part of this job (`job_terms`) beyond the work's own name, as a message's
    question must (`question_problems`)."""
    own = {w[:LINK_STEM] for w in writing_check.words_of(f"{story.name} {' '.join(story.aliases)}")}
    return bool(({w[:LINK_STEM] for w in writing_check.words_of(sentence)} - own) & job_terms(scope))


def _content_stems(text: str) -> set[str]:
    """The stems (`LINK_STEM` letters) of a text's words that say something: little words and words any employer
    or piece of work uses left out."""
    return {w[:LINK_STEM] for w in writing_check.words_of(text) if len(w) > 2 and w not in drafts.LITTLE_WORDS
            and w not in motive.GENERIC and w not in purpose.GENERAL}


def question_problems(text: str, story: Story, scope: Scope, shape: Shape) -> list[str]:
    """What is wrong with the question a message body asks (see the module notes): it names nothing of the job
    (`job_terms`), or, unless the ranking fits its work to the job (`Shape.fits`), it shares a word with the
    work's own facts that the advert and the brief do not hold."""
    asked = next((s for s in reversed(writing_check.sentences(text)) if s.rstrip().endswith("?")), "")
    if not shape.question or not asked or not (scope.advert or scope.brief):
        return []
    out = [] if linked(asked, story, scope) else [QUESTION_OFF]
    own = _content_stems("\n".join([story.summary, *story.facts])) - job_terms(scope)
    mine = sorted(w for w in writing_check.words_of(asked) if w[:LINK_STEM] in own & _content_stems(asked))
    if mine and not shape.fits:
        out.append(QUESTION_OWN.format(words=", ".join(dict.fromkeys(mine))))
    return out


def untied(text: str, story: Story, scope: Scope, shape: Shape) -> list[Problem]:
    """What a "why here" answer (`Shape.employer`) lacks: its first sentence restates the advert
    (`motive.restated`), or no later sentence names its work (`NOT_TIED`); [] else, and [] for other shapes.
    It asks for a redraft but never cuts or drops a paragraph (`settle`)."""
    said = writing_check.sentences(text)
    if not shape.employer or not said:
        return []
    out = [Problem("", r) for r in [motive.restated(said[0], scope.advert, scope.names)] if r]
    if story.name and len(said) > 1 and not any(_names(s, story, scope) for s in said[1:]):
        out.append(Problem("", NOT_TIED.format(card=story.name)))
    return out


def restates(text: str, story: Story, scope: Scope) -> bool:
    """True when every content word of a paragraph, by its root, is in its story's facts or the employer's words:
    it says nothing they do not, so no AI check is needed."""
    own = _roots(text)
    return bool(own) and own <= _roots(f"{story.words()}\n{scope.allowed}\n{' '.join(scope.names)}")


def verify(text: str, story: Story, scope: Scope) -> list[Problem]:
    """What the AI finds a paragraph says that its story's facts do not (prompt `paragraph_verify`); quoted
    words the facts or the employer's words hold are dropped (`_dropped`); [] for a reply that is not JSON."""
    request = f"Paragraph:\n{text}\n\nThe facts of the work it is about:\n{story.block()}"
    reply = llm.chat_json(prompts.text(VERIFY_PROMPT), request, role="main", purpose=VERIFY_PROMPT)
    if not isinstance(reply, dict):
        return []
    said, where = writing_check.sentences(text), f"{story.words()}\n{scope.allowed}\n{' '.join(scope.names)}"
    out = []
    if reply.get("supported") is False:
        given = [str(s) for s in reply.get("unsupported") or [] if str(s).strip()]
        spans = [s for s in given if not _dropped(s, where)]
        out += [Problem(next((x for x in said if _held(s, x)), ""), NOT_SUPPORTED.format(span=s.strip().strip("\"'")))
                for s in spans]
        if not given:
            out.append(Problem("", NOT_SUPPORTED_ANY))
    if reply.get("context_ok") is False:
        out.append(Problem("", WRONG_PLACE))
    if reply.get("role_ok") is False:
        out.append(Problem("", TOO_MUCH))
    return out


def ask(prompt_id: str, request: str, scope: Scope, key: str = "paragraph") -> dict | str:
    """One draft from the AI: the `key` of its JSON reply, undashed and respelt ("" for none), or the whole reply
    when `key` is ""."""
    reply = llm.chat_json(prompts.text(prompt_id, **scope.values), request, role="writer",
                          temperature=DRAFT_TEMPERATURE, purpose=prompt_id)
    if not key:
        return reply if isinstance(reply, dict) else {}
    return clean(reply.get(key) if isinstance(reply, dict) else "", scope)


def clean(said, scope: Scope) -> str:
    text = " ".join(str(said or "").split())
    return spelling.respell(llm.undash(text), scope.values.get("spelling_code")) if text else ""


VOICE_HEADING = ("How this person writes (from their own letters; match the tone and sentence length, never copy "
                 "a fact or a phrase from it):")


def voice_block(scope: Scope) -> str:
    """The person's voice excerpts as a request carries them; "" when there are none."""
    return "\n".join([VOICE_HEADING, *(f"<<<\n{v}\n>>>" for v in scope.voice)]) if scope.voice else ""


def _request(story: Story, shape: Shape, scope: Scope, last: str = "", found: list[Problem] = ()) -> str:
    parts = [f"Shape: {shape.words}", story.block(), scope.context, voice_block(scope)]
    if scope.request:
        parts.append(f"The applicant asked for this change: {scope.request}")
    if last:
        parts.append(f"Your last paragraph:\n{last}\n\nWhat is wrong with it:\n"
                     + "\n".join(f"- {p.line()}" for p in found)
                     + "\nWrite it again without these problems, from the facts above alone.")
    return "\n\n".join(p for p in parts if p)


def draft(story: Story, shape: Shape, scope: Scope, last: str = "", found: list[Problem] = ()) -> str:
    """One paragraph about `story` in `shape` from the AI (prompt `story_paragraph`); with `last` and `found`,
    the paragraph drafted again without those problems. "" when the facts give nothing for the shape."""
    return ask(PROMPT, _request(story, shape, scope, last, found), scope)


def _judge(text: str, story: Story, scope: Scope, shape: Shape, out: Paragraph) -> tuple[list[Problem], bool]:
    """The checks with no AI, then, when they find nothing and the paragraph says more than its facts' own words,
    the one AI check; with whether what was found is the AI check's alone."""
    found = problems(text, story, scope, shape)
    if found or restates(text, story, scope):
        return found, False
    out.calls += 1
    return verify(text, story, scope), True


def _cut(text: str, found: list[Problem], story: Story, scope: Scope, shape: Shape) -> str:
    """`text` without its failing sentences, each cut whole, when what is left still reads (see the module
    notes); "" else."""
    if any(not p.sentence for p in found):
        return ""
    kept = text
    for sentence in dict.fromkeys(p.sentence for p in found):
        said = writing_check.sentences(kept)
        if sentence not in said or not flow.reads_without(kept, sentence):
            return ""
        kept = " ".join(s for s in said if s != sentence)
    return "" if problems(kept, story, scope, shape) else kept


def _changed(text: str, kept: str) -> int:
    """How many sentences of `text` were cut in `kept`."""
    return len(writing_check.sentences(text)) - len(writing_check.sentences(kept))


def _doubted(out: Paragraph, text: str, found: list[Problem], story: Story, scope: Scope, shape: Shape) -> Paragraph:
    """A paragraph that failed only the AI check, kept: without the sentences the AI flagged when the rest still
    reads, else whole with what the AI found to check (see the module notes)."""
    kept = _cut(text, found, story, scope, shape)
    if kept:
        out.text, out.cut = kept, _changed(text, kept)
    else:
        out.text, out.doubts = text, tuple(dict.fromkeys(p.reason for p in found))
    log.info("paragraph about %s kept although the AI check found: %s", _called(story),
             "; ".join(p.line() for p in found))
    return out


def settle(story: Story, shape: Shape, scope: Scope) -> Paragraph:
    """One paragraph about `story` in `shape`: drafted, checked (`problems`, then `verify`), drafted once more
    with what is wrong, then kept, kept without its failing sentences, or dropped; never dropped for the AI
    check alone (see the module notes)."""
    out = Paragraph(story)
    text = draft(story, shape, scope)
    out.calls += 1
    if not text:
        out.dropped = NOTHING
        return out
    found, by_ai = _judge(text, story, scope, shape, out)
    first = (text, found, by_ai)
    asked = [*found, *untied(text, story, scope, shape)]
    if asked:
        log.info("paragraph about %s: %s", _called(story), "; ".join(p.line() for p in asked))
        again = draft(story, shape, scope, text, asked)
        out.calls += 1
        out.redrafted = True
        if again:
            text, (found, by_ai) = again, _judge(again, story, scope, shape, out)
        if found and not first[1]:     # the redraft broke a draft that lacked only its tie: keep that draft
            text, found, by_ai = first
    if not found:
        out.text = text
        return out
    if by_ai:
        return _doubted(out, text, found, story, scope, shape)
    kept = _cut(text, found, story, scope, shape)
    if kept:
        out.text, out.cut = kept, _changed(text, kept)
    elif out.redrafted and first[2]:
        return _doubted(out, *first[:2], story, scope, shape)
    else:
        out.dropped = "; ".join(dict.fromkeys(p.reason for p in found))
    log.info("paragraph about %s after its redraft: %s", _called(story), "; ".join(p.line() for p in found))
    return out

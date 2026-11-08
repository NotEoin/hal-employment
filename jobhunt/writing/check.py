"""The deterministic writing checker: letters, CV lines and form answers against `writing.rules`.

`check(text, kind, ...)` returns a `Hit` for each break of a rule a pattern or a count can see, with the
words quoted, so the same draft always gets the same hits. It needs no AI and reads nothing but its
arguments.

Design decisions, each with its reason:

- Two kinds of word list. A short list fails on one match (L1). A longer list of fashionable words is
  flagged only when two or more sit in one paragraph (L2), and evaluative words only by density (L3),
  since each of them is sometimes simply the right word.
- Numbers are checked with `pack.unsupported_numbers`' rule, kept here as `unsupported_numbers`, so a letter,
  a CV suggestion and a form answer agree on what counts as an invented number. A code (`CODE`: capitals with
  digits, such as a module code "CSC2031" and its year "CSC2031/23") is a name, not a number
  (`without_codes`): "number not in the CV: 2031/23" was listed in every pack of one person (2026-11-08).
- A letter's greeting and sign-off are checked only when the text holds both; the pack adds them around
  the model's body itself.
- A word in the other spelling (L10) comes with the form to write instead (`Hit.fix`, from
  `writing.spelling`), so the flag says what to change and a caller can apply it without the AI.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass

from jobhunt.writing import rules
from jobhunt.writing import spelling as spelling_mod
from jobhunt.writing.rules import ANSWER, CV, LETTER

BULLET_WORDS_MAX = 35         # one idea per bullet; longer reads as a paragraph (research B1)
OPENER_REPEAT_MAX = 3         # bullets that may start with the same word (B7)
CLUSTER_MIN = 2               # fashionable words in one paragraph before L2 flags them
DENSITY_MIN = 3               # uses of one evaluative word before L3 can flag it
DENSITY_SHARE = 0.03          # ... and at least this share of the words
I_OPENER_SHARE = 0.5          # share of sentences starting with "I" before C11 flags it
I_OPENER_MIN = 4              # sentences a letter needs before that share means anything
# C5: a letter sentence that holds, in order, `VERBATIM_SHARE` of one whole CV line's words and adds at most
# `ADDED_WORDS_MAX` of its own is a copy of that line. A letter that uses a CV line's facts in a sentence of
# its own is what a letter is for: the old rule (80% of a line's words, in any order) flagged those
# (2026-11-07). Five added words still catch a pasted bullet with "In my last role I" in front.
VERBATIM_SHARE = 0.9
ADDED_WORDS_MAX = 5
REPEAT_MIN_WORDS = 6          # CV lines shorter than this are too short to be repeated meaningfully
LETTER_OVER = 1.5             # a letter longer than this times the set length is too long
LETTER_PAGE_WORDS = 450       # about one A4 page of letter; never longer than this
LETTER_UNDER = 0.6            # shorter than this share of the set length reads as thin (advice only)

# L1: one match is enough. Words recruiters read as machine-written, and the stock cliches of CVs.
AI_WORDS = re.compile(
    r"\b(delv(e|es|ed|ing)|tapestry|realm|beacon|testament to|game[- ]changer|cutting[- ]edge|paradigm shift|"
    r"synerg(y|ies|istic)|leverag(e|es|ed|ing)|utili[sz](e|es|ed|ing|ation)|spearhead(s|ed|ing)?|"
    r"orchestrat(ed|ing)|ever[- ]evolving|embark(ed|ing)?|unleash(ed|ing)?|passion(ate|ately)?|"
    r"results[- ]driven|go[- ]getter|self[- ]starter|team player|hard[- ]working|detail[- ]oriented|"
    r"think outside the box|proven track record|guru|ninja|rockstar|thrilled|excited|thrive|thriving|"
    r"fast[- ]paced|perfectly align|aligns? perfectly|eager to|dynamic)\b", re.I)
# L2: fashionable words, flagged when CLUSTER_MIN different ones share a paragraph.
CLUSTER_WORDS = re.compile(
    r"\b(harness(es|ed|ing)?|navigat(e|es|ed|ing)|foster(s|ed|ing)?|elevat(e|es|ed|ing)|empower(s|ed|ing)?|"
    r"streamlin(e|es|ed|ing)|bolster(s|ed|ing)?|facilitat(e|es|ed|ing)|resonat(e|es|ed|ing)|nuanced|crucial|"
    r"pivotal|robust|seamless(ly)?|meticulous(ly)?|comprehensive|holistic|impactful|actionable|multifaceted|"
    r"myriad|plethora|encompass(es|ed|ing)?|cultivat(e|es|ed|ing)|showcas(e|es|ed|ing)|underscor(e|es|ed|ing)|"
    r"enhanc(e|es|ed|ing)|vibrant|innovative|landscape)\b", re.I)
# L3: evaluative words that are fine once and a tic when repeated.
DENSITY_WORDS = ("significant", "effective", "exceptional", "remarkable", "sophisticated", "instrumental",
                 "compelling", "innovative")
FILLER = re.compile(
    r"\b(in order to|due to the fact that|at this point in time|in the event that|on a (daily|regular) basis|"
    r"in a timely manner|going forward|at the end of the day|it is worth noting|it's worth noting|"
    r"when it comes to|in terms of|for the purpose of|with a view to)\b", re.I)
# L5: an -ing tail that claims what something means (", showcasing my care"), not one that states a fact:
# ", ensuring patient-ID separation with assertion checks" was flagged (2026-11-07). A tail counts only when
# its verb is followed by a quality or a significance word, or by "how", "that" or "why".
MEANING_VERBS = (r"showcasing|highlighting|demonstrating|reflecting|underscoring|emphasi[sz]ing|illustrating|"
                 r"solidifying|cementing|signall?ing")
SIGNIFICANCE = (r"care|commitment|dedication|ability|abilities|skills?|passion|interest|enthusiasm|eagerness|value|"
                r"values|importance|significance|strengths?|potential|readiness|capacity|understanding|knowledge|"
                r"expertise|experience|growth|impact|success|excellence|versatility|adaptability|competence|"
                r"proficiency|capabilit(y|ies)|talents?|drive|role|attention|aptitude|suitability|fit")
ING_TAIL = re.compile(
    rf",\s+(thereby\s+)?(({MEANING_VERBS}|fostering|ensuring|contributing to)\s+((my|our|their|its|a|an|the|this|"
    rf"real|strong|genuine|deep|clear|lasting|key)\s+){{0,3}}({SIGNIFICANCE})\b|({MEANING_VERBS})\s+(how|that|why)\b)"
    rf"[^.!?]*", re.I)
PUFFERY = re.compile(
    r"\b(a testament to|plays? an? (vital|key|crucial|pivotal) role|pivotal moment|deeply committed|"
    r"invaluable experience|unique opportunity|rich (history|heritage) of|commitment to excellence)\b", re.I)
COPULA = re.compile(r"\b(serves as|serve as|stands as|boasts)\b", re.I)
# "X, not just Y" is the same staged contrast turned round ("This knowledge is practical, not just theoretical."):
# a real letter carried it past the check (2026-11-05).
CONTRAST = re.compile(r"\bnot (just|only|merely|simply)\b[^.!?]{1,80}?\bbut( also)?\b"
                      r"|,\s*not (just|only|merely|simply)\b"
                      r"|\b(it'?s|this is|that is) not (about )?[^.!?]{1,40}\.\s+(it'?s|this is|that is)\b", re.I)
SIZE_WORDS = re.compile(r"\b(significantly|dramatically|greatly|hugely|massively|vastly|substantially|"
                        r"exponentially|tremendously)\b", re.I)
DASH = re.compile("\u2014|\u2013|\\s--\\s")
BOLD = re.compile(r"\*\*[^*\n]+\*\*")
CHATBOT = re.compile(
    r"(?<!\w)(certainly[!,]|i hope this helps|as an ai\b|as a language model|as of my last|"
    r"here is (a|your) (draft|letter|revised)|here's (a|your) (draft|letter|revised)|feel free to adjust)"
    r"|\[(company|employer|your name|name|role|job title|insert[^\]]*|hiring manager)\]", re.I)
STOCK_OPENING = re.compile(
    r"(^|(?<=[.!?]\s))(in today's|in an ever|in the world of|as a passionate|with a strong background in|"
    r"i hope this (letter|message) finds you|furthermore,|moreover,|additionally,)", re.I | re.M)
HEDGE = re.compile(r"\b(i believe (that )?i (could|would|can|might)|i feel (that|like)|i think (that )?i|"
                   r"i would like to think|hopefully|perhaps|somewhat|i might be able to)\b", re.I)
WEAK_OPENER = re.compile(r"^(responsible for|duties (included|include)|tasks?:|in charge of|tasked with|"
                         r"helped|assisted|worked on|worked with|participated|was involved|involved in|"
                         r"exposed to|gained experience|contributed to|was required to|had to|my role was)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|me|my|we|our)\b")
LETTER_OPENING = re.compile(r"\b(i am writing to (express|apply|register|enquire|you)|i am writing in "
                            r"(response|regard)|i was (excited|thrilled|delighted) to (see|come across)|"
                            r"please accept this letter)\b", re.I)
STOCK_CLOSING = re.compile(
    r"\b(valuable (asset|addition)|great fit for your|perfect fit for|look forward to hearing( from you)?|"
    r"look forward to the opportunity to (contribute|discuss)|would welcome the opportunity to contribute|"
    r"i am confident (that )?i (would|will) (be|make))\b", re.I)
GREETING = re.compile(r"^\s*Dear\s+([^,\n]+),?\s*$", re.M)
SIGNOFF = re.compile(r"^\s*(Yours\s+(sincerely|faithfully)|Kind regards|Best wishes|Regards|Best regards|"
                     r"With best wishes)\b", re.M | re.I)
GENERIC_GREETING = re.compile(r"^(sir|madam|sir or madam|sir/madam|hiring (manager|team)|recruitment team|"
                              r"recruiter|recruiting manager|selection panel|team|colleagues|"
                              r"[\w&' -]+ (team|recruitment|recruiters|panel))$", re.I)
ADJECTIVES = frozenset("""
    adaptable ambitious analytical approachable articulate caring committed confident conscientious creative
    curious dedicated dependable determined diligent driven efficient energetic enthusiastic flexible focused
    friendly hardworking honest innovative inquisitive meticulous methodical motivated organised organized
    passionate patient personable positive proactive punctual reliable resilient resourceful responsible
    self-motivated sociable strategic supportive thorough trustworthy versatile
""".split())
TRIAD = re.compile(r"\b([a-z-]+),\s+([a-z-]+),?\s+and\s+([a-z-]+)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
NUMBER = re.compile(r"\d[\d,.]*")
# A code: two or more capitals, maybe more letters and a hyphen, then two or more digits, maybe a "/year".
CODE = re.compile(r"\b[A-Z]{2,}[A-Za-z]*-?\d{2,}\w*(?:/\d{2,4})?\b")


@dataclass(frozen=True)
class Hit:
    """One break of a rule: the rule, how much it matters, the words found and what to do about it.

    `fix` is the exact text to write in place of `quote` when there is only one right answer (a spelling);
    "" otherwise.
    """

    rule: str
    severity: str
    quote: str
    message: str
    fix: str = ""

    def to_dict(self) -> dict:
        """The hit as JSON-ready fields."""
        return asdict(self)

    @property
    def flag(self) -> str:
        """The line for "Check before sending": the rule's name, the words found and any exact fix."""
        return f"{rules.BY_ID[self.rule].name}: {self.quote}" + (f' (write "{self.fix}")' if self.fix else "")


def without_codes(text: str) -> str:
    """`text` with each code (`CODE`, a module code such as "CSC2031/23") taken out, as its digits are no number."""
    return CODE.sub(" ", text or "")


def unsupported_numbers(draft: str, *sources: str) -> list[str]:
    """Returns numbers in a draft that appear in none of the sources; a code's digits are no number."""
    # A number ending a sentence ("2023-2024.") is collected with its full stop; it is the same number.
    known = {n.rstrip(".,") for n in NUMBER.findall(without_codes(" ".join(sources)))}
    return sorted({n for n in NUMBER.findall(without_codes(draft)) if n.rstrip(".,") not in known and len(n) > 1})


def sentences(text: str) -> list[str]:
    """The sentences of a text, each stripped; headings, greetings and bullets count as sentences of their own."""
    out = []
    for para in re.split(r"\n\s*\n|\n(?=\s*[-*] )", text or ""):
        para = " ".join(para.split()).lstrip("-* ").strip()
        out += [s.strip() for s in SENTENCE.split(para) if s.strip()]
    return out


def words_of(text: str) -> list[str]:
    """The words of a text, lower case."""
    return re.findall(r"[a-z][a-z'-]*", (text or "").lower())


def _hit(rule_id: str, quote: str, message: str = "", *, fix: str = "") -> Hit:
    rule = rules.BY_ID[rule_id]
    return Hit(rule_id, rule.severity, " ".join(quote.split())[:120], message or rule.rule, fix)


def _applies(rule_id: str, kind: str) -> bool:
    return kind in rules.BY_ID[rule_id].applies


def _matches(rx: re.Pattern, text: str, allowed: set[str]) -> list[str]:
    found = []
    for m in rx.finditer(text):
        word = m.group(0).strip().lower()
        if word not in allowed and word not in found:
            found.append(word)
    return found


def _greeting_pairing(text: str) -> Hit | None:
    """C8: "Dear <name>" with "Yours faithfully", or a generic greeting with "Yours sincerely"."""
    greet, sign = GREETING.search(text), SIGNOFF.search(text)
    if not greet or not sign or not sign.group(2):
        return None
    to = greet.group(1).strip()
    generic = bool(GENERIC_GREETING.match(to))
    closing = sign.group(2).lower()
    if generic and closing == "sincerely":
        return _hit("C8", f"Dear {to} ... Yours sincerely",
                    f'"Dear {to}" names no one, so a UK letter ends "Yours faithfully".')
    if not generic and closing == "faithfully":
        return _hit("C8", f"Dear {to} ... Yours faithfully",
                    f'"Dear {to}" names the reader, so a UK letter ends "Yours sincerely".')
    return None


def _triads(text: str, allowed: set[str]) -> list[str]:
    out = []
    for m in TRIAD.finditer(text):
        group = [w.lower() for w in m.groups()]
        if sum(w in ADJECTIVES for w in group) >= 2 and not set(group) & allowed:
            out.append(m.group(0))
    return out


def _repeats(text: str, cv_text: str) -> list[str]:
    """C5: letter sentences that are a near word-for-word copy of one whole CV line (`VERBATIM_SHARE`)."""
    cv_lines = [words_of(line) for line in (cv_text or "").splitlines()]
    cv_lines = [w for w in cv_lines if len(w) >= REPEAT_MIN_WORDS]
    out = []
    for sentence in sentences(text):
        said = words_of(sentence)
        for line in cv_lines:
            blocks = difflib.SequenceMatcher(None, line, said, autojunk=False).get_matching_blocks()
            held = sum(b.size for b in blocks)
            if held >= VERBATIM_SHARE * len(line) and len(said) - held <= ADDED_WORDS_MAX:
                out.append(sentence)
                break
    return out


def _spelling(text: str, spelling: str | None) -> list[Hit]:
    """L10: each word in the other spelling once, with the form `spelling` writes instead."""
    code = spelling_mod.code_of(spelling)
    seen, hits = set(), []
    for f in spelling_mod.find(text, code):
        if f.word.lower() not in seen:
            seen.add(f.word.lower())
            hits.append(_hit("L10", f.word, f'Write "{f.fix}": your settings say {spelling_mod.LABELS[code]}.',
                             fix=f.fix))
    return hits


def _length(text: str, kind: str, max_words: int | None, target_words: int | None) -> list[Hit]:
    n = len(text.split())
    if kind == LETTER and target_words:
        most = min(max_words or LETTER_PAGE_WORDS, round(target_words * LETTER_OVER))
        if n > most:
            return [_hit("C1", f"{n} words", f"{n} words; keep it under {most} (you prefer about {target_words}).")]
        if n < LETTER_UNDER * target_words:
            return [Hit("C1", rules.LOW, f"{n} words", f"{n} words reads as thin next to the {target_words} you "
                                                       "prefer; add the evidence the advert asks for.")]
    elif max_words and n > max_words:
        return [_hit("C1", f"{n} words", f"{n} words; the limit is {max_words}.")]
    return []


def check(text: str, kind: str, *, sources: tuple[str, ...] | None = None, spelling: str | None = None,
          cv_text: str = "", max_words: int | None = None, target_words: int | None = None,
          allowed: tuple[str, ...] | set[str] = ()) -> list[Hit]:
    """Every rule `text` breaks, as hits with the words quoted; [] for a clean draft.

    `kind` is "letter", "cv" (one line or several, one per line) or "answer". `sources` are the texts every
    number must come from (None skips the number check); `spelling` is "en-GB" or "en-US", and anything else
    (unset included) is "en-GB", so the spelling is always checked; `cv_text` lets
    a letter be checked for repeating the CV; `allowed` holds words the person uses in their own writing.
    """
    if kind not in rules.KINDS:
        raise ValueError(f"kind must be one of {', '.join(rules.KINDS)}, not {kind!r}")
    text = text or ""
    allowed = {a.lower() for a in allowed}
    hits: list[Hit] = []
    if sources is not None:
        hits += [_hit("T1", n, f"{n} is in none of the facts; use the facts' own number or leave it out.")
                 for n in unsupported_numbers(text, *sources)]
        given = " ".join(sources).lower()
        hits += [_hit("T4", w) for w in _matches(SIZE_WORDS, text, allowed) if w not in given]
    hits += [_hit("L1", w) for w in _matches(AI_WORDS, text, allowed)]
    for para in re.split(r"\n\s*\n", text):
        found = {_stem(w) for w in _matches(CLUSTER_WORDS, para, allowed)}
        if len(found) >= CLUSTER_MIN:
            hits.append(_hit("L2", ", ".join(sorted(found))))
    all_words = words_of(text)
    for word in DENSITY_WORDS:
        n = all_words.count(word)
        if word not in allowed and n >= max(DENSITY_MIN, DENSITY_SHARE * len(all_words)):
            hits.append(_hit("L3", f"{word} ({n} times)"))
    hits += [_hit("L4", w) for w in _matches(FILLER, text, allowed)]
    hits += [_hit("L5", m.group(0).lstrip(", ")) for m in ING_TAIL.finditer(text)]
    hits += [_hit("L6", w) for w in _matches(PUFFERY, text, allowed)]
    hits += [_hit("L7", w) for w in _matches(COPULA, text, allowed)]
    hits += [_hit("L8", m.group(0)) for m in CONTRAST.finditer(text)]
    if _applies("L9", kind):
        hits += [_hit("L9", t) for t in _triads(text, allowed)]
    hits += _spelling(text, spelling)
    hits += [_hit("L11", text[max(0, m.start() - 20):m.end() + 20]) for m in DASH.finditer(text)]
    hits += [_hit("L12", m.group(0)) for m in BOLD.finditer(text)]
    hits += [_hit("L13", m.group(0)) for m in CHATBOT.finditer(text)]
    if kind in (LETTER, ANSWER):
        hits += [_hit("L16", m.group(0).strip()) for m in STOCK_OPENING.finditer(text)]
        hits += [_hit("L17", w) for w in _matches(HEDGE, text, allowed)]
    if kind == CV:
        hits += _cv_lines(text)
    if kind == LETTER:
        hits += [_hit("C2", w) for w in _matches(LETTER_OPENING, text, allowed)]
        hits += [_hit("C6", w) for w in _matches(STOCK_CLOSING, text, allowed)]
        hits += [_hit("C5", s) for s in _repeats(text, cv_text)]
        pairing = _greeting_pairing(text)
        if pairing:
            hits.append(pairing)
        said = [s for s in sentences(text) if not GREETING.match(s) and not SIGNOFF.match(s)]
        starts = sum(1 for s in said if re.match(r"I\b", s))
        if len(said) >= I_OPENER_MIN and starts / len(said) > I_OPENER_SHARE:
            hits.append(_hit("C11", f"{starts} of {len(said)} sentences"))
    if kind in (LETTER, ANSWER):
        hits += _length(text, kind, max_words, target_words)
    return hits


def _stem(word: str) -> str:
    return re.sub(r"(es|ed|ing|s|ly)$", "", word.lower())


def _cv_lines(text: str) -> list[Hit]:
    lines = [line.strip().lstrip("-* ").strip() for line in text.splitlines() if line.strip()]
    hits = []
    for line in lines:
        n = len(line.split())
        if n > BULLET_WORDS_MAX:
            hits.append(_hit("B1", " ".join(line.split()[:8]) + " ...", f"{n} words; keep a bullet to one idea, "
                                                                        f"at most {BULLET_WORDS_MAX} words."))
        m = WEAK_OPENER.match(line)
        if m:
            hits.append(_hit("B3", m.group(0)))
        if FIRST_PERSON.search(line):
            hits.append(_hit("B4", FIRST_PERSON.search(line).group(0)))
    firsts = [line.split()[0].lower() for line in lines if line.split()]
    for word in sorted(set(firsts)):
        if firsts.count(word) > OPENER_REPEAT_MAX:
            hits.append(_hit("B7", f"{word} ({firsts.count(word)} bullets)"))
    return hits


def high(hits: list[Hit]) -> list[Hit]:
    """The hits that make the writer revise its draft."""
    return [h for h in hits if h.severity == rules.HIGH]


def flagged(hits: list[Hit]) -> list[Hit]:
    """The hits listed under "Check before sending"."""
    return [h for h in hits if h.severity in rules.FLAGGED]


def revision_note(hits: list[Hit]) -> str:
    """The instruction a writer gets to revise its draft once: each problem quoted with its rule."""
    lines = [f'- "{h.quote}": {h.message}' for h in hits]
    return ("Revise the draft once to fix these problems, keeping every fact and the length; change nothing "
            "else:\n" + "\n".join(lines))

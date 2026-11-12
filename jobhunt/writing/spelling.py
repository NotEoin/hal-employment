"""UK and US spelling: the words a text spells the other way, each with its fix, and the text respelt.

`chosen()` is the person's spelling (`cvdocs.spelling`), `find(text, code)` lists the words spelt the
other way with the form to write instead, and `respell(text, code)` applies every fix. The writing checker
(rule L10), the CV checks, the CV suggestions and every draft the AI writes for the person use these, so a
letter, a form answer and a CV line agree on what counts as the other spelling.

Design decisions, each with its reason:

- Only words with one right form on the other side are matched, so every finding carries its fix and a
  draft can be respelt without a reader. Words that are right in UK English too are never flagged:
  "program" (a computer program), "disk", "analog" (electronics), "meter" (an instrument), "license" the
  verb, and the -ise words US English spells -ise as well (advise, exercise, expertise).
- UK spelling means -ise (organise, recognise). Oxford spelling's -ize is UK English too, but most UK
  employers and universities write -ise, and one rule keeps a CV consistent.
- Names, code and quotations are left alone: a capitalised word inside a sentence (the Kennedy Center),
  a word in capitals, a word joined to code (`color_map`, `colorPicker`, `style.color`), anything in
  backticks or double quotes, a web address and an email address.
- "dialog" stays when it names part of a screen ("dialog box", "dialog window"), as UK software writing
  spells it; elsewhere it is "dialogue". "license" is the UK noun "licence" only after a word that makes it
  a noun ("a", "your", "driving"), since the UK verb is "license".
- US spelling turns -ise into -ize only for listed stems, because many -ise words are -ise in US English
  too; the -ize rule the other way needs no list, only the words that end in -size, -prize or -seize.
- An unset, empty or unknown spelling is UK English, the setting's default, so a missing value never
  turns the check off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UK, US = "en-GB", "en-US"
DEFAULT = UK
CODES = (UK, US)
# How a prompt names the spelling: "write in {spelling}." (`cvstudio.context`).
WORDS = {UK: "UK English, with UK spelling (organise, colour, centre, travelled)",
         US: "US English, with US spelling (organize, color, center, traveled)"}
LABELS = {UK: "UK English", US: "US English"}
SIDES = {UK: "US", US: "UK"}        # the spelling a finding is in, by the spelling chosen

# -our words: the US stem (without "or") and the endings both spellings share. Endings that drop the u
# in UK English as well (humorous, honorary, laborious, humoral, humorist) are left out on purpose.
OUR_STEMS = ("col", "behavi", "fav", "hon", "lab", "neighb", "hum", "harb", "flav", "rum", "sav", "vap", "vig",
             "val", "endeav", "od", "arm", "clam", "cand", "ard", "parl", "splend", "tum", "demean", "rig", "ferv",
             "savi")
OUR_ENDINGS = ("", "s", "ed", "ing", "ful", "fully", "less", "able", "ably", "er", "ers", "hood", "hoods", "y")
# Verbs that double a final l before an ending in UK English (travelled, labelled, counsellor).
LL_STEMS = ("travel", "model", "label", "cancel", "fuel", "signal", "level", "total", "channel", "counsel",
            "marvel", "jewel", "quarrel", "tunnel", "funnel", "rival", "dial", "panel", "shovel", "refuel",
            "snorkel", "equal", "barrel", "duel", "enamel", "grovel", "libel", "pedal", "pencil", "shrivel",
            "swivel", "unravel", "yodel", "initial", "spiral", "tassel", "chisel", "bevel")
LL_ENDINGS = ("ed", "ing", "er", "ers", "or", "ors", "ist", "ists")
# Single words, US form first; inverted for US spelling.
PAIRS = (
    ("favorite", "favourite"), ("favorites", "favourites"), ("favoritism", "favouritism"),
    ("behavioral", "behavioural"), ("behaviorally", "behaviourally"), ("behaviorism", "behaviourism"),
    ("center", "centre"), ("centers", "centres"), ("centered", "centred"), ("centering", "centring"),
    ("centerpiece", "centrepiece"), ("centerpieces", "centrepieces"), ("theater", "theatre"),
    ("theaters", "theatres"), ("fiber", "fibre"), ("fibers", "fibres"), ("fiberglass", "fibreglass"),
    ("liter", "litre"), ("liters", "litres"), ("milliliter", "millilitre"), ("milliliters", "millilitres"),
    ("centimeter", "centimetre"), ("centimeters", "centimetres"), ("millimeter", "millimetre"),
    ("millimeters", "millimetres"), ("kilometer", "kilometre"), ("kilometers", "kilometres"),
    ("nanometer", "nanometre"), ("nanometers", "nanometres"), ("meager", "meagre"), ("somber", "sombre"),
    ("specter", "spectre"), ("saber", "sabre"), ("luster", "lustre"), ("caliber", "calibre"),
    ("ocher", "ochre"), ("maneuver", "manoeuvre"), ("maneuvers", "manoeuvres"),
    ("maneuvered", "manoeuvred"), ("maneuvering", "manoeuvring"), ("defense", "defence"),
    ("defenses", "defences"), ("defenseless", "defenceless"), ("offense", "offence"), ("offenses", "offences"),
    ("pretense", "pretence"), ("pretenses", "pretences"), ("catalog", "catalogue"), ("catalogs", "catalogues"),
    ("cataloged", "catalogued"), ("cataloging", "cataloguing"), ("cataloger", "cataloguer"),
    ("fulfill", "fulfil"), ("fulfills", "fulfils"), ("fulfillment", "fulfilment"),
    ("fulfillments", "fulfilments"), ("enroll", "enrol"), ("enrolls", "enrols"), ("enrollment", "enrolment"),
    ("enrollments", "enrolments"), ("installment", "instalment"), ("installments", "instalments"),
    ("skillful", "skilful"), ("skillfully", "skilfully"), ("willful", "wilful"), ("willfully", "wilfully"),
    ("jewelry", "jewellery"), ("marvelous", "marvellous"), ("marvelously", "marvellously"),
    ("woolen", "woollen"), ("gray", "grey"), ("grays", "greys"), ("grayed", "greyed"), ("graying", "greying"),
    ("grayish", "greyish"), ("grayscale", "greyscale"), ("mold", "mould"), ("molds", "moulds"),
    ("molded", "moulded"), ("molding", "moulding"), ("moldy", "mouldy"), ("skeptic", "sceptic"),
    ("skeptics", "sceptics"), ("skeptical", "sceptical"), ("skeptically", "sceptically"),
    ("skepticism", "scepticism"), ("aluminum", "aluminium"), ("plow", "plough"), ("plows", "ploughs"),
    ("plowed", "ploughed"), ("plowing", "ploughing"), ("pajamas", "pyjamas"), ("cozy", "cosy"),
    ("mustache", "moustache"), ("airplane", "aeroplane"), ("airplanes", "aeroplanes"), ("math", "maths"),
    ("checkbook", "chequebook"), ("paycheck", "paycheque"), ("paychecks", "paycheques"),
    ("pediatric", "paediatric"), ("pediatrics", "paediatrics"), ("pediatrician", "paediatrician"),
    ("pediatricians", "paediatricians"), ("anesthetic", "anaesthetic"), ("anesthetics", "anaesthetics"),
    ("anesthesia", "anaesthesia"), ("anesthetist", "anaesthetist"), ("anemia", "anaemia"), ("anemic", "anaemic"),
    ("orthopedic", "orthopaedic"), ("orthopedics", "orthopaedics"),
)
# Pairs that hold one way only: US English writes "cancellation" too, and UK software writing "dialog".
US_ONLY = (("cancelation", "cancellation"), ("cancelations", "cancellations"))
UK_ONLY = (("programme", "program"), ("programmes", "programs"), ("cheque", "check"), ("cheques", "checks"),
           ("licence", "license"), ("licences", "licenses"))
DIALOG_SCREEN = frozenset({"box", "boxes", "window", "windows"})
LICENSE_NOUN_AFTER = frozenset("""
    a an the my your his her their our its this that driver drivers full clean valid current driving provisional
    software music premises teaching pilot practising practicing mit gpl
""".split())
# -ize -> -ise needs no list; these endings are the same in UK English (size, prize, seize, capsize).
IZE = re.compile(r"([a-z]{3,})iz(e|es|ed|ing|er|ers|ation|ations|ational|able|ability)")
IZE_SAME = ("size",)
YZE = re.compile(r"([a-z]{2,})yz(e|es|ed|ing|er|ers)")
# -ise -> -ize only for these stems: many -ise words are -ise in US English too (advise, exercise).
ISE_STEMS = frozenset("""
    agon apolog author capital categor central character civil colon critic custom digit econom emphas energ
    fantas fertil final general global harmon hospital human ideal immun initial item jeopard legal legitim
    liberal local magnet marginal material maxim mechan memor minim mobil modern monet moral national natural
    neutral normal optim organ oxid patron penal personal plagiar polar popular pressur privat public random
    rational real recogn revital revolution sanit satir scandal sensit serial social special stabil standard
    steril stigmat subsid summar symbol sympath synchron terror theor tranquil trivial urban util vandal
    verbal visual vocal
""".split())
ISE = re.compile(r"([a-z]{3,})is(e|es|ed|ing|er|ers|ation|ations|ational|able|ability)")
YSE = re.compile(r"(anal|paral|catal|dial|electrol|hydrol|psychoanal)ys(e|ed|ing|er|ers)")

WORD = re.compile(r"[A-Za-z]+")
# Spans never respelt: code in backticks, quotations, web and email addresses.
KEEP = re.compile(r"`[^`\n]*`|\"[^\"\n]*\"|\u201c[^\u201d\n]*\u201d|https?://\S+|\bwww\.\S+"
                  r"|[\w.+-]+@[\w-]+\.[\w.]+")
CODE_NEXT = set("_/\\@#$%^&*=<>{}[]|~0123456789")
SENTENCE_END = set(".!?:")
BULLETS = ("-", "*", "\u2022")


@dataclass(frozen=True)
class Found:
    """One word spelt the other way: the word as written, where it is and what to write instead."""

    word: str
    fix: str
    start: int
    end: int


def _table() -> dict[str, dict[str, str]]:
    to_uk: dict[str, str] = {}
    for stem in OUR_STEMS:
        to_uk.update({f"{stem}or{end}": f"{stem}our{end}" for end in OUR_ENDINGS})
    for stem in LL_STEMS:
        to_uk.update({f"{stem}{end}": f"{stem}l{end}" for end in LL_ENDINGS})
    to_uk.update(PAIRS)
    to_us = {uk: us for us, uk in to_uk.items()}
    to_uk.update(US_ONLY)
    to_us.update(UK_ONLY)
    return {UK: to_uk, US: to_us}


TABLE = _table()


def code_of(value: object) -> str:
    """"en-GB" or "en-US" for a spelling setting; anything else, unset or empty included, is UK English."""
    key = str(value or "").strip().replace("_", "-").lower()
    return {"en-gb": UK, "en-us": US}.get(key, DEFAULT)


def chosen() -> str:
    """The person's spelling from `cvdocs.spelling`."""
    from jobhunt.core import settings

    return code_of(settings.get("cvdocs.spelling"))


def _word_fix(word: str, code: str, before: str, after: str) -> str | None:
    """The lower-case fix for one lower-case word, or None when it is right (or may be) in `code`."""
    if word in TABLE[code]:
        return TABLE[code][word]
    if code == UK:
        if word in ("dialog", "dialogs"):
            return None if after in DIALOG_SCREEN else word + "ue" if word == "dialog" else "dialogues"
        if word in ("license", "licenses"):
            return word.replace("se", "ce") if before in LICENSE_NOUN_AFTER else None
        m = IZE.fullmatch(word)
        if m and not (m.group(1) + "ize").endswith(IZE_SAME):
            return f"{m.group(1)}is{m.group(2)}"
        m = YZE.fullmatch(word)
        return f"{m.group(1)}ys{m.group(2)}" if m else None
    m = ISE.fullmatch(word)
    if m and m.group(1) in ISE_STEMS:
        return f"{m.group(1)}iz{m.group(2)}"
    m = YSE.fullmatch(word)
    return f"{m.group(1)}yz{m.group(2)}" if m else None


def _starts_sentence(text: str, start: int) -> bool:
    line = text[text.rfind("\n", 0, start) + 1:start].strip().rstrip("\"'([\u2018\u201c").strip()
    return (not line or line in BULLETS or line[-1] in SENTENCE_END or re.fullmatch(r"\d+[.)]", line) is not None)


def _is_code_or_name(text: str, m: re.Match) -> bool:
    word, start, end = m.group(0), m.start(), m.end()
    prev = text[start - 1] if start else ""
    nxt = text[end] if end < len(text) else ""
    if prev in CODE_NEXT or nxt in CODE_NEXT or nxt == "(":
        return True
    # a dot inside a run of letters is code or an address (`style.color`, `color.js`), not a full stop
    if (prev == "." and start > 1 and text[start - 2].isalnum()) or (nxt == "." and text[end + 1:end + 2].isalnum()):
        return True
    if any(c.isupper() for c in word[1:]):
        return True     # capitals (NASA) or inner capitals (colorPicker)
    return word[0].isupper() and not _starts_sentence(text, start)


def _neighbour(text: str, start: int, end: int) -> tuple[str, str]:
    before = re.search(r"([A-Za-z']+)\W*$", text[max(0, start - 40):start])
    after = re.match(r"\W*([A-Za-z']+)", text[end:end + 40])
    return (before.group(1).lower().removesuffix("'s") if before else "",
            after.group(1).lower() if after else "")


def find(text: str, code: object = DEFAULT) -> list[Found]:
    """Each word of `text` spelt the other way from `code`, in order, with the form to write instead."""
    code = code_of(code)
    text = text or ""
    kept = [m.span() for m in KEEP.finditer(text)]
    out = []
    for m in WORD.finditer(text):
        if any(s <= m.start() < e for s, e in kept) or _is_code_or_name(text, m):
            continue
        word = m.group(0)
        fix = _word_fix(word.lower(), code, *_neighbour(text, m.start(), m.end()))
        if fix and fix != word.lower():
            out.append(Found(word, fix.capitalize() if word[0].isupper() else fix, m.start(), m.end()))
    return out


def respell(text: str, code: object = DEFAULT) -> str:
    """`text` with every word spelt the other way from `code` changed to its fix; nothing else changes."""
    if not text:
        return text
    out = text
    for f in reversed(find(text, code)):
        out = out[:f.start] + f.fix + out[f.end:]
    return out

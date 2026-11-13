"""The cover letter: one story a paragraph between a drafted opening and closing, else drafted whole, and
the Tone, Length, Evidence and Checked notes beside it."""
import logging

from .. import cv
from ..profile import writing as profile_writing
from ..writing import (
    drafts,
    eligibility,
    fields,
    flow,
    frame,
    motive,
    opening,
    paragraphs,
    plain,
    rank,
    relocation,
    stage,
)
from ..writing import plan as planning
from .checks import (
    CUT_FLAG,
    CUT_WHAT,
    DANGLING_FLAG,
    ECHO_FLAG,
    _hits,
    check_drafts,
    fix_claims,
    fix_echoes,
    revise_once,
)
from .drafting import _context, _draft, fitting_names, paragraph_scope, story_ranking
from .fitmap import _fit, known_tools
from .reading import (
    SOMEONE,
    Brief,
    _analyse,
    _job_text,
    _kept,
    _requirements,
    evidence_plan,
    field_of,
    passages,
    person,
    previous_applications,
)

log = logging.getLogger(__name__)


NOTES_HEADING = "Letter notes"
NOTE_KEYS = {"Tone": "tone", "Length": "length", "Evidence": "evidence", "Checked": "checked"}
TONE_LABELS = {"plain": "Plain", "formal": "Formal", "warm": "Warm"}   # the setting's choices (catalog/cvdocs.py)
CHECKED_CLEAN = "Every fact is in your CV, record or the advert. No numbers were invented."
REVISED = "Hal revised it once for its writing rules. "   # before the Checked line when the first draft broke one
SOFTENED = "Hal rewrote it once to take out claims your facts do not support. "   # ... when it made such claims
# A letter left short after its sentence checks and the one added paragraph (`flow.short`).
SHORT_FLAG = ("the letter is {n} words, well under the {words} you prefer; add a paragraph of your own evidence "
              "before you send it")
EVIDENCE_SHOWN = 3        # pieces of evidence named on the letter's Evidence line


def _letter(who: dict, context: str, trimmed: set[str] | None = None) -> str:
    return _draft(who, "letter", context, set() if trimmed is None else trimmed)


def letter_notes(who: dict, letter: str, evidence: list[str], flags: list[str], *, revised: bool = False,
                 softened: bool = False) -> dict:
    """The Tone, Length, Evidence and Checked lines shown beside a letter; `revised` when the writer had to
    revise it once for the writing rules, `softened` when it rewrote claims the facts do not support."""
    from ..core import settings

    tone = TONE_LABELS.get(settings.get("cvdocs.letter_tone"), who["tone"].capitalize())
    letter_flags = [f for f in flags if not f.startswith(("REAPPLICATION", "ELIGIBILITY"))]
    # the fit map's evidence is sentences ("... spreadsheet.", 2026-10-23), so the line joins them with ";"
    shown = [e.strip().rstrip(".") for e in evidence[:EVIDENCE_SHOWN] if e.strip()]
    return {"tone": f"{tone} (your setting)",
            "length": f"{len(letter.split())} words (you prefer about {who['letter_words']})",
            "evidence": "; ".join(shown) or "None named",
            "checked": (REVISED if revised else "") + (SOFTENED if softened else "") + (
                CHECKED_CLEAN if not letter_flags else
                f"{len(letter_flags)} {'thing' if len(letter_flags) == 1 else 'things'} to check before "
                "sending: see Check before sending.")}


def letter_frame(brief: Brief, scope: paragraphs.Scope, pairing: motive.Pairing | None,
                 told: tuple[str, ...], modules: tuple[str, ...] = (),
                 middle: tuple[tuple[str, str], ...] = ()) -> frame.Facts:
    """The fixed facts of a letter's opening and closing: the person's name and course (`opening.described`), the
    role, the reason for this employer, the names of the work the middle tells, the `modules` the job's
    requirements name that no paragraph shows (`plan.relevant_modules`) and the middle's paragraphs, (name,
    text) each, which the frame must not retell (`frame.retold`)."""
    name = str(brief.who.get("name") or "")
    now = scope.now or stage.current()
    return frame.Facts("" if name == SOMEONE else name, opening.described(now), brief.job["title"],
                       brief.job["company"],
                       pairing, told, now.institution, modules, middle)


LETTER_TRIES = 3           # stories past the chosen ones a letter tries: each costs one to three calls
SHORT_MIDDLE = 0.5         # share of the length setting under which a letter tries further stories


def _short_middle(bodies: list[paragraphs.Paragraph], n: int, words: int | None) -> bool:
    """True when a letter's paragraphs that stand are fewer than the `n` it chose, or they and the opening and
    closing (`paragraphs.FRAME_WORDS`) come to under `SHORT_MIDDLE` of the length setting `words`."""
    standing = [b.text for b in bodies if b.text]
    said = sum(flow.word_count(t) for t in standing) + paragraphs.FRAME_WORDS
    return len(standing) < n or (bool(words) and said < SHORT_MIDDLE * int(words))


def _letter_bodies(chosen: list[planning.Story], spare: list[planning.Story], shape: paragraphs.Shape,
                   scope: paragraphs.Scope, words: int | None) -> list[paragraphs.Paragraph]:
    """The letter's paragraphs: one per `chosen` story, then, while one was dropped or the letter is under half
    its length setting (`_short_middle`), the next `spare` project or job story with facts, up to
    `LETTER_TRIES` of them. A letter came out at 56 words when three paragraphs were dropped (2026-11-08)."""
    bodies = [paragraphs.write_paragraph(s, shape, scope) for s in chosen]
    left = [s for s in spare if s not in chosen and s.kind in (planning.PROJECT, planning.JOB) and s.facts]
    for story in left[:LETTER_TRIES]:
        if not _short_middle(bodies, len(chosen), words):
            break
        log.info("letter: one more story, %s, as the letter is short of paragraphs or words", story.name or story.key)
        bodies.append(paragraphs.write_paragraph(story, shape, scope))
    return bodies


def _story_letter(con, brief: Brief, scope: paragraphs.Scope, *, plan: list[planning.Item],
                  pairing: motive.Pairing | None, chosen: list[planning.Story], wanted: str = "",
                  spare: list[planning.Story] = ()) -> dict:
    """A letter told one piece of work a paragraph (`writing.paragraphs`): the `chosen` stories (`rank.order`),
    each drafted from its own facts and checked against them, at a length that fills the person's letter length
    (`paragraphs.letter`), and the next of `spare` (`rank.Told.others`) when one is dropped or the letter is short
    (`_letter_bodies`), between an opening and a closing drafted from the fixed facts of who is writing, the
    role and the reason for this employer (`letter_frame`, `writing.frame`); `wanted` is the job's requirements,
    for the modules the opening may name."""
    who, facts, kept = brief.who, brief.facts, brief.kept
    shape = paragraphs.letter(who.get("letter_words"), len(chosen))
    bodies = _letter_bodies(chosen, list(spare), shape, scope, who.get("letter_words"))
    told = tuple(b.story.name for b in bodies if b.text and b.story.name)
    course = scope.now.institution if scope.now else ""
    modules = planning.relevant_modules(profile_writing.marked_modules(institution=course), wanted,
                                        "\n".join(b.story.words() for b in bodies if b.text))
    middle = tuple((b.story.name, b.text) for b in bodies if b.text)
    facts_said = letter_frame(brief, scope, pairing, told, modules, middle)
    framed = frame.write_frame(facts_said, scope)
    cards = list(scope.cards)
    text = "\n\n".join(p for p in (framed.opening, *(b.text for b in bodies), framed.closing) if p)
    letter = plain.tidy(relocation.without_own_move(text, eligibility.person(con)), cards)
    flags = check_drafts(brief, letter, sources=(scope.advert, scope.brief, facts_said.words(), scope.allowed),
                         claims=[])
    flags += [b.listed for b in bodies if b.dropped]
    flags += [line for b in bodies for line in b.checks]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(letter)]
    if flow.short(letter, who.get("letter_words")):
        flags.append(SHORT_FLAG.format(n=flow.word_count(letter), words=who["letter_words"]))
    softened = any(b.redrafted or b.cut for b in bodies) or framed.redrafted
    shown = [_item_note(i, cards) for i in plan]
    shown = [e for e in (drafts.evidence_checked(s, cards, known_tools(cards), facts)[0] for s in shown) if e]
    told_keys = [b.story.key for b in bodies if b.text]
    return {"text": letter, "flags": flags, "used": passages(kept), "revised": False, "softened": softened,
            "plan": plan, "pairing": pairing, "told": list(dict.fromkeys(told_keys)),
            "calls": framed.calls + sum(b.calls for b in bodies),
            "notes": letter_notes(who, letter, shown, flags, softened=softened)}


def write_letter(con, job, *, who: dict | None = None, analysis: dict | None = None, about: str = "",
                 kept: str | None = None, fit: dict | None = None, request: str = "",
                 plan: list[planning.Item] | None = None, ranking: list[rank.Ranked] | None = None) -> dict:
    """Writes and checks a cover letter for one job: {text, flags, used, notes, plan, pairing, ranking}.

    The same path for a pack and for a letter drafted at form time: the advert read, the record searched,
    the fit map made and the evidence plan picked (`evidence_plan`), each unless given, then the letter and its
    checks. `request` holds the person's own words when they asked for a rewrite. Its reason for the employer
    comes from a motivation pairing (`writing.motive`), or it gives none. When the plan or a starred card tells
    any piece of work (`plan.stories`, `plan.with_showcase`), the letter is one paragraph per piece of work
    (`_story_letter`), the work chosen by the `ranking` (`story_ranking`, one AI call made here unless given).
    Otherwise it is drafted whole, revised once for the writing rules and once for restating the advert, and
    checked whole for claims the facts do not support (`fix_claims`).
    """
    who = who or person(job)
    facts = cv.facts_text()
    analysis = analysis if analysis is not None else _analyse(job, who)
    kept = kept if kept is not None else _kept(analysis)
    fit = fit if fit is not None else _fit(who, analysis, facts, kept, job)
    plan = plan if plan is not None else evidence_plan(analysis, facts, kept)
    brief = Brief(job, who, analysis, about, facts, kept)
    names = (job["title"], job["company"])
    cards = profile_writing.cards()
    found = planning.with_showcase(planning.stories(plan, cards), cards)
    if found:
        scope = paragraph_scope(brief, found=found, cards=cards, request=request, kind="letter")
        asked = ranking is None
        ranking = story_ranking(brief, found, cards) if asked else ranking
        wanted = "\n".join(_requirements(analysis))
        told = rank.order(found, cards, ranking, wanted)
        pairing = motive.make(names, _job_text(job), about, plan, fitting_names(ranking))
        written = _story_letter(con, brief, scope, plan=plan, pairing=pairing, chosen=list(told.letter),
                                wanted=wanted, spare=list(told.others))
        return {**written, "ranking": ranking, "calls": written["calls"] + asked}
    pairing = motive.make(names, _job_text(job), about, plan)
    text = _context(brief, fit, previous_applications(con, job))
    text += f"\n\n{motive.block(pairing)}" if pairing is not None else ""
    text += "\n\n" + fields.block(field_of(job, who, analysis), "letter")
    if request:
        text += f"\n\nThe applicant asked for this change: {request}"
    trimmed: set[str] = set()
    sources = (_job_text(job), about)

    def echoed_in(draft: str) -> list[str]:
        return drafts.echoed(drafts.first_paragraph(draft), *sources, names)

    def again(note: str) -> str:
        return _letter(who, f"{text}\n\nYour draft:\n{letter}\n\n{note}", trimmed)

    letter = _letter(who, text, trimmed)
    letter, revised = revise_once(letter, _hits(brief, letter, "letter", sources),
                                  lambda note: _letter(who, f"{text}\n\nYour first draft:\n{letter}\n\n{note}",
                                                       trimmed))
    letter, _ = fix_echoes(letter, echoed_in, again)
    letter, claims, softened = fix_claims(brief, letter, again, employer=sources)
    cut = letter in trimmed
    letter = plain.tidy(relocation.without_own_move(letter, eligibility.person(con)), cards)
    flags = check_drafts(brief, letter, sources=sources, claims=claims)
    flags += [f"{ECHO_FLAG}: {s}" for s in echoed_in(letter)]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(letter)]
    if flow.short(letter, who.get("letter_words")):
        flags.append(SHORT_FLAG.format(n=flow.word_count(letter), words=who["letter_words"]))
    if cut:
        flags.append(CUT_FLAG.format(what=CUT_WHAT["letter"]))
    used = passages(kept)
    shown = [_item_note(i, cards) for i in plan] or used or [m.get("evidence", "") for m in fit.get("matches", [])
                                                            if m.get("strength") == "strong"]
    shown = [e for e in (drafts.evidence_checked(s, cards, known_tools(cards), facts)[0] for s in shown) if e]
    return {"text": letter, "flags": flags, "used": used, "revised": revised, "softened": softened, "plan": plan,
            "pairing": pairing, "ranking": [],
            "notes": letter_notes(who, letter, shown, flags, revised=revised, softened=softened)}


def _item_note(item: planning.Item, cards: list[dict]) -> str:
    """A plan item as the letter's Evidence line shows it: its quote, with its card and tag after it when the
    card's name is like another's and the quote does not name it."""
    card = next((c for c in cards if c["name"] == item.card), None)
    if not item.tag or card is None or card in drafts.named_cards(item.text, cards):
        return item.text
    return f"{item.text} ({item.card}, {item.tag})"

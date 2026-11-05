"""The suggestion engine for Hal's copy of the CV: reviewers, guards and the Accept loop.

Reviewers run one section at a time in the CV type's order, then the whole CV. Each first runs checks that
need no AI (spelling against `cvdocs.spelling`, symbols job-site scanners drop, date formats, banned and
never-use phrases, entries over the type's limit, empty sections, section order), then asks the `cv_suggest`
prompt for at most one more suggestion for that section. So suggestions appear with no AI set up at all.

Every suggestion passes the same guards before it is stored: the target exists and is not locked or kept
in the person's wording, `before` is the text there now, numbers, years and qualifications Hal cannot find
become gaps (`[how many?]`, `[when?]`, `[which?]`), no banned phrase, not a fingerprint the person already
left, no I or we when the type writes without them, and it fits the type's limits. Any bracketed question
counts as a gap, whoever wrote it, so a gap is never accepted into the CV or sent in a job's CV.

Design decisions:
- One suggestion per line. A spelling fix covers every word on its line, so accepting one never makes
  another suggestion's `before` stale, and "Accept all" can apply a kind in one version.
- The AI's wordings are respelt to the person's spelling (`writing.spelling`) before the guards, so a
  suggestion never brings in the other spelling; the person's own lines get a spelling suggestion instead.
- `before` and each `after` are what the card shows. For a reorder they are the names in order, which
  are resolved back to ids against the version being changed, so a renamed part is caught by the guard.
- The store's functions open their own connection, so this module commits its own rows before and after
  each store call and never holds a write transaction across one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field

from jobhunt.core import clock, events

log = logging.getLogger(__name__)

MAX_PENDING = 6                     # "Look again" stops when this many wait
AI_PRIORITY = 1                     # the writer role at priority 1: documents for jobs you chose
DETERMINISTIC_KINDS = ("Consistency", "Symbols", "Dates")   # the kinds "Accept all" works for
KINDS = ("Wording", "Results", "Order", "Cut", "Consistency", "Symbols", "Dates", "Add", "Gap")
OPS = ("replace", "insert", "delete", "move", "hide", "reorder", "retitle")
REASONS = ("Keep my wording", "This is not true", "Too formal", "Too long", "Not relevant")
GAPS = {"number": "[how many?]", "year": "[when?]", "qualification": "[which?]"}
# I, my, we in a CV line; Claude wrote "applied their feedback to [how many?] of my changes" (2026-10-23)
FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|me|[Mm]y|[Ww]e|[Oo]ur)\b")
# Hal's own gaps, and any bracketed question the AI writes instead: Claude wrote "[what outcome?]" and
# "[which languages, and what level?]" on 2026-10-23, and a gap of any wording must stop Accept.
GAP = re.compile(r"\[[^\[\]\n]{1,80}\?\]")
META_REVIEWED = "cv_suggest_reviewed"       # the version id a full pass last found nothing more in
DOC_ID = "main"
WHOLE_CV = "cv"     # a suggestion's target and reviewer id for the whole CV; the model's own target is ""

ALL_REVIEWED = "All reviewed. Hal will suggest more when your CV, its type or your goal changes."
ONLY_OPTION = "This is the only sensible option here. Edit it instead?"
CHANGED_SINCE = "This has changed since. Edit it instead?"
FILL_GAP = "Fill in the gap first: Hal does not know this, so it left {gap} for you."
DECIDED = "You already decided on this suggestion."

# The reviewers' order; experience and volunteering are read together.
REVIEW_ORDER = ("summary", ("experience", "volunteering"), "projects", "skills", "interests")
REVIEW_FIRST = ("summary", "education")

# Characters job-site scanners drop or misread, with what to write instead ("" drops them).
SYMBOLS = {"\u2605": "", "\u2606": "", "\u2713": "", "\u2714": "", "\u2192": "-", "\u27a4": "", "\u25ba": "",
           "\u25cf": "", "\u2022": "", "\u25aa": "", "\u2794": "-", "\u00bb": "", "\u2756": ""}
EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u26ff]")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{4})\s*$|^\s*(\d{4})-(\d{1,2})\s*$")
YEAR = re.compile(r"^(19|20)\d{2}$")
QUALIFICATION = re.compile(r"\b(First|2:1|2:2|Third|Distinction|Merit|BA|BSc|MA|MSc|MPhil|PhD|LLB|PGCE|"
                           r"A[- ]levels?|GCSEs?|BTEC|HND)\b")
NUMBER = re.compile(r"\d[\d,.]*")


@dataclass
class Draft:
    """A suggestion before it is stored."""

    target: str
    op: str
    kind: str
    title: str
    why: str
    trigger: dict
    before: str
    afters: list[str]
    section_id: str = ""
    evidence: list[dict] = field(default_factory=list)
    ai: str | None = None

    @property
    def fingerprint(self) -> str:
        """sha1(target + trigger id + normalised first after): the same advice made again has the same one."""
        return fingerprint(self.target, self.trigger.get("id", ""), self.afters[0] if self.afters else "")


def fingerprint(target: str, trigger_id: str, after: str) -> str:
    """The suggestion's identity, so a suggestion the person left never comes back."""
    norm = " ".join(after.lower().split())
    return hashlib.sha1(f"{target}\n{trigger_id}\n{norm}".encode()).hexdigest()


# -- the CV model and CV types, imported when used so this module loads before they land -------------------------

def _model():
    from jobhunt.cvstudio import model

    return model


def _store():
    from jobhunt.cvstudio import store

    return store


def _types():
    from jobhunt.cvstudio import types

    return types


def _setting(key: str):
    from jobhunt.core import settings

    return settings.get(key)


def cv_type(row: dict | None = None):
    """The CV type Hal's copy uses now."""
    row = row or _store().doc()
    return _types().load(row["type_id"])


# -- reading the document ---------------------------------------------------------------------------------

def _section(doc, sid: str):
    return next((s for s in doc.sections if s.id == sid), None)


def model_target(target: str) -> str:
    """The CV model's target for a suggestion's target (`WHOLE_CV` is the whole document, "")."""
    return "" if target == WHOLE_CV else target


def names_at(doc, target: str) -> list[str]:
    """The names a reorder works on, in their current order."""
    node = _model().find(doc, model_target(target))
    if target == WHOLE_CV:
        return [s.title for s in node.sections if s.visible]
    if hasattr(node, "entries"):
        return [e.heading or e.org for e in node.entries if not e.hidden]
    return list(node.items)


def shown_at(doc, op: str, target: str) -> str:
    """What the card's "Now" shows for this target: the text, the names in order, or the part's name."""
    if op == "reorder":
        return ", ".join(names_at(doc, target))
    node = _model().find(doc, model_target(target))
    if op == "hide":
        if hasattr(node, "visible"):
            return node.title if node.visible else ""
        return "" if node.hidden else (node.heading or node.org)
    return node.text if hasattr(node, "text") and not isinstance(node, str) else str(node)


def where(doc, target: str) -> str:
    """Plain words for where a target is: "Volunteering, Archive Volunteer"."""
    if target == WHOLE_CV:
        return "Sections"
    parts = target.split("/")
    sec = _section(doc, parts[0].partition(":")[2])
    if sec is None:
        return "Whole CV"
    if len(parts) > 1 and parts[1].startswith("e:"):
        ent = next((e for e in sec.entries if e.id == parts[1][2:]), None)
        if ent is not None:
            return f"{sec.title}, {ent.heading or ent.org}"
    return sec.title


def lines(doc) -> list[tuple[str, str, str]]:
    """Every line of text with its target and section id: profile, paragraphs and bullets."""
    out = []
    for s in doc.sections:
        if not s.visible:
            continue
        if s.text:
            out.append((f"s:{s.id}/text", s.text, s.id))
        for e in s.entries:
            if e.hidden:
                continue
            if e.text:
                out.append((f"s:{s.id}/e:{e.id}/text", e.text, s.id))
            out += [(f"s:{s.id}/e:{e.id}/b:{b.id}", b.text, s.id) for b in e.bullets]
    return out


def _locked(doc, target: str, layout: dict) -> bool:
    if target in set(layout.get("locked_lines") or []):
        return True
    if target == WHOLE_CV:
        return any(s.locked for s in doc.sections)
    model = _model()
    parts = target.split("/")
    for i in range(1, len(parts) + 1):
        try:
            node = model.find(doc, "/".join(parts[:i]))
        except model.CVError:
            return True
        if getattr(node, "locked", False):
            return True
    locked_sections = {x.get("id") for x in layout.get("sections") or [] if x.get("lock")}
    return parts[0].partition(":")[2] in locked_sections


# -- the checks that need no AI ---------------------------------------------------------------------------

def _spelling(doc, sid: str) -> list[Draft]:
    from jobhunt.writing import spelling

    code = spelling.chosen()
    label = spelling.LABELS[code]
    out = []
    for target, text, s in lines(doc):
        found = spelling.find(text, code) if s == sid else []
        if not found:
            continue
        first = found[0]
        out.append(Draft(target, "replace", "Consistency", f'Use one spelling: "{first.fix.lower()}"',
                         f'This line uses the {spelling.SIDES[code]} spelling "{first.word}". Your settings say '
                         f'{label}.',
                         {"type": "setting", "id": "cvdocs.spelling", "label": f"Setting: spelling is {label}"},
                         text, [spelling.respell(text, code)], sid,
                         [{"label": f'Found: "{first.word}"', "ref": target}]))
    return out


def _symbols(doc, sid: str) -> list[Draft]:
    out = []
    for target, text, s in lines(doc):
        if s != sid:
            continue
        fixed = EMOJI.sub("", "".join(SYMBOLS.get(ch, ch) for ch in text))
        fixed = re.sub(r"\s{2,}", " ", fixed).strip()
        if fixed == text or not fixed:
            continue
        out.append(Draft(target, "replace", "Symbols", "Use characters job sites can read",
                         "Job-site scanners drop or misread symbols such as stars, ticks, arrows and emoji.",
                         {"type": "check", "id": "symbols_kept",
                          "label": 'How job-site scanners read it: "Symbols scanners keep"'}, text, [fixed], sid))
    return out


def _date(text: str) -> str | None:
    m = NUMERIC_DATE.match(text or "")
    if not m:
        return None
    month, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    return f"{MONTHS[int(month) - 1]} {year}" if 1 <= int(month) <= len(MONTHS) else None


def _dates(doc, sid: str) -> list[Draft]:
    sec = _section(doc, sid)
    out = []
    for e in sec.entries if sec and sec.visible else []:
        for attr in ("start", "end"):
            fixed = _date(getattr(e, attr))
            if fixed:
                out.append(Draft(f"s:{sid}/e:{e.id}/{attr}", "replace", "Dates", "Write dates the same way",
                                 "Month names read the same to people and to job-site scanners.",
                                 {"type": "check", "id": "dates_one_format",
                                  "label": 'Check for your CV type: "Dates in one format"'},
                                 getattr(e, attr), [fixed], sid))
    return out


def banned_phrases(type_) -> list[str]:
    """The type's banned phrases and the person's never-use list, longest first."""
    own = [str(x) for x in _setting("cvdocs.never_use") or []]
    words = {w.strip().lower() for w in [*(type_.writing.get("banned") or []), *own] if w.strip()}
    return sorted(words, key=len, reverse=True)


def _without(text: str, phrase: str) -> str:
    out = re.sub(r",?\s*\b" + re.escape(phrase) + r"\b,?", " ", text, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([,.;])", r"\1", out)
    out = re.sub(r"^[,;\s]+", "", out)
    return out[:1].upper() + out[1:] if out else out


def _banned(doc, sid: str, type_) -> list[Draft]:
    out = []
    phrases = banned_phrases(type_)
    for target, text, s in lines(doc):
        if s != sid:
            continue
        found = [p for p in phrases if re.search(r"\b" + re.escape(p) + r"\b", text, re.I)]
        if not found:
            continue
        fixed = text
        for p in found:
            fixed = _without(fixed, p)
        if len(fixed.split()) < 3:
            continue
        listed = '", "'.join(found)
        out.append(Draft(target, "replace", "Wording", f'Leave out "{found[0]}"',
                         f'Recruiters skim for evidence, and "{listed}" says a quality without showing it.',
                         {"type": "type_rule", "id": "banned", "label": "Your CV type: words to avoid"},
                         text, [fixed], sid))
    return out


def _limits(doc, sid: str, type_) -> list[Draft]:
    sec = _section(doc, sid)
    if sec is None or not sec.visible:
        return []
    rules = type_.sections.get(sid) or type_.sections.get(sec.kind) or {}
    shown = [e for e in sec.entries if not e.hidden]
    if not sec.text and not shown:
        return [Draft(f"s:{sid}", "hide", "Cut", f"Leave out the empty {sec.title} section",
                      "An empty heading makes a reader look for something that is not there.",
                      {"type": "check", "id": "empty_section", "label": "Section check: no empty sections"},
                      sec.title, [""], sid)]
    most = sec.max_entries or rules.get("max_entries")
    if most and len(shown) > most:
        last = shown[-1]
        name = last.heading or last.org
        return [Draft(f"s:{sid}/e:{last.id}", "hide", "Cut", f"Keep {sec.title} to {most} entries",
                      f"Your CV type shows at most {most} entries here. {name} is the last one.",
                      {"type": "type_rule", "id": f"max_entries:{sid}",
                       "label": f"Your CV type: at most {most} in {sec.title}"}, name, [""], sid)]
    return []


def _order(doc, type_) -> list[Draft]:
    order = list(type_.section_order)
    rank = {sid: i for i, sid in enumerate(order)}
    shown = [s for s in doc.sections if s.visible]

    def place(s) -> int:
        return rank.get(s.id, rank.get(s.kind, len(order)))
    wanted = sorted(shown, key=place)
    if [s.id for s in wanted] == [s.id for s in shown]:
        return []
    moved = next(s for s, w in zip(shown, wanted, strict=True) if s.id != w.id)
    first = wanted[[s.id for s in wanted].index(moved.id) - 1] if moved is not wanted[0] else wanted[0]
    return [Draft(WHOLE_CV, "reorder", "Order", "Put your sections in the order your CV type uses",
                  f"Your CV type, {type_.name}, puts {first.title} earlier. Readers look for it there.",
                  {"type": "type_rule", "id": "section_order", "label": f"Your CV type: {type_.name}"},
                  ", ".join(s.title for s in shown), [", ".join(s.title for s in wanted)], WHOLE_CV)]


def checks_for(doc, sid: str, type_) -> list[Draft]:
    """The suggestions for one section that need no AI."""
    if sid == WHOLE_CV:
        return _order(doc, type_)
    return [*_spelling(doc, sid), *_symbols(doc, sid), *_dates(doc, sid), *_banned(doc, sid, type_),
            *_limits(doc, sid, type_)]


def reviewers(doc, type_) -> list[list[str]]:
    """The groups of section ids reviewed together, in order, ending with the whole CV."""
    known = {s.id for s in doc.sections}
    groups: list[list[str]] = []
    for want in (*REVIEW_FIRST, *REVIEW_ORDER[1:]):
        ids = list(want) if isinstance(want, tuple) else [want]
        found = [s.id for s in doc.sections if s.id in ids or s.kind in ids]
        if found:
            groups.append(found)
    seen = {sid for g in groups for sid in g}
    rest = [s.id for s in doc.sections if s.id not in seen and s.id in known]
    groups += [[sid] for sid in rest]
    return [*groups, [WHOLE_CV]]


# -- guards -----------------------------------------------------------------------------------------------

@dataclass
class Context:
    """What the guards check a suggestion against."""

    doc: object
    type_: object
    layout: dict
    known_text: str                          # the CV, record passages and what the person said
    blocked: set[str] = field(default_factory=set)       # fingerprints the person left or that wait already
    keep: set[str] = field(default_factory=set)          # targets kept in the person's wording or locked
    kinds_off: set[tuple[str, str]] = field(default_factory=set)   # (kind, section id) the person turned off


def with_gaps(after: str, known_text: str) -> str:
    """`after` with every number, year or qualification not found in `known_text` replaced by a gap."""
    from jobhunt.pack import unsupported_numbers

    missing = set(unsupported_numbers(after, known_text))

    def number(m: re.Match) -> str:
        n = m.group(0)
        core = n.rstrip(".,")
        if n not in missing and core not in missing:
            return n
        return (GAPS["year"] if YEAR.match(core) else GAPS["number"]) + n[len(core):]
    out = NUMBER.sub(number, after)
    known = known_text.lower()
    return QUALIFICATION.sub(lambda m: m.group(0) if m.group(0).lower() in known else GAPS["qualification"], out)


def _fits(draft: Draft, after: str, type_) -> bool:
    if draft.op != "replace":
        return True
    words = len(after.split())
    sid = draft.section_id
    rules = type_.sections.get(sid) or {}
    if draft.target.endswith("/text") and "/e:" not in draft.target and rules.get("max_words"):
        return words <= int(rules["max_words"])
    span = type_.writing.get("bullet_words") or []
    return "/b:" not in draft.target or not span or words <= int(span[1])


def first_person(text: str, type_) -> bool:
    """True when `text` speaks as I or we and the CV type writes with no first person."""
    return (type_.writing or {}).get("person") == "none" and FIRST_PERSON.search(text) is not None


def _has(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text, re.I) is not None


def guard(draft: Draft, ctx: Context) -> Draft | None:
    """The draft with gaps filled in when it passes every guard, else None."""
    from jobhunt.writing import spelling

    model = _model()
    if draft.op not in OPS or draft.kind not in KINDS or not draft.afters:
        return None
    try:
        now = shown_at(ctx.doc, draft.op, draft.target)
    except (model.CVError, AttributeError, StopIteration):
        return None
    if draft.target in ctx.keep or _locked(ctx.doc, draft.target, ctx.layout):
        return None
    if " ".join(now.split()) != " ".join((draft.before or "").split()):
        return None
    if (draft.kind, draft.section_id) in ctx.kinds_off:
        return None
    afters = []
    phrases = banned_phrases(ctx.type_)
    code = spelling.chosen() if draft.ai else None
    for after in draft.afters:
        after = with_gaps(str(after), ctx.known_text) if draft.op == "replace" else str(after)
        if code:
            after = spelling.respell(after, code)
        if any(_has(after, p) and not _has(now, p) for p in phrases):
            continue
        # a fix that needs no AI keeps the person's own I; the AI's wording never speaks as I
        if first_person(after, ctx.type_) and (draft.ai or not first_person(now, ctx.type_)):
            continue
        if not _fits(draft, after, ctx.type_) or after in afters or after == now:
            continue
        afters.append(after)
    if not afters:
        return None
    draft.afters = afters
    if draft.fingerprint in ctx.blocked:
        return None
    return draft


def context(con: sqlite3.Connection, doc, type_, *, passages: str = "") -> Context:
    """The guards' view: locks, the person's preferences, fingerprints to skip and the known facts."""
    store, model = _store(), _model()
    blocked = {r[0] for r in con.execute(
        "SELECT fingerprint FROM cv_suggestions WHERE doc_id = ? AND job_id IS NULL AND status IN ('left', 'pending')",
        (DOC_ID,))}
    keep, off = set(), set()
    for r in con.execute("SELECT kind, target, text FROM cv_preferences WHERE active = 1"):
        if r["kind"] in ("keep_wording", "lock_line") and r["target"]:
            keep.add(r["target"])
        elif r["kind"] == "prefer" and str(r["text"]).startswith("kind_off:") and r["target"]:
            off.add((r["text"].partition(":")[2], r["target"].partition(":")[2]))
    said = " ".join(r[0] for r in con.execute(
        "SELECT edited FROM cv_suggestions WHERE edited IS NOT NULL AND doc_id = ?", (DOC_ID,)))
    profile = _profile()
    known = "\n".join([model.plain_text(doc, profile), passages, said])
    return Context(doc, type_, store.layout(), known, blocked, keep, off)


def _profile() -> dict:
    from jobhunt.core import settings

    return {"name": settings.get("you.name") or ""}


# -- the AI reviewer --------------------------------------------------------------------------------------

def passages_for(doc, sids: list[str]) -> list[dict]:
    """Record passages about these sections' entries; none when the record cannot be searched."""
    from jobhunt import record

    queries = []
    for sid in sids:
        sec = _section(doc, sid)
        if sec is not None:
            queries += [" ".join(x for x in (e.heading, e.org) if x) for e in sec.entries if not e.hidden][:3]
    if not queries:
        return []
    try:
        found = record.search_many(queries, k=2)
    except Exception as e:  # a missing or unreadable record means fewer facts, not no suggestions
        log.info("record search for CV suggestions failed: %r", e)
        return []
    return [p for hits in found for p in hits]


def _prefs_words(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT text FROM cv_preferences WHERE active = 1 AND kind = 'prefer' "
                                      "AND text NOT LIKE 'kind_off:%'")]


def _section_block(doc, sids: list[str]) -> str:
    out = []
    for target, text, sid in lines(doc):
        if sid in sids:
            out.append(f"{target}\t{text}")
    return "\n".join(out)


def ai_ready() -> bool:
    """True when the AI can write a suggestion now."""
    from jobhunt import ai

    return ai.ready(AI_PRIORITY, "cv_suggest") is None


def spelling_words() -> str:
    """The person's spelling as the CV prompts name it ("UK English, with UK spelling ...")."""
    from jobhunt.writing import spelling

    return spelling.WORDS[spelling.chosen()]


def writing_problems(block: str, type_) -> list[str]:
    """What the writing checker finds in a section's bullet lines, as "<target>: <problem>" lines."""
    from jobhunt.writing import check as writing_check
    from jobhunt.writing import spelling as spelling_mod

    out = []
    spelling = spelling_mod.chosen()   # the setting, as the prompt is told; the type's own is only a default
    for row in block.splitlines():
        target, _, text = row.partition("\t")
        if "/b:" not in target:
            continue
        out += [f"{target}: {h.flag}. {h.message}" for h in writing_check.flagged(writing_check.check(text, "cv",
                                                                                           spelling=spelling))]
    return out


def field_conventions(job: dict | None = None) -> list[str]:
    """The CV conventions of the person's field, or of the job's when one is given (`writing.fields`)."""
    from jobhunt.writing import fields

    if not job:
        return fields.conventions(fields.resolve("", "", fields.person_now()), "cv")
    advert = " ".join([str(job.get("description") or ""), *(str(r) for r in job.get("requirements") or [])])
    resolved = fields.resolve(str(job.get("title") or ""), advert, fields.person_now())
    return fields.conventions(resolved, "cv")


def ask_ai(con: sqlite3.Connection, doc, type_, sids: list[str], failing: list[Draft], passages: list[dict],
           *, job: dict | None = None) -> Draft | None:
    """At most one suggestion for these sections from the `cv_suggest` prompt; None for `{"none": true}`."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    block = _section_block(doc, sids)
    if not block:
        return None
    extra = str((type_.prompts or {}).get("cv_suggest") or "").strip()
    system = prompts.text("cv_suggest", con=con, spelling=spelling_words()) + (f"\n{extra}\n" if extra else "")
    user = json.dumps({
        "cv_type": type_.name, "writing": type_.writing, "lines": block,
        "failing_checks": [d.title for d in failing], "writing_problems": writing_problems(block, type_),
        "field_conventions": field_conventions(job), "preferences": _prefs_words(con),
        "record": [{"label": f"{p.get('path', '')} > {p.get('heading', '')}", "text": p.get("text", "")}
                   for p in passages],
        "job": {"title": job.get("title"), "requirements": job.get("requirements")} if job else None,
    }, ensure_ascii=True)
    try:
        got = ai.chat_json(system, user, role="writer", purpose="cv_suggest", priority=AI_PRIORITY)
    except ai.LLMUnavailable as e:
        log.info("no AI suggestion for %s: %s", sids, e)
        return None
    return draft_from_ai(got, sids, passages)


def draft_from_ai(got: dict, sids: list[str], passages: list[dict]) -> Draft | None:
    """A draft from the prompt's JSON answer, or None when it said none or is not usable."""
    if not isinstance(got, dict) or got.get("none") or not got.get("target"):
        return None
    afters = got.get("afters") or ([got["after"]] if got.get("after") else [])
    trigger = got.get("trigger") if isinstance(got.get("trigger"), dict) else {}
    evidence = [{"label": str(x), "ref": ""} if not isinstance(x, dict) else
                {"label": str(x.get("label", "")), "ref": str(x.get("ref", ""))} for x in got.get("evidence") or []]
    target = str(got["target"])
    sid = target.split("/")[0].partition(":")[2] if target != WHOLE_CV else WHOLE_CV
    if sid not in sids:
        return None
    return Draft(target, str(got.get("op") or "replace"), str(got.get("kind") or "Wording"),
                 str(got.get("title") or "A clearer way to say this"), str(got.get("why") or ""),
                 {"type": str(trigger.get("type") or "check"), "id": str(trigger.get("id") or "writing"),
                  "label": str(trigger.get("label") or "Your CV type's writing rules")},
                 str(got.get("before") or ""), [str(a) for a in afters[:3]], sid, evidence, ai="cv_suggest")


# -- storing and listing ----------------------------------------------------------------------------------

def _insert(con: sqlite3.Connection, draft: Draft, based_on: str, job_id: str | None = None) -> str:
    sid = uuid.uuid4().hex[:12]
    con.execute(
        "INSERT INTO cv_suggestions (id, doc_id, based_on, job_id, section_id, target, op, kind, title, why, "
        "trigger, evidence, before, afters, status, fingerprint, ai, created) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
        (sid, DOC_ID, based_on, job_id, draft.section_id, draft.target, draft.op, draft.kind, draft.title,
         draft.why, json.dumps(draft.trigger), json.dumps(draft.evidence), draft.before, json.dumps(draft.afters),
         draft.fingerprint, draft.ai, clock.iso()))
    return sid


def _pending(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM cv_suggestions WHERE doc_id = ? AND job_id IS NULL AND status = 'pending' "
                       "ORDER BY created, rowid", (DOC_ID,)).fetchall()


def recheck(con: sqlite3.Connection, vid: str, doc) -> int:
    """Moves pending suggestions onto version `vid`; those whose `before` no longer matches are superseded."""
    model = _model()
    gone = 0
    for row in _pending(con):
        if row["based_on"] == vid:
            continue
        try:
            same = " ".join(shown_at(doc, row["op"], row["target"]).split()) == " ".join((row["before"] or "").split())
        except (model.CVError, AttributeError, StopIteration):
            same = False
        # only rows still pending: a request deciding a suggestion at the same time keeps its decision
        if same:
            con.execute("UPDATE cv_suggestions SET based_on = ? WHERE id = ? AND status = 'pending'", (vid, row["id"]))
        else:
            gone += con.execute("UPDATE cv_suggestions SET status = 'superseded', decided = ?"
                                " WHERE id = ? AND status = 'pending'", (clock.iso(), row["id"])).rowcount
    con.commit()
    return gone


def _meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row[0] if row else None


def look_again(con: sqlite3.Connection, *, step=None, use_ai: bool | None = None) -> dict:
    """Runs reviewers until `MAX_PENDING` suggestions wait or none are left; says what it found."""
    store = _store()
    vid, doc = store.current()
    type_ = cv_type()
    recheck(con, vid, doc)
    use_ai = ai_ready() if use_ai is None else use_ai
    found = 0
    pending = len(_pending(con))
    finished = True
    for sids in reviewers(doc, type_):
        if pending >= MAX_PENDING:
            finished = False
            break
        if step:
            step(f"Reading {where(doc, 's:' + sids[0]) if sids != [WHOLE_CV] else 'your whole CV'}")
        passages = passages_for(doc, sids) if use_ai and sids != [WHOLE_CV] else []
        ctx = context(con, doc, type_, passages="\n".join(p.get("text", "") for p in passages))
        failing = [d for sid in sids for d in checks_for(doc, sid, type_)]
        for draft in failing:
            if pending >= MAX_PENDING:
                break
            ok = guard(draft, ctx)
            if ok is not None:
                _insert(con, ok, vid)
                ctx.blocked.add(ok.fingerprint)
                ctx.keep.add(ok.target)          # one suggestion per line at a time
                found += 1
                pending += 1
        con.commit()              # the AI layer records its call on its own connection
        if use_ai and pending < MAX_PENDING:
            got = ask_ai(con, doc, type_, sids, failing, passages)
            ok = guard(got, ctx) if got is not None else None
            if ok is not None:
                _insert(con, ok, vid)
                found += 1
                pending += 1
        con.commit()
    if finished and not found:
        con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (META_REVIEWED, vid))
    else:
        con.execute("DELETE FROM meta WHERE k = ?", (META_REVIEWED,))
    con.commit()
    events.publish("suggestions")
    return {"found": found, "pending": pending, "version_id": vid,
            "state": "nothing_more" if finished and not found and not pending else "ready"}


def shape(row: sqlite3.Row, doc=None) -> dict:
    """A suggestion as the API's Sugg."""
    trigger = json.loads(row["trigger"] or "{}")
    try:
        place = where(doc, row["target"]) if doc is not None else ""
    except Exception:  # a part since removed still lists, under its section id
        place = row["section_id"] or ""
    return {"id": row["id"], "kind": row["kind"], "where": place, "target": row["target"],
            "title": row["title"], "why": row["why"],
            "trigger": trigger.get("label", ""), "trigger_type": trigger.get("type", ""),
            "evidence": [e.get("label", "") for e in json.loads(row["evidence"] or "[]")],
            "before": row["before"] or "", "afters": json.loads(row["afters"]), "alt": row["alt"],
            "edited": row["edited"], "status": row["status"], "reason": row["reason"], "op": row["op"],
            "can_accept_all": row["kind"] in DETERMINISTIC_KINDS, "ai": bool(row["ai"]),
            "result_version_id": row["result_version_id"]}


def _looking(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT 1 FROM actions WHERE kind = 'cv.suggest' AND state IN ('queued', 'running') "
                          "LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def listing(con: sqlite3.Connection, kind: str | None = None) -> dict:
    """The Suggestions tab: state, counts, kinds, pending and reviewed suggestions."""
    store = _store()
    try:
        vid, doc = store.current()
    except LookupError:
        return {"state": "nothing_more", "reviewed": 0, "total": 0, "kinds": [], "pending": [], "done": [],
                "message": ALL_REVIEWED}
    recheck(con, vid, doc)
    pending = _pending(con)
    done = con.execute("SELECT * FROM cv_suggestions WHERE doc_id = ? AND job_id IS NULL AND status IN "
                       "('accepted', 'edited', 'left') ORDER BY decided DESC, rowid DESC", (DOC_ID,)).fetchall()
    if _looking(con):
        state = "looking"
    elif not pending and _meta(con, META_REVIEWED) == vid:
        state = "nothing_more"
    else:
        state = "ready"
    kinds = sorted({r["kind"] for r in pending}, key=KINDS.index)
    shown = [r for r in pending if not kind or kind == "All" or r["kind"] == kind]
    return {"state": state, "reviewed": len(done), "total": len(done) + len(pending), "kinds": kinds,
            "pending": [shape(r, doc) for r in shown], "done": [shape(r, doc) for r in done],
            "message": ALL_REVIEWED if state == "nothing_more" else ""}


# -- decisions --------------------------------------------------------------------------------------------

def _row(con: sqlite3.Connection, sugg_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM cv_suggestions WHERE id = ?", (sugg_id,)).fetchone()
    if row is None:
        raise LookupError("That suggestion is not in Hal.")
    return row


def _ids_for(doc, target: str, shown: str) -> list[str]:
    names = [n.strip() for n in shown.split(",") if n.strip()]
    node = _model().find(doc, model_target(target))
    if target == WHOLE_CV:
        by_name = {s.title: s.id for s in node.sections}
        hidden = [s.id for s in node.sections if not s.visible]
        return [by_name[n] for n in names] + hidden
    if hasattr(node, "entries"):
        by_name = {(e.heading or e.org): e.id for e in node.entries}
        hidden = [e.id for e in node.entries if e.hidden]
        return [by_name[n] for n in names] + hidden
    return names


def change_for(doc, op: str, target: str, text: str):
    """The model change that makes `target` show `text` (the inverse for hide when `text` is the name)."""
    model = _model()
    if op == "reorder":
        return model.Change("reorder", model_target(target), _ids_for(doc, target, text))
    if op == "hide":
        return model.Change("hide" if not text else "show", target)
    return model.Change("replace" if op != "retitle" else "retitle", target, text)


def _version_n(con: sqlite3.Connection, vid: str) -> int | None:
    row = con.execute("SELECT n FROM cv_versions WHERE id = ?", (vid,)).fetchone()
    return row[0] if row else None


def _fresh_pending(con: sqlite3.Connection, sugg_id: str):
    row = _row(con, sugg_id)
    if row["status"] != "pending":
        raise RuntimeError(DECIDED)
    vid, doc = _store().current()
    recheck(con, vid, doc)
    row = _row(con, sugg_id)
    if row["status"] != "pending":
        raise RuntimeError(CHANGED_SINCE)
    return row, vid, doc


def accept(con: sqlite3.Connection, sugg_id: str, *, alt: int | None = None, text: str | None = None) -> dict:
    """Applies a suggestion (or the person's edit of it) as a new version; returns it and whether to ask
    about sending Hal's copy."""
    row, vid, doc = _fresh_pending(con, sugg_id)
    afters = json.loads(row["afters"])
    alt = row["alt"] if alt is None else int(alt)
    if not 0 <= alt < len(afters):
        raise ValueError("There is no such wording for this suggestion.")
    after = afters[alt] if text is None else str(text).strip()
    gap = GAP.search(after) if row["op"] == "replace" else None
    if gap:
        raise ValueError(FILL_GAP.format(gap=gap.group(0)))
    if row["op"] == "replace" and not after:
        raise ValueError("Type the words to use, or leave it as it is.")
    new_doc = _model().apply(doc, change_for(doc, row["op"], row["target"], after))
    new_vid = _store().new_version(new_doc, origin="improved", what=f"You accepted: {row['title']}", by="you",
                                   parent=vid)
    edited = text is not None and after != afters[alt]
    if edited:
        from jobhunt.writing import edits

        edits.record(con, "cv_line", row["target"], afters[alt], after, job_id=row["job_id"])
    con.execute("UPDATE cv_suggestions SET status = ?, alt = ?, edited = ?, result_version_id = ?, decided = ? "
                "WHERE id = ?", ("edited" if edited else "accepted", alt, after if edited else None, new_vid,
                                 clock.iso(), sugg_id))
    con.commit()
    recheck(con, new_vid, new_doc)
    events.publish("suggestions")
    return {"version_id": new_vid, "n": _version_n(con, new_vid),
            "ask_send_copy": _store().doc()["send"] == "original"}


def accept_kind(con: sqlite3.Connection, kind: str) -> dict:
    """Accepts every pending suggestion of a deterministic kind in one version."""
    if kind not in DETERMINISTIC_KINDS:
        raise ValueError(f"Accept all works for {', '.join(DETERMINISTIC_KINDS)} only.")
    store, model = _store(), _model()
    vid, doc = store.current()
    recheck(con, vid, doc)
    rows = [r for r in _pending(con) if r["kind"] == kind]
    done = []
    for r in rows:
        try:
            if shown_at(doc, r["op"], r["target"]) != (r["before"] or ""):
                continue
            doc = model.apply(doc, change_for(doc, r["op"], r["target"], json.loads(r["afters"])[r["alt"]]))
        except model.CVError as e:
            log.info("skipped suggestion %s in accept all: %s", r["id"], e)
            continue
        done.append(r["id"])
    if not done:
        return {"accepted": 0, "version_id": vid}
    words = f"{kind.lower()} fix" if len(done) == 1 else f"{kind.lower()} fixes"
    new_vid = store.new_version(doc, origin="improved", what=f"You accepted {len(done)} {words}", by="you",
                                parent=vid)
    now = clock.iso()
    con.executemany("UPDATE cv_suggestions SET status = 'accepted', result_version_id = ?, decided = ? WHERE id = ?",
                    [(new_vid, now, i) for i in done])
    con.commit()
    recheck(con, new_vid, doc)
    events.publish("suggestions")
    return {"accepted": len(done), "version_id": new_vid}


def undo(con: sqlite3.Connection, sugg_id: str) -> dict:
    """Sets the target back to `before` as a new version, when it still shows what was accepted."""
    row = _row(con, sugg_id)
    if row["status"] not in ("accepted", "edited"):
        raise RuntimeError("Only an accepted suggestion can be undone.")
    store, model = _store(), _model()
    vid, doc = store.current()
    accepted = row["edited"] if row["edited"] is not None else json.loads(row["afters"])[row["alt"]]
    try:
        now = shown_at(doc, row["op"], row["target"])
    except (model.CVError, AttributeError, StopIteration):
        raise RuntimeError(CHANGED_SINCE) from None
    if " ".join(now.split()) != " ".join(accepted.split()):
        raise RuntimeError(CHANGED_SINCE)
    back = row["before"] or ""
    new_doc = model.apply(doc, change_for(doc, row["op"], row["target"], back))
    new_vid = store.new_version(new_doc, origin="undo", what=f"You undid: {row['title']}", by="you", parent=vid)
    con.execute("UPDATE cv_suggestions SET status = 'pending', based_on = ?, edited = NULL, "
                "result_version_id = NULL, decided = NULL WHERE id = ?", (new_vid, sugg_id))
    con.commit()
    recheck(con, new_vid, new_doc)
    events.publish("suggestions")
    return {"version_id": new_vid}


def leave(con: sqlite3.Connection, sugg_id: str, *, reason: str = "", words: str = "", remember: bool = False) -> dict:
    """Leaves the CV as it is, keeps the reason and, with "Remember this", a preference."""
    row = _row(con, sugg_id)
    if row["status"] != "pending":
        raise RuntimeError(DECIDED)
    if reason and reason not in REASONS:
        raise ValueError(f"Pick one of: {', '.join(REASONS)}.")
    now = clock.iso()
    con.execute("UPDATE cv_suggestions SET status = 'left', reason = ?, words = ?, remember = ?, decided = ? "
                "WHERE id = ?", (reason or None, words or None, int(bool(remember)), now, sugg_id))
    pref = _preference(row, reason) if remember else None
    if pref:
        con.execute("INSERT INTO cv_preferences (kind, target, text, from_suggestion, created) VALUES (?, ?, ?, ?, ?)",
                    (*pref, sugg_id, now))
    con.commit()
    events.publish("suggestions")
    return {"ok": True, "remembered": bool(pref)}


def _preference(row: sqlite3.Row, reason: str) -> tuple[str, str | None, str] | None:
    section = f"s:{row['section_id']}" if row["section_id"] and row["section_id"] != WHOLE_CV else WHOLE_CV
    return {
        "Keep my wording": ("keep_wording", row["target"], row["before"] or ""),
        "Too formal": ("prefer", None, "Write in a plain tone"),
        "Too long": ("prefer", None, "Keep lines shorter"),
        "Not relevant": ("prefer", section, f"kind_off:{row['kind']}"),
    }.get(reason)


def another(con: sqlite3.Connection, sugg_id: str) -> dict:
    """Asks `cv_another` for new wording; says when there is no other sensible option."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    row, _vid, doc = _fresh_pending(con, sugg_id)
    afters = json.loads(row["afters"])
    if row["op"] != "replace" or not ai_ready():
        return {"new": False, "message": ONLY_OPTION, "alt": row["alt"]}
    type_ = cv_type()
    system = prompts.text("cv_another", con=con, spelling=spelling_words())
    user = json.dumps({"cv_type": type_.name, "writing": type_.writing, "target": row["target"],
                       "now": row["before"], "why": row["why"], "already_shown": afters,
                       "preferences": _prefs_words(con)}, ensure_ascii=True)
    try:
        got = ai.chat_json(system, user, role="writer", purpose="cv_another", priority=AI_PRIORITY)
    except ai.LLMUnavailable as e:
        log.info("no other wording for %s: %s", sugg_id, e)
        return {"new": False, "message": ONLY_OPTION, "alt": row["alt"]}
    offered = got.get("afters") or ([got["after"]] if got.get("after") else [])
    ctx = context(con, doc, type_)
    ctx.blocked = set()
    seen = {" ".join(a.lower().split()) for a in afters}
    for text in offered:
        draft = Draft(row["target"], row["op"], row["kind"], row["title"], row["why"],
                      json.loads(row["trigger"]), row["before"] or "", [str(text)], row["section_id"] or "",
                      ai="cv_another")
        ok = guard(draft, ctx)
        if ok is None or " ".join(ok.afters[0].lower().split()) in seen:
            continue
        afters.append(ok.afters[0])
        con.execute("UPDATE cv_suggestions SET afters = ?, alt = ? WHERE id = ?",
                    (json.dumps(afters), len(afters) - 1, sugg_id))
        con.commit()
        events.publish("suggestions")
        return {"new": True, "alt": len(afters) - 1, "after": afters[-1]}
    return {"new": False, "message": ONLY_OPTION, "alt": row["alt"]}

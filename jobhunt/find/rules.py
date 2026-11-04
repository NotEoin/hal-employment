"""Scoring rules with structured reasons: every point and every hide says why.

`score(job, goal, weights)` is pure. It runs every rule in a fixed order and keeps each verdict as a
`Reason`; the first rule that hides a job is the one shown under Hidden, and a hidden job scores 0, as
the command line expects. `score_notes` is the comma-joined short notes, in the order and words
of L score.py, so the technical-graduate preset with the owner's goal reproduces today's scores and notes
(the compatibility test), except that a paid internship the goal includes no longer loses 10 points.

Weights come from the preset (`find/presets/<id>.toml`); any `scoring.*` setting the person changed is
laid over them. Only changed settings count, because the catalogue's plain defaults are one preset's
round numbers, not the technical preset's exact ones.

Not yet built: distances (`goal.max_distance`; places match by name and alias only), "Older than 21
days", "You chose: <reason>" (from the person's feedback) and the "Starts before your course ends" note.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from jobhunt.core import clock
from jobhunt.find import kinds as kinds_mod
from jobhunt.find import pay as pay_mod
from jobhunt.find.goal import Goal, company_key

log = logging.getLogger(__name__)

PRESETS = Path(__file__).resolve().parent / "presets"
DEFAULT_PRESET = "technical_grad"
SCORE_RANGE = (1, 100)      # a shown job never scores 0, which means hidden
TEXT_WINDOW = 4000          # characters of the advert the title-like rules read (L score.py)
PLACE_TEXT_WINDOW = 3000    # characters read for an office place when the location says nothing useful
FRESH_DAYS, OLD_DAYS, STALE_DAYS = 3, 45, 180
SMALL_BOARD, MID_BOARD = 15, 40        # open roles on the employer's board
FAR_UNDER = 0.85            # pay below this share of the person's lowest hides the job (presets that say so)
MAX_YEARS_READ = 20         # "25 years of trading" is history, not an experience requirement
NICHE_SOURCES = ("hn", "ktp")
ALERT_ONLY_SOURCES = ("indeed",)
TITLE_RULES_SKIP = ("hn",)  # HN "titles" are free text; judged on the body


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


TRAINING_PROVIDER = _rx(r"\btraining\b|\bacademy\b|bootcamp|career switch|\bupskill")
AGENCY = _rx(r"recruit|staffing|resourcing|talent|search|consulting\b|\bnoir\b|hays|reed specialist|"
             r"harvey nash|robert walters|michael page|computer futures|jefferson frank")
TRAINING_BOND = _rx(r"training bond|repay (the )?(cost of )?(your )?training|training (cost|fee)s? "
                    r"(will be|are) (repayable|recoverable)|exit fee|minimum (service|commitment) "
                    r"(period|of \d+ (months|years))")
EXPERIENCE_REQUIRED = _rx(
    r"\b(proven|significant|solid|demonstrable|substantial|extensive|previous|prior|relevant) "
    r"(commercial |professional |industry |hands-on |production )?experience (in|with|of|as|building|developing)|"
    r"\b(commercial|professional|industry) experience (in|with|of|as|building|developing|is required)|"
    r"\bextensive \w+ experience|\b(strong|solid),? proven experience|\btrack record of|"
    r"meaningful production ownership|\b(several|many|a few|multiple) years\b|\bmid[- ]level role|"
    r"experience level: (mid|senior|mid-senior)")
POSTGRAD_REQUIRED = _rx(r"\b(msc|ph\.?d|master'?s( degree)?)\b[^.\n]{0,30}\b(in|required|degree)\b")
UNDERGRAD_OK = _rx(r"\b(bsc|bachelor'?s?|undergraduate|first[- ]class|2[.:]1)\b")
NO_EXPERIENCE_NEEDED = _rx(r"\b(no|without) (prior |previous |commercial |professional )*experience|"
                           r"experience (is )?(not required|not necessary|a plus|a bonus|desirable|"
                           r"is nice to have)")
CLEARANCE_HELD = _rx(r"\b(sc|dv|ctc|esc)[- ]clear(ed|ance)\b|active (sc|dv) clearance|"
                     r"(hold|holding|have) (a |an )?(current |active |valid )?(sc|dv|security) clearance")
FULLY_REMOTE = _rx(r"fully remote|100% remote|remote[- ]first|remote[- ]only|work from anywhere|"
                   r"remote[- ]based|this is a remote (role|position)|workplace: remote|distributed (team|company)")
HYBRID = _rx(r"\bhybrid\b|days? (a|per) week in (the |our )?office|days? in (the |our )?office|"
             r"workplace: hybrid|flexible working")
ON_SITE = _rx(r"\bon-?site\b|\bin[- ]office\b|office[- ]based|\bin[- ]person\b|"
              r"5 days (a week )?in|five days (a week )?in|workplace: on_site")
REMOTE_ANY = r"\bremote\b|\banywhere\b"
# A remote role pinned to somewhere else is not open to someone living in the UK.
ELSEWHERE = (r"\b(north america|americas|usa?|united states|canada|latam|apac|asia|india|australia|"
             r"germany|france|spain|poland|portugal|netherlands|ireland|singapore|japan|brazil|mexico|uae|dubai|"
             r"israel|switzerland|sweden|denmark|paris|berlin|amsterdam|madrid|lisbon|warsaw|dublin|"
             r"new york|san francisco|toronto|[a-z]+, (ca|ny|tx|wa|ma|co|il))\b")
SENIORITY = _rx(r"\b(senior|sr\.?|staff|principal|lead|head|director|manager|vp|vice president|chief|"
                r"architect|distinguished|fellow)\b|\b(iii|iv|l[5-9]|e[5-9])\b")
ENTRY = _rx(r"\b(graduate|grad|junior|jr\.?|entry[- ]level|early careers?|new grad|"
            r"associate (software|engineer|developer)|engineer i\b|level 1|"
            r"ktp associate|trainee|0-2 years|recent graduates?)\b")
YEARS = _rx(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|to|\u2013)?\s*(?:\d{1,2})?\s*\+?\s*years?['\u2019]?\s*(?:of\s+)?"
            r"(?:\w+[\s,/-]+){0,4}?(?:experience|building|developing|of (?:software|engineering|industry|"
            r"commercial|professional|production|hands-on))")
# Built-in sectors: each with the advert words that place a job in it, and today's short note.
SECTORS = {
    "gambling": (_rx(r"\b(igaming|sportsbook|online casino|casino games|betting (company|operator|platform|exchange|"
                     r"industry)|gambling (company|operator|industry))\b"), "gambling"),
    "tobacco": (_rx(r"\b(tobacco|vap(e|ing) products|e-cigarettes?)\b"), "excluded sector"),
    "payday loans": (_rx(r"\b(payday loans?|high-cost (short-term )?credit)\b"), "excluded sector"),
    "multi-level marketing": (_rx(r"\b(multi-level marketing|network marketing)\b"), "excluded sector"),
    "firearms": (_rx(r"\b(firearms|ammunition)\b"), "excluded sector"),
    "adult": (_rx(r"\badult (content|entertainment|industry|creators?)\b|\b18\+ (content|platform)|\bnsfw\b|\bporn"),
              "adult content"),
}
SECTOR_ORDER = ("tobacco", "payday loans", "multi-level marketing", "firearms", "adult", "gambling")
DEFENCE = "arms and defence"
DEFENCE_NAME = _rx(r"defen[cs]e|military|weapons?|munitions|armaments|\barmy\b|\bnavy\b|"
                   r"royal air force|\braf\b|royal marines|armed forces|\bmod\b")
NOT_FOR_GRADS_NOTE = "internship/placement/apprenticeship/PhD"
FLAGS = kinds_mod.FLAG_KINDS


@dataclass(frozen=True)
class Reason:
    """One rule's verdict on one job."""

    rule: str            # stable id; job_reasons.rule ("place_first", "kind_not_chosen:internship")
    stage: str           # filter | eligibility | points | screen | closed
    points: int
    label: str           # "In Leeds, your first choice of place"
    note: str            # today's short note for score_notes ("entry-level title", "skills +12")
    quote: str = ""      # advert words that matched
    hide: bool = False
    data: dict | None = None


@dataclass(frozen=True)
class Scored:
    """A job's score, the rule that hid it (if any) and every reason, with its kind."""

    score: int
    base: int
    hidden: Reason | None
    reasons: tuple[Reason, ...]
    kind: kinds_mod.Kind

    @property
    def notes(self) -> str:
        """`score_notes` as today: the hiding rule's note, else every note in order."""
        if self.hidden is not None:
            return self.hidden.note
        return ", ".join(r.note for r in self.reasons if r.note and not r.hide)


@dataclass(frozen=True)
class Weights:
    """The points and switches of one preset, with the person's changed settings laid over them."""

    preset: str
    w: dict
    role_filter: bool
    role_miss_note: str
    experience: str
    pay_far_under_hide: bool
    skill_cap: int
    interest_cap: int
    roles: tuple[str, ...]
    avoid_roles: tuple[str, ...]
    interests: tuple[str, ...]
    skills: tuple[tuple[str, int], ...]
    kinds: tuple[str, ...] = field(default=())
    off_field: tuple[str, ...] = field(default=())

    def __getitem__(self, name: str) -> int:
        return int(self.w.get(name, 0))


def phrase_rx(items: tuple[str, ...] | list[str]) -> re.Pattern | None:
    """One pattern for a list of words: "re:" items are regular expressions, the rest whole-word phrases."""
    parts = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        parts.append(f"(?:{item[3:]})" if item.startswith("re:") else re.escape(item.lower()))
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.I) if parts else None


def parse_skills(lines: list[str] | tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """Skill lines such as "python = 3" (or "re:<pattern> = 2") as (pattern, points)."""
    out = []
    for line in lines or []:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, points = line.rsplit("=", 1)
        name = name.strip()
        try:
            value = int(points.strip())
        except ValueError:
            log.warning("skill line without whole points ignored: %r", line)
            continue
        out.append((name[3:] if name.startswith("re:") else rf"\b{re.escape(name.lower())}\b", value))
    return tuple(out)


def preset(preset_id: str) -> dict:
    """The raw preset file `find/presets/<id>.toml`."""
    path = PRESETS / f"{preset_id}.toml"
    if not path.is_file():
        raise LookupError(f"There is no scoring preset {preset_id!r}.")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def preset_ids() -> list[str]:
    """Every preset that ships, by id."""
    return sorted(p.stem for p in PRESETS.glob("*.toml"))


def weights_from(preset_id: str, changed: dict | None = None) -> Weights:
    """A preset's weights with `changed` (`scoring.*` values the person set) laid over them."""
    raw = preset(preset_id)
    changed = changed or {}
    w = dict(raw["weights"])
    for key, value in changed.items():
        if key.startswith("scoring.w."):
            w[key.removeprefix("scoring.w.")] = value
    if "scoring.base" in changed:
        w["base"] = changed["scoring.base"]
    skills = changed.get("scoring.skills") or raw.get("skills", [])
    return Weights(
        preset=preset_id, w=w, role_filter=bool(raw.get("role_filter")), role_miss_note=raw.get("role_miss_note", ""),
        experience=raw.get("experience", "points"), pay_far_under_hide=bool(raw.get("pay_far_under_hide")),
        skill_cap=int(changed.get("scoring.skill_cap", raw.get("skill_cap", 0))),
        interest_cap=int(changed.get("scoring.interest_cap", raw.get("interest_cap", 0))),
        roles=tuple(raw.get("roles", [])), avoid_roles=tuple(raw.get("avoid_roles", [])),
        interests=tuple(raw.get("interests", [])), skills=parse_skills(skills), kinds=tuple(raw.get("kinds", [])),
        off_field=tuple(raw.get("off_field", [])))


_memo: dict[str, Weights] = {}


def current_weights() -> Weights:
    """The live weights: the chosen preset, with the person's changed `scoring.*` settings on top."""
    from jobhunt.core import settings
    from jobhunt.find.goal import stored_key

    key = stored_key("scoring.", "goal.stage")
    found = _memo.get(key)
    if found is None:
        changed = {k: v for k, v in settings.changed().items() if k.startswith("scoring.")}
        chosen = settings.get("scoring.preset") or DEFAULT_PRESET
        found = weights_from(chosen if (PRESETS / f"{chosen}.toml").is_file() else DEFAULT_PRESET, changed)
        _memo.clear()
        _memo[key] = found
    return found


@dataclass
class _Compiled:
    places: list[tuple[object, re.Pattern | None]]
    roles: re.Pattern | None
    avoid: re.Pattern | None
    off_field: re.Pattern | None
    interests: list[tuple[str, re.Pattern]]
    skills: list[tuple[re.Pattern, int]]
    ok_places: re.Pattern | None


_compiled: dict[tuple, _Compiled] = {}


def compiled(goal: Goal, w: Weights) -> _Compiled:
    """The goal's and preset's word lists as patterns, made once per goal and weights."""
    key = (goal, w.preset, json.dumps(w.w, sort_keys=True), w.roles, w.avoid_roles, w.off_field, w.interests,
           w.skills)
    found = _compiled.get(key)
    if found is not None:
        return found
    places = [(p, re.compile(p.pattern(), re.I) if not p.remote else None) for p in goal.places]
    by_name = {p.name.lower(): p for p in goal.places}
    ok = [by_name[n.lower()].pattern() if n.lower() in by_name else rf"\b{re.escape(n.lower())}\b"
          for n in goal.training_ok_places if n.strip()]
    interests = goal.interests or w.interests
    found = _Compiled(
        places=places, roles=phrase_rx(goal.roles or w.roles),
        avoid=phrase_rx(tuple(goal.avoid_roles) + tuple(w.avoid_roles)), off_field=phrase_rx(w.off_field),
        interests=[(i, re.compile(rf"\b{re.escape(i.lower())}\b", re.I)) for i in interests if i.strip()],
        skills=[(re.compile(p, re.I), pts) for p, pts in w.skills],
        ok_places=re.compile("|".join(ok), re.I) if ok else None)
    if len(_compiled) > 8:
        _compiled.clear()
    _compiled[key] = found
    return found


def place(c: _Compiled, w: Weights, location: str, text: str) -> tuple[object | None, int, str]:
    """The goal place a job is in, its points and the note; (None, 0, "") when it is in none of them."""
    loc = (location or "").lower()
    named = [(p, rx) for p, rx in c.places if rx is not None]
    for p, rx in named:
        if rx.search(loc):
            return p, w["first_place"] if p.rank == 0 else w["other_place"], p.name
    remote_like = not loc.strip() or (re.search(REMOTE_ANY, loc) and not re.search(ELSEWHERE, loc))
    if remote_like:
        if loc.strip() and any(p.remote for p, _ in c.places):
            remote = next(p for p, _ in c.places if p.remote)
            return remote, w["remote_uk"], "remote (UK)"
        body = (text or "")[:PLACE_TEXT_WINDOW].lower()
        for p, rx in named:
            if rx.search(body):
                return p, w["first_place_text"] if p.rank == 0 else w["other_place_text"], f"{p.name} (from text)"
    return None, 0, ""


def _workplace(location: str, text: str) -> tuple[str | None, bool]:
    """The working pattern an advert states ("on-site", "hybrid", "remote") and whether it is fully remote."""
    head = f"{location or ''}\n{(text or '')[:TEXT_WINDOW]}"
    if FULLY_REMOTE.search(head) and not (ON_SITE.search(head) or HYBRID.search(head)):
        return "remote", True
    if ON_SITE.search(head) and not HYBRID.search(head):
        return "on-site", False
    if HYBRID.search(head) or re.search(REMOTE_ANY, (location or "").lower()):
        return "hybrid", False
    return None, False


PATTERN_OF = {"office": "on-site", "hybrid": "hybrid", "remote": "remote"}


def min_years(text: str) -> int | None:
    """The fewest years of experience an advert asks for, or None."""
    found = [int(m.group(1)) for m in YEARS.finditer(text or "") if int(m.group(1)) < MAX_YEARS_READ]
    return min(found) if found else None


def _age_days(posted_at: str | None) -> int | None:
    if not posted_at:
        return None
    try:
        posted = datetime.fromisoformat(posted_at.replace("Z", "+00:00").replace(" UTC", "+00:00"))
    except ValueError:
        return None
    if posted.tzinfo is None:  # KTP dates are naive UK local time
        posted = posted.replace(tzinfo=UTC)
    return (clock.now_utc() - posted).days


def score(job: dict, goal: Goal | None = None, weights: Weights | None = None) -> Scored:
    """Every rule's verdict on one job; pure, no database and no network."""
    if goal is None:
        from jobhunt.find.goal import current
        goal = current()
    w = weights or current_weights()
    c = compiled(goal, w)
    title = job.get("title") or ""
    text = job.get("description") or ""
    company = job.get("company") or ""
    location = job.get("location") or ""
    source = job.get("source") or ""
    kind = kinds_mod.classify(title, text, job.get("salary"))
    reasons: list[Reason] = []
    add = reasons.append

    def hide(rule, stage, label, note, quote="", data=None):
        add(Reason(rule, stage, 0, label, note, quote, True, data))

    if job.get("open_state") == "closed":
        hide("closed", "closed", "The advert had closed before Hal could show it", "advert closed")
    if job.get("screen_state") == "out":
        why = job.get("screen_why") or ""
        hide("screen_out", "screen", f"Hal's AI check: {why}", f"screened out: {why}")

    key = company_key(company)
    if key and key in goal.avoid_employers:
        hide("excluded_employer", "filter", "Employers you avoid", "excluded employer", company)
    elif DEFENCE in goal.avoid_sectors and DEFENCE_NAME.search(company):
        hide("excluded_employer", "filter", f"Sectors you avoid: {DEFENCE}", "excluded employer", company)

    scheme = (key in goal.training_providers or TRAINING_PROVIDER.search(company) or TRAINING_BOND.search(text))
    training_points = 0
    if scheme and goal.training_schemes != "allow":
        in_ok = c.ok_places is not None and c.ok_places.search(location.lower())
        if c.ok_places is not None and not in_ok:
            hide("training_scheme", "filter", "Makes you repay training costs",
                 f"train-and-place outside {', '.join(goal.training_ok_places)}")
        elif goal.training_schemes == "hide":
            hide("training_scheme", "filter", "Makes you repay training costs", "train-and-place scheme")
        else:
            training_points = w["training_scheme"]

    m = CLEARANCE_HELD.search(f"{title}\n{text}")
    if m and not goal.has_clearance:
        hide("clearance", "filter", "Needs a security clearance you do not hold", "needs existing security clearance",
             m.group(0))

    where, place_points, place_note = (None, 0, "")
    if goal.places:
        where, place_points, place_note = place(c, w, location, text)
        if where is None:
            hide("too_far", "filter", "Too far from your places", f"location: {location or 'unknown'}")

    title_rules = source not in TITLE_RULES_SKIP
    role_points: Reason | None = None
    if title_rules:
        avoided = c.avoid.search(title) if c.avoid is not None else None
        wanted = c.roles.search(title) if c.roles is not None else None
        if w.role_filter and c.roles is not None and (avoided or not wanted):
            hide("avoid_role" if avoided else "role_title", "filter",
                 f"Roles you avoid: {avoided.group(0)}" if avoided else "Not one of the roles you want",
                 f"{w.role_miss_note}: {title}", avoided.group(0) if avoided else "")
        elif avoided:
            hide("avoid_role", "filter", f"Roles you avoid: {avoided.group(0).lower()}",
                 f"role to avoid: {avoided.group(0).lower()}", avoided.group(0))
        elif (off := c.off_field.search(title) if c.off_field is not None else None) and not wanted:
            hide("off_field", "filter", f"Outside your field: {off.group(0).lower()}",
                 f"outside your field: {off.group(0).lower()}", off.group(0))
        elif c.roles is not None and not w.role_filter:
            if wanted:
                role_points = Reason("role_title", "points", w["role_title"],
                                     f"\"{wanted.group(0)}\" is a role you want", f"role: {wanted.group(0).lower()}",
                                     wanted.group(0))
            else:
                in_text = c.roles.search(text[:TEXT_WINDOW])
                role_points = Reason("role_text_only", "points", w["role_text_only"],
                                     f"\"{in_text.group(0)}\" is only in the advert text" if in_text
                                     else "None of your roles is in the title", "role only in text" if in_text
                                     else "not one of your roles", in_text.group(0) if in_text else "")
        senior = SENIORITY.search(title)
        if senior and "experienced" not in goal.kinds:
            hide("kind_not_chosen:experienced", "filter",
                 f"{kinds_mod.LABELS['experienced']}: not a kind of role you chose", "too senior", senior.group(0))
        elif kind.kind not in ("other", "experienced") and kind.kind not in goal.kinds and kind.kind != "graduate_job":
            hide(f"kind_not_chosen:{kind.kind}", "filter",
                 f"{kinds_mod.LABELS[kind.kind]}: not a kind of role you chose",
                 NOT_FOR_GRADS_NOTE, kind.kind_quote)
        elif kind.kind == "experienced" and "experienced" not in goal.kinds:
            hide("kind_not_chosen:experienced", "filter",
                 f"{kinds_mod.LABELS['experienced']}: not a kind of role you chose", NOT_FOR_GRADS_NOTE,
                 kind.kind_quote)
        elif kind.kind == "graduate_job" and "graduate_job" not in goal.kinds and goal.kinds - set(FLAGS):
            hide("kind_not_chosen:graduate_job", "filter",
                 f"{kinds_mod.LABELS['graduate_job']}: not a kind of role you chose", "graduate or entry-level role",
                 kind.kind_quote)

    student = goal.is_student()
    verdict, why = kinds_mod.eligible(kind.eligibility, student=student, end_year=goal.finished_year())
    if verdict is False:
        rule = "grad_year" if kind.eligibility.get("requires") == "graduating_in" and student else "students_only"
        label = ("For current students only" if rule == "students_only"
                 else f"For people finishing in {', '.join(map(str, kind.eligibility.get('years', [])))}")
        hide(rule, "eligibility", label, "for current students only" if rule == "students_only" else label.lower(),
             kind.eligibility.get("quote", ""), {"why": why})

    for name in SECTOR_ORDER:
        rx, note = SECTORS[name]
        m = rx.search(text)
        if m and name in goal.avoid_sectors:
            hide(f"avoid_sector:{name}", "filter", f"Sectors you avoid: {name}", note, m.group(0))
    for word in goal.avoid_sectors:
        if word in SECTORS or word == DEFENCE:
            continue
        m = re.search(rf"\b{re.escape(word)}\b", text, re.I)
        if m:
            hide(f"avoid_sector:{word}", "filter", f"Sectors you avoid: {word}", "excluded sector", m.group(0))

    if kind.paid == 0 and not goal.unpaid_ok:
        hide("unpaid", "filter", "Unpaid or expenses only", "unpaid", kind.pay.quote)

    pattern, fully_remote = _workplace(location, text)
    pattern_points = 0
    if goal.pattern != "any" and pattern:
        if fully_remote and goal.pattern in ("office", "hybrid"):
            hide("pattern", "filter", f"Fully remote, and you chose {goal.pattern}",
                 "fully remote")
        elif PATTERN_OF[goal.pattern] == pattern:
            pattern_points = w["pattern_match"]
        else:
            pattern_points = w["pattern_other"]
    if place_note:
        add(Reason(f"place_{'first' if getattr(where, 'rank', 1) == 0 else 'other'}"
                   if not getattr(where, "remote", False) else "remote_uk", "points", place_points,
                   f"In {where.name}, your {'first choice of' if where.rank == 0 else 'other'} place"
                   if not where.remote else "Remote within the UK", place_note))
    if pattern:
        add(Reason("pattern", "points", pattern_points, f"Working pattern: {pattern}", pattern))

    if training_points:
        add(Reason("training_scheme", "points", training_points, "Makes you repay training costs",
                   "train-and-place: check bond and repayment terms"))
    if role_points is not None:
        add(role_points)
    if kind.kind in goal.kinds and kind.kind not in ("graduate_job", "other", "experienced"):
        note = ("graduate internship" if kind.kind == "internship" and not student
                else kinds_mod.LABELS[kind.kind].lower())
        add(Reason("kind_wanted", "points", w["kind_wanted"], f"{kinds_mod.ONE[kind.kind]}, which you chose", note,
                   kind.kind_quote))
    if kind.hours == "part" and "part_time" not in goal.kinds:
        add(Reason("kind_also:part_time", "points", w["kind_also"], "Part-time, which you did not choose", "part-time"))
    if kind.contract == "fixed" and "fixed_term" not in goal.kinds:
        add(Reason("kind_also:fixed_term", "points", w["kind_also"], "Fixed-term, which you did not choose",
                   "fixed-term"))
    if not title_rules and SENIORITY.search(title) and not ENTRY.search(text):
        add(Reason("seems_senior", "points", w["seems_senior"], "Seems to be for senior people only",
                   "seems senior-only"))
    m = ENTRY.search(title)
    if m:
        add(Reason("entry_level", "points", w["entry_level"], "Graduate or entry-level role", "entry-level title",
                   m.group(0)))
    else:
        m = ENTRY.search(text[:TEXT_WINDOW])
        if m:
            add(Reason("entry_level", "points", w["entry_level_text"], "Open to graduates", "open to grads",
                       m.group(0)))

    if AGENCY.search(company):
        if goal.agencies == "hide":
            hide("agency", "filter", "Recruitment agency; employer not named", "recruitment agency")
        else:
            add(Reason("agency", "points", w["agency_lower"] if goal.agencies == "lower" else 0,
                       "Recruitment agency; employer not named", "recruitment agency, employer unnamed"))
    if source in ALERT_ONLY_SOURCES:
        add(Reason("alert_only", "points", w["alert_only"], "Known only from an alert email",
                   "alert only, find the advert"))

    yrs = min_years(text)
    over = (yrs or 0) - goal.experience_years
    if yrs is not None and over >= 1:
        if w.experience == "hide" or over >= 2:
            hide("years_required", "filter", f"Asks for {yrs} years of experience; you have {goal.experience_years}",
                 f"asks {yrs}+ years", data={"years": yrs})
        else:
            add(Reason("years_required", "points", w["years_required"],
                       f"Asks for {yrs} years of experience; you have {goal.experience_years}", f"asks {yrs}+ years"))
    if goal.experience_years == 0:
        m = EXPERIENCE_REQUIRED.search(text)
        if m and not NO_EXPERIENCE_NEEDED.search(text):
            if w.experience == "hide":
                hide("experience_required", "filter", "Asks for earlier experience", "asks for prior experience",
                     m.group(0))
            else:
                add(Reason("experience_required", "points", w["years_required"], "Asks for earlier experience",
                           "asks for prior experience", m.group(0)))
    m = POSTGRAD_REQUIRED.search(text)
    if m and not UNDERGRAD_OK.search(text):
        if w.experience == "hide":
            hide("postgrad_required", "filter", "Asks for a postgraduate degree", "asks for a postgraduate degree",
                 m.group(0))
        else:
            add(Reason("postgrad_required", "points", w["years_required"], "Asks for a postgraduate degree",
                       "asks for a postgraduate degree", m.group(0)))

    if c.interests:
        hits = [name for name, rx in c.interests if rx.search(f"{title}\n{text}")]
        if hits:
            add(Reason("interest", "points", min(len(hits) * w["interest"], w.interest_cap),
                       f"Mentions {len(hits)} of your interests: {', '.join(hits)}", f"interests: {', '.join(hits)}"))
        else:
            add(Reason("no_interest", "points", w["no_interest"], "Mentions none of your interests", "no interests"))
    if c.skills:
        skill_points = sum(pts for rx, pts in c.skills if rx.search(text))
        capped = min(skill_points, w.skill_cap)
        add(Reason("skill", "points", capped, "Skills you have", f"skills +{capped}"))

    floor = goal.min_pay
    top = kind.pay.max
    if top and floor and top < floor:
        if w.pay_far_under_hide and top < floor * FAR_UNDER:
            hide("pay_far_under", "filter", f"Pay is well under your lowest ({pay_mod.words(floor)})",
                 f"pay <= GBP {top:,}", kind.pay.quote)
        else:
            add(Reason("pay_under", "points", w["pay_under"], f"Pay is under your lowest ({pay_mod.words(floor)})",
                       f"pay <= GBP {top:,}", kind.pay.quote))
    elif top:
        add(Reason("pay", "points", 0, f"Pays up to {pay_mod.words(top)} a year", f"pay to GBP {top:,}",
                   kind.pay.quote))

    size = job.get("board_size")
    if source in NICHE_SOURCES:
        add(Reason("niche_source", "points", w["niche_source"], "Found on a small, specialist site", "niche source"))
    elif size is not None and size <= SMALL_BOARD:
        add(Reason("small_employer", "points", w["small_employer"], f"Small employer: {size} open roles",
                   f"small ({size} roles open)"))
    elif size is not None and size <= MID_BOARD:
        add(Reason("small_employer", "points", w["small_employer_mid"], f"Smaller employer: {size} open roles", ""))

    age = _age_days(job.get("posted_at"))
    if age is not None:
        if age <= FRESH_DAYS:
            add(Reason("fresh", "points", w["fresh"], f"Posted {age} days ago", "fresh"))
        elif age > STALE_DAYS:
            add(Reason("stale", "points", w["stale"], f"Posted {age} days ago", f"{age}d old"))
        elif age > OLD_DAYS:
            add(Reason("old", "points", w["old"], f"Posted {age} days ago", f"{age}d old"))

    points = w["base"] + sum(r.points for r in reasons if not r.hide)
    base = max(SCORE_RANGE[0], min(SCORE_RANGE[1], points))
    hidden = next((r for r in reasons if r.hide), None)
    return Scored(0 if hidden else base, base, hidden, tuple(reasons), kind)


def as_legacy(scored: Scored) -> tuple[float, str]:
    """`(score, score_notes)` in the shape `jobhunt.score.score` has always returned."""
    return scored.score, scored.notes


def save(con, job_id: str, scored: Scored, *, goal_version: int | None = None, preset: str | None = None) -> None:
    """Stores a job's score, its reasons and its kind; the caller commits."""
    k = scored.kind
    elig = dict(k.eligibility)
    if elig:
        hidden_elig = next((r for r in scored.reasons if r.stage == "eligibility" and r.hide), None)
        elig["eligible"] = hidden_elig is None
        elig["why"] = (hidden_elig.data or {}).get("why", "") if hidden_elig else ""
    con.execute("DELETE FROM job_reasons WHERE job_id = ?", (job_id,))
    seen = set()
    for r in scored.reasons:
        if r.rule in seen:
            continue
        seen.add(r.rule)
        con.execute("""INSERT INTO job_reasons (job_id, rule, stage, points, hide, label, note, quote, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, r.rule, r.stage, r.points, int(r.hide), r.label, r.note, r.quote,
                     json.dumps(r.data) if r.data else None))
    con.execute(
        """UPDATE jobs SET score = ?, score_notes = ?, hidden_rule = ?, kind = ?, kind_quote = ?, hours = ?,
                  contract = ?, paid = ?, pay_min = ?, pay_max = ?, pay_per = ?, students_only = ?, eligibility = ?,
                  goal_version = ?, preset = ?, scored_at = ? WHERE id = ?""",
        (scored.score, scored.notes, scored.hidden.rule if scored.hidden else None, k.kind, k.kind_quote or None,
         k.hours, k.contract, k.paid, k.pay.min, k.pay.max, k.pay.per, int(k.students_only),
         json.dumps(elig) if elig else None, goal_version, preset, clock.iso(), job_id))


def score_and_save(con, job: dict, goal: Goal | None = None, weights: Weights | None = None, *,
                   settle: bool = True) -> int:
    """Scores one job under the live goal, stores the result and returns the score; the caller commits.

    The score returned is 0 when the job turns out to be a repost of one shown or applied for already.
    `settle=False` leaves reposts to the caller, which settles them all at once (`rescore_all`).
    """
    from jobhunt.find.goal import current

    goal = goal or current()
    w = weights or current_weights()
    scored = score(job, goal, w)
    save(con, job["id"], scored, goal_version=goal.version or None, preset=w.preset)
    if settle and settle_reposts(con, job, goal, w) == job["id"]:
        return 0
    return scored.score


REPOST = "repost"
# An application already under way, sent or saved: its reposts are never shown beside it.
IN_HAND = ("applying", "applied", "assessment", "interview", "offer")


def repost_key(job) -> tuple[str, str, str]:
    """What makes two adverts one job: the employer, the title and the place, ignoring case and spacing."""
    def flat(text):
        return " ".join((text or "").lower().split())
    return company_key(job["company"]), flat(job["title"]), flat(job["location"])


def settle_reposts_of(con, job_id: str) -> None:
    """Settles the reposts of one job under the live goal, after it was queued or saved; the caller commits."""
    from jobhunt.find.goal import current

    row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is not None:
        settle_reposts(con, dict(row), current(), current_weights())


def settle_reposts(con, job, goal: Goal, w: Weights) -> str | None:
    """Shows one advert of a job listed more than once and hides the rest as reposts; the caller commits.

    Agencies post the same advert again each day under a new id (Reed, 2026-11-04: 11 copies of one trainee
    role in each of two cities), and each copy was a new match. The one kept is the copy already applied
    for or saved, else the newest listing, whose link is the likeliest still to work. Returns the id of
    `job` when it was hidden, else None.
    """
    key = repost_key(job)
    rows = con.execute("SELECT * FROM jobs WHERE lower(replace(title, ' ', '')) = ? AND active = 1 "
                       "AND source != 'email'", (key[1].replace(" ", ""),)).fetchall()
    group = [dict(r) for r in rows if repost_key(r) == key]
    return job["id"] if job["id"] in _settle_group(con, group, goal, w) else None


def settle_all_reposts(con, goal: Goal, w: Weights) -> int:
    """Settles every job's reposts in one pass; returns how many copies are hidden. The caller commits."""
    groups: dict[tuple, list[dict]] = {}
    for r in con.execute("SELECT * FROM jobs WHERE active = 1 AND source != 'email'"):
        groups.setdefault(repost_key(r), []).append(dict(r))
    return sum(len(_settle_group(con, g, goal, w)) for g in groups.values())


def _settle_group(con, group: list[dict], goal: Goal, w: Weights) -> set[str]:
    """Hides all but one copy of one job (see `settle_reposts`); returns the ids hidden."""
    from jobhunt.find.feedback import OPEN_STATUSES

    if len(group) < 2:
        return set()
    in_hand = [r for r in group if r["status"] in IN_HAND or r["saved_at"]]
    shown = [r for r in group if r["status"] in OPEN_STATUSES and not r["saved_at"]
             and r["hidden_rule"] in (None, REPOST)]
    keeper = in_hand[0] if in_hand else max(shown, key=lambda r: (r["first_seen"] or "", r["id"]), default=None)
    hidden = set()
    for r in shown:
        if keeper is not None and r["id"] == keeper["id"]:
            if r["hidden_rule"] == REPOST:   # the copy it stood behind went: show this one again
                save(con, r["id"], score(r, goal, w), goal_version=goal.version or None, preset=w.preset)
            continue
        label = ("Already in your applications" if in_hand else "Listed again; Hal shows the newest listing")
        con.execute("DELETE FROM job_reasons WHERE job_id = ? AND rule = ?", (r["id"], REPOST))
        con.execute("""INSERT INTO job_reasons (job_id, rule, stage, points, hide, label, note, quote, data)
                       VALUES (?, ?, 'filter', 0, 1, ?, 'repost', '', ?)""",
                    (r["id"], REPOST, label, json.dumps({"same_as": keeper["id"]})))
        con.execute("UPDATE jobs SET hidden_rule = ?, score = 0 WHERE id = ?", (REPOST, r["id"]))
        hidden.add(r["id"])
    return hidden


def rescore_all(con, step=None, *, every: int = 500) -> int:
    """Re-scores every job that has an advert, in one transaction; returns how many.

    `step(done, total)` is called every `every` jobs, so an action can show progress and be stopped.
    """
    from jobhunt.find.goal import current

    goal, w = current(), current_weights()
    # applications found in email have no advert to score
    rows = con.execute("SELECT * FROM jobs WHERE source != 'email'").fetchall()
    for n, row in enumerate(rows, 1):
        score_and_save(con, dict(row), goal, w, settle=False)
        if step is not None and n % every == 0:
            step(n, len(rows))
    settle_all_reposts(con, goal, w)
    con.commit()
    return len(rows)

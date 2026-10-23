"""Settings group "What you're looking for": the goal.

Every stored row here is part of the goal, so a change writes a goal version, and most change scores.
Defaults that depend on the career stage follow the table below.
"""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, safe
from jobhunt.core.settings.registry import Setting, register

STAGES = choices(
    ("student", "Student"), ("new_grad", "New graduate"), ("early", "Early career (1 to 3 years)"),
    ("experienced", "Experienced"), ("changing", "Changing career"), ("school_leaver", "School leaver"))
KINDS = choices(
    ("graduate_job", "Graduate and entry-level jobs"), ("graduate_scheme", "Graduate schemes"),
    ("internship", "Paid internships"), ("placement", "Placement years"),
    ("insight", "Insight weeks and spring weeks"), ("apprenticeship", "Apprenticeships"),
    ("experienced", "Jobs that ask for experience"), ("part_time", "Part-time jobs"),
    ("fixed_term", "Fixed-term jobs"))
# Default kinds and years of experience by career stage.
KINDS_BY_STAGE = {
    "student": ["internship", "placement", "insight", "graduate_scheme", "part_time", "fixed_term"],
    "new_grad": ["graduate_job", "graduate_scheme", "internship", "fixed_term"],
    "early": ["graduate_job", "experienced", "fixed_term"],
    "experienced": ["experienced", "fixed_term"],
    "changing": ["graduate_job", "graduate_scheme", "internship", "apprenticeship", "experienced", "fixed_term"],
    "school_leaver": ["apprenticeship", "graduate_job", "part_time", "fixed_term"],
}
YEARS_BY_STAGE = {"early": 2, "experienced": 5}
STUDY_BY_STAGE = {"student": "at_uni", "school_leaver": "none"}
DEFAULT_STAGE = "new_grad"
REMOTE_UK = "Remote in the UK"
MAX_YEARS = 40
MODE_HELP = "Just watching: Hal searches weekly and only tells you about exceptional matches."
SCHEME_CHOICES = choices(("allow", "Show them"), ("lower", "Show them lower"), ("hide", "Hide them"))


def _stage(get) -> str:
    return safe(get, "goal.stage") or DEFAULT_STAGE


def goal(key, kind, default, label, *, rescore=True, **kw) -> Setting:
    """A goal setting: versioned, and by default one that changes scores."""
    return Setting(key, kind, default, label, goal=True, rescore=rescore, **kw)


register(
    goal("goal.stage", "choice", DEFAULT_STAGE, "Career stage", choices=STAGES, order=10),
    goal("goal.study_status", "choice", "finished", "Are you studying now?", order=20,
         default_fn=lambda get: STUDY_BY_STAGE.get(_stage(get), "finished"), choices=choices(
             ("at_uni", "Yes, at university or college"), ("finished", "No, I have finished"),
             ("none", "I did not go to university"))),
    goal("goal.study_end", "text", "", "When your course ends or ended", order=30,
         help="A year such as 2026, or a month such as 2026-06."),
    goal("goal.kinds", "multi", KINDS_BY_STAGE[DEFAULT_STAGE], "Kinds of role", choices=KINDS, order=40,
         default_fn=lambda get: list(KINDS_BY_STAGE.get(_stage(get), KINDS_BY_STAGE[DEFAULT_STAGE]))),
    goal("goal.roles", "tags", [], "Roles you want", order=50),
    goal("goal.avoid_roles", "tags", [], "Roles to avoid", order=60),
    goal("goal.interests", "tags", [], "Things you are interested in", order=70),
    goal("goal.places", "ordered_tags", [], "Places, in order of preference", order=80,
         help=f"Put your favourite first. \"{REMOTE_UK}\" counts as a place."),
    goal("goal.place_aliases", "textlist", [], "Place names that count as each place", advanced=True, order=90,
         help="One place a line, such as \"Leeds: Headingley, Horsforth\"."),
    goal("goal.max_distance", "range", 25, "How far you will travel", min=5, max=60, unit="miles", order=100),
    goal("goal.pattern", "seg", "any", "Working pattern", order=110, choices=choices(
        ("office", "Office"), ("hybrid", "Hybrid"), ("remote", "Remote"), ("any", "Any"))),
    goal("goal.min_pay", "money", 0, "Lowest pay you will consider", order=120,
         help="Type it any way: 24000, \u00a324,000 or 24k."),
    goal("goal.unpaid_ok", "bool", False, "Show unpaid roles", order=130,
         help="Unpaid roles are hidden unless you switch this on."),
    goal("goal.avoid_sectors", "tags", ["gambling", "tobacco", "arms and defence"], "Sectors to avoid", order=140,
         help="Pick from Hal's list or type your own words."),
    goal("goal.avoid_employers", "textlist", [], "Employers to avoid", order=150,
         help="One employer a line. Lines starting with # are headings."),
    goal("goal.mode", "seg", "actively", "How hard you are looking", rescore=False, help=MODE_HELP, order=160,
         choices=choices(("actively", "Actively"), ("getting_ready", "Getting ready"), ("watching", "Just watching"))),
    goal("goal.experience_years", "int", 0, "Years of work experience you have", min=0, max=MAX_YEARS, order=170,
         default_fn=lambda get: YEARS_BY_STAGE.get(_stage(get), 0)),
    goal("goal.has_clearance", "bool", False, "I hold a security clearance", advanced=True, order=180),
    goal("goal.agencies", "choice", "flag", "Recruitment agencies", advanced=True, order=190, choices=choices(
        ("flag", "Show them, marked"), ("lower", "Show them lower"), ("hide", "Hide them"))),
    goal("goal.training_schemes", "choice", "lower", "Schemes that make you repay training costs", advanced=True,
         choices=SCHEME_CHOICES, order=200),
    goal("goal.training_providers", "textlist", [], "Training providers to treat as such schemes", advanced=True,
         order=210),
    goal("goal.training_ok_places", "tags", [], "Places where those schemes are fine", advanced=True, order=220),
    Setting("goal.history", "link", None, "Earlier versions of your goal", to="settings-goal", order=230),
)

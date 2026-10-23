"""Settings group "How Hal picks jobs": the points each rule gives.

The six weights shown first (first_place, role_title, entry_level, fresh, pay_under, years_required) are
in the open; the rest are under "Show more". The defaults are the technical-graduate preset, which
reproduces the scores of the first version.
"""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, safe, uses_local_ai
from jobhunt.core.settings.registry import Setting, register

PRESETS = choices(
    ("technical_grad", "Technical graduate"), ("humanities_grad", "Humanities graduate"),
    ("career_changer", "Career changer"), ("experienced", "Experienced"), ("school_leaver", "School leaver"),
    ("internship_seeker", "Student looking for internships"))
# The stage suggests a preset; a new graduate gets the technical one because it reproduces today's scores.
PRESET_BY_STAGE = {"student": "internship_seeker", "new_grad": "technical_grad", "early": "technical_grad",
                   "experienced": "experienced", "changing": "career_changer", "school_leaver": "school_leaver"}
PREPARE_LOCAL = 5     # local AI costs nothing per document
PREPARE_CLAUDE = 0    # Claude spends the person's own allowance, so nothing is drafted unasked

# (name, label, default, min, max, shown in the open)
WEIGHTS = (
    ("first_place", "Your first-choice place", 10, 0, 20, True),
    ("other_place", "Your other places", 7, 0, 20, False),
    ("remote_uk", "Remote within the UK", 5, 0, 20, False),
    ("role_title", "A role you want in the job title", 12, 0, 20, True),
    ("role_text_only", "A role you want only in the advert text", -8, -20, 0, False),
    ("entry_level", "Graduate or entry-level role", 8, 0, 30, True),
    ("kind_wanted", "A kind of role you chose, such as a paid internship", 0, 0, 20, False),
    ("kind_also", "Part-time or fixed-term when you did not choose them", -3, -10, 0, False),
    ("fresh", "Advert posted in the last 3 days", 6, 0, 10, True),
    ("old", "Advert older than 45 days", -8, -20, 0, False),
    ("stale", "Advert older than 180 days", -15, -30, 0, False),
    ("pay_under", "Pay under your lowest", -12, -30, 0, True),
    ("years_required", "Asks for 2 or more years of experience", -15, -40, 0, True),
    ("interest", "Each of your interests the advert mentions", 3, 0, 10, False),
    ("no_interest", "Mentions none of your interests", -10, -20, 0, False),
    ("pattern_match", "The working pattern you prefer", 5, 0, 10, False),
    ("pattern_other", "A pattern you did not choose", -3, -10, 0, False),
    ("small_employer", "Small employer, 15 or fewer open roles", 7, 0, 10, False),
    ("niche_source", "Found on a small, specialist site", 6, 0, 10, False),
    ("alert_only", "Known only from an alert email", -10, -20, 0, False),
    ("training_scheme", "A scheme that makes you repay training costs", -12, -20, 0, False),
)

register(
    Setting("scoring.preset", "choice", "technical_grad", "Starting point", choices=PRESETS, rescore=True,
            help="A preset fills in the points below. Change any of them after.", order=10,
            default_fn=lambda get: PRESET_BY_STAGE.get(safe(get, "goal.stage") or "")),
    Setting("scoring.base", "int", 40, "Starting points for every job", rescore=True, advanced=True, order=20),
    *(Setting(f"scoring.w.{name}", "range", default, label, min=low, max=high, unit="points", rescore=True,
              advanced=not shown, order=30 + n)
      for n, (name, label, default, low, high, shown) in enumerate(WEIGHTS)),
    Setting("scoring.interest_cap", "int", 18, "Most points from interests", rescore=True, advanced=True,
            min=0, order=60),
    Setting("scoring.skills", "textlist", [], "Skills to look for, with points", rescore=True, advanced=True,
            help="One skill a line, such as \"python = 3\". The starting point fills these in.", order=70),
    Setting("scoring.skill_cap", "int", 20, "Most points from skills", rescore=True, advanced=True, min=0, order=80),
    Setting("scoring.min_show", "range", 45, "Lowest score to show in Matches", min=0, max=90, order=90),
    Setting("scoring.prepare_ahead", "choice", PREPARE_CLAUDE, "Prepare documents ahead for your best matches",
            order=100, choices=choices((0, "None"), (3, "3 jobs"), (5, "5 jobs"), (10, "10 jobs")),
            default_fn=lambda get: PREPARE_LOCAL if uses_local_ai(get) else PREPARE_CLAUDE),
    Setting("scoring.screen", "bool", True, "Ask the AI whether each new match fits", advanced=True, order=110),
    Setting("scoring.screen_min", "int", 50, "Only ask about jobs scoring at least", advanced=True, min=0, max=100,
            order=120),
    Setting("scoring.rescore", "button", None, "Re-check every open job with these points", action="find.rescore",
            help="Re-check N jobs", order=130),
)

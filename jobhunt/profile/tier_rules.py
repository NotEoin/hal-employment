"""The tiers, their rules (`rule`) and the tier a shaped item shows (`shown`); `profile.tiers` explains them.

Kept apart from `profile.tiers`, which reads and writes the store, because the store shapes every item with
`shown`: this module imports nothing that imports the store, so the imports run one way.
"""
from __future__ import annotations

import re

from jobhunt.profile import strength

CORE, SUPPORTING, ARCHIVE = "core", "supporting", "archive"
TIERS = (CORE, SUPPORTING, ARCHIVE)
TIERED_KINDS = ("job", "education", "project", "achievement", "certificate")
BY_HAL, BY_PERSON = "hal", "person"
RULE_BASIS = "rule"
TIER_WORDS = {CORE: "Lead with this", SUPPORTING: "Useful when a job asks", ARCHIVE: "Archived", "": "Not sorted yet"}
WHY_HIDDEN = "You hid it"
WHY_NOT_OWN = "Only in files that are not your own"
WHY_SCHOOL = "A school qualification"
WHY_PERSON = "You chose this"
# A school-age qualification: useful when a job asks for it, never the lead (2026-11-07).
SCHOOL = re.compile(r"\b(?:i?gcses?|o[ -]?levels?|school[ -]leaving|leaving cert(?:ificate)?|secondary school)\b",
                    re.I)


def rule(item: dict) -> tuple[str, str] | None:
    """(tier, why) when a rule decides the shaped `item`'s tier, else None (see `profile.tiers`)."""
    if item["kind"] not in TIERED_KINDS:
        return None
    if item["state"] == "hidden":
        # a card Hal folded into another (`store.fold_away`) was not hidden by the person
        return ARCHIVE, (item.get("merged_why") if item.get("merged_into") else "") or WHY_HIDDEN
    if item["origin"] == "found" and item["state"] == "hal" and \
            not any(e.get("role", "") == strength.OWN for e in item["evidence"]):
        return ARCHIVE, WHY_NOT_OWN
    if item["kind"] in ("education", "certificate"):
        f = item["fields"]
        if SCHOOL.search(" ".join(str(f.get(k) or "") for k in ("qualification", "subject", "institution", "name"))):
            return SUPPORTING, WHY_SCHOOL
    return None


def shown(item: dict, stored: dict) -> dict:
    """The tier to show for a shaped `item`: {tier, tier_by, tier_why, tier_words}.

    `stored` holds the item's tier columns. The person's choice wins, then a rule, then the AI's judgement;
    a stored rule tier whose rule no longer applies is shown as not judged.
    """
    if item["kind"] not in TIERED_KINDS:
        tier, by, why = "", "", ""
    elif stored["tier_by"] == BY_PERSON and stored["tier"] in TIERS:
        tier, by, why = stored["tier"], BY_PERSON, stored["tier_why"] or WHY_PERSON
    elif (found := rule(item)) is not None:
        tier, by, why = found[0], BY_HAL, found[1]
    elif stored["tier_basis"] == RULE_BASIS or stored["tier"] not in TIERS:
        tier, by, why = "", "", ""
    else:
        tier, by, why = stored["tier"], BY_HAL, stored["tier_why"]
    words = TIER_WORDS[tier] if item["kind"] in TIERED_KINDS else ""
    return {"tier": tier, "tier_by": by, "tier_why": why, "tier_words": words}

"""Migration 0025: how much each experience in the profile is worth telling an employer.

`profile_items.tier` is `core`, `supporting`, `archive` or '' (not judged yet); `tier_by` is `hal` or
`person`; `tier_why` is a few plain words; `tier_basis` says what the tier was judged from (`rule`, or a
fingerprint of the item's evidence), so Hal judges a card again only when its evidence changes
(`profile.tiers`). Only jobs, education, projects, achievements and certificates have a tier.
"""

from jobhunt.core.db.migrate import add_column

TITLE = "Added which of your experiences to lead with"


def up(con):
    """Adds each profile item's tier, who set it, why, and what it was judged from."""
    for column in ("tier", "tier_by", "tier_why", "tier_basis"):
        add_column(con, "profile_items", column, "TEXT NOT NULL DEFAULT ''")

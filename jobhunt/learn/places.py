"""How far a job is from where the person lives, for the "Too far" pattern.

Scoring has no distances yet (find/rules.py matches places by name only), so learning carries a small table of
UK towns and cities with their centres and measures straight-line miles between two of them. A job whose
location names no town in the table has no distance, and a pattern never counts it as evidence: Hal suggests a
smaller distance only from jobs whose distance it actually knows.

Centres are rounded to two decimal places (about half a mile), which is far finer than the five-mile steps
the distance setting moves in.
"""
from __future__ import annotations

import math
import re

EARTH_RADIUS_MILES = 3958.8

# name: (latitude, longitude). Aliases share a centre with the name they stand for.
TOWNS: dict[str, tuple[float, float]] = {
    "aberdeen": (57.15, -2.09), "aberystwyth": (52.42, -4.08), "aylesbury": (51.82, -0.81),
    "bangor": (53.23, -4.13), "barnsley": (53.55, -1.48), "basingstoke": (51.27, -1.09), "bath": (51.38, -2.36),
    "batley": (53.71, -1.63), "bedford": (52.14, -0.47), "belfast": (54.60, -5.93), "birkenhead": (53.39, -3.02),
    "birmingham": (52.49, -1.89), "bishop auckland": (54.66, -1.68), "blackburn": (53.75, -2.48),
    "blackpool": (53.82, -3.04), "blyth": (55.13, -1.51), "bolton": (53.58, -2.43), "bournemouth": (50.72, -1.88),
    "bradford": (53.80, -1.76), "brighton": (50.82, -0.14), "bristol": (51.45, -2.59), "burnley": (53.79, -2.25),
    "burton upon trent": (52.81, -1.64), "bury": (53.59, -2.30), "cambridge": (52.21, 0.12),
    "canterbury": (51.28, 1.08), "cardiff": (51.48, -3.18), "carlisle": (54.89, -2.93),
    "castleford": (53.73, -1.36), "chelmsford": (51.74, 0.48), "cheltenham": (51.90, -2.08),
    "chester": (53.19, -2.89), "chester-le-street": (54.86, -1.57), "chesterfield": (53.24, -1.42),
    "chichester": (50.84, -0.78), "colchester": (51.89, 0.90), "consett": (54.85, -1.83),
    "coventry": (52.41, -1.51), "cramlington": (55.09, -1.59), "crawley": (51.11, -0.19), "crewe": (53.10, -2.44),
    "croydon": (51.38, -0.10), "darlington": (54.52, -1.55), "derby": (52.92, -1.48), "dewsbury": (53.69, -1.63),
    "doncaster": (53.52, -1.13), "dudley": (52.51, -2.08), "dundee": (56.46, -2.97), "durham": (54.78, -1.58),
    "eastbourne": (50.77, 0.29), "edinburgh": (55.95, -3.19), "exeter": (50.72, -3.53), "glasgow": (55.86, -4.25),
    "gloucester": (51.86, -2.24), "goole": (53.70, -0.87), "grimsby": (53.57, -0.08), "guildford": (51.24, -0.57),
    "halifax": (53.73, -1.86), "harlow": (51.77, 0.10), "harrogate": (53.99, -1.54), "hartlepool": (54.69, -1.21),
    "hastings": (50.86, 0.57), "hemel hempstead": (51.75, -0.45), "hereford": (52.06, -2.72),
    "hexham": (54.97, -2.10), "high wycombe": (51.63, -0.75), "huddersfield": (53.65, -1.79),
    "hull": (53.75, -0.34), "ilkley": (53.93, -1.82), "inverness": (57.48, -4.22), "ipswich": (52.06, 1.15),
    "keighley": (53.87, -1.91), "kendal": (54.33, -2.75), "kidderminster": (52.39, -2.25),
    "lancaster": (54.05, -2.80), "leamington spa": (52.29, -1.54), "leeds": (53.80, -1.55),
    "leicester": (52.64, -1.14), "lincoln": (53.23, -0.54), "liverpool": (53.41, -2.99), "london": (51.51, -0.13),
    "londonderry": (55.00, -7.31), "loughborough": (52.77, -1.21), "luton": (51.88, -0.42),
    "macclesfield": (53.26, -2.13), "maidstone": (51.27, 0.53), "manchester": (53.48, -2.24),
    "mansfield": (53.14, -1.20), "middlesbrough": (54.57, -1.24), "milton keynes": (52.04, -0.76),
    "morpeth": (55.17, -1.69), "newport": (51.59, -3.00), "northallerton": (54.34, -1.43),
    "northampton": (52.24, -0.90), "norwich": (52.63, 1.30), "nottingham": (52.95, -1.16),
    "nuneaton": (52.52, -1.47), "oldham": (53.54, -2.11), "otley": (53.91, -1.69), "oxford": (51.75, -1.26),
    "perth": (56.40, -3.44), "peterborough": (52.57, -0.24), "peterlee": (54.76, -1.34), "plymouth": (50.38, -4.14),
    "pontefract": (53.69, -1.31), "poole": (50.72, -1.99), "portsmouth": (50.82, -1.09), "preston": (53.76, -2.70),
    "reading": (51.45, -0.98), "redcar": (54.62, -1.07), "ripon": (54.14, -1.52), "rochdale": (53.62, -2.16),
    "rotherham": (53.43, -1.36), "rugby": (52.37, -1.26), "salford": (53.48, -2.29), "salisbury": (51.07, -1.80),
    "scarborough": (54.28, -0.40), "scunthorpe": (53.59, -0.65), "selby": (53.78, -1.07),
    "sheffield": (53.38, -1.47), "shrewsbury": (52.71, -2.75), "skipton": (53.96, -2.02), "slough": (51.51, -0.60),
    "solihull": (52.41, -1.78), "southampton": (50.91, -1.40), "southend-on-sea": (51.55, 0.71),
    "southport": (53.65, -3.01), "st albans": (51.75, -0.34), "st helens": (53.45, -2.74),
    "stafford": (52.81, -2.12), "stevenage": (51.90, -0.20), "stirling": (56.12, -3.94),
    "stockport": (53.41, -2.16), "stockton-on-tees": (54.57, -1.32), "stoke-on-trent": (53.00, -2.18),
    "stratford-upon-avon": (52.19, -1.71), "swansea": (51.62, -3.94), "swindon": (51.56, -1.78),
    "taunton": (51.02, -3.10), "telford": (52.68, -2.45), "torquay": (50.46, -3.53), "truro": (50.26, -5.05),
    "wakefield": (53.68, -1.50), "walsall": (52.59, -1.98), "warrington": (53.39, -2.60), "warwick": (52.28, -1.59),
    "watford": (51.66, -0.39), "west bromwich": (52.52, -2.00), "wetherby": (53.93, -1.39),
    "weymouth": (50.61, -2.46), "whitby": (54.49, -0.62), "wigan": (53.55, -2.63), "winchester": (51.06, -1.31),
    "woking": (51.32, -0.56), "wolverhampton": (52.59, -2.13), "worcester": (52.19, -2.22),
    "worthing": (50.82, -0.37), "wrexham": (53.05, -2.99), "yeovil": (50.94, -2.63), "york": (53.96, -1.08),
}
ALIASES = {
    "kingston upon hull": "hull", "derry": "londonderry", "southend": "southend-on-sea",
    "stockton": "stockton-on-tees", "stoke": "stoke-on-trent", "royal leamington spa": "leamington spa",
    "burton": "burton upon trent",
}
# Longest names first, so "stoke-on-trent" wins over "stoke" and "chester-le-street" over "chester".
_NAMES = sorted([*TOWNS, *ALIASES], key=len, reverse=True)
_RX = re.compile(r"(?<![a-z-])(" + "|".join(re.escape(n) for n in _NAMES) + r")(?![a-z-])")


def town_in(text: str | None) -> str | None:
    """The first town of the table named in `text` (a job location or a home town), or None."""
    m = _RX.search((text or "").lower())
    if m is None:
        return None
    return ALIASES.get(m.group(1), m.group(1))


def miles_between(a: str, b: str) -> float:
    """Straight-line miles between two towns of the table."""
    (lat1, lon1), (lat2, lon2) = TOWNS[a], TOWNS[b]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


def distance(home: str | None, location: str | None) -> float | None:
    """Miles from `home` to the town a job's location names; None when either is not in the table."""
    here, there = town_in(home), town_in(location)
    if here is None or there is None:
        return None
    return miles_between(here, there)


def display_name(town: str) -> str:
    """A table name as it is written ("stoke-on-trent" -> "Stoke-on-Trent", "st albans" -> "St Albans")."""
    small = {"upon", "on", "le", "in"}
    return "-".join(" ".join(w if w in small else w.capitalize() for w in part.split(" "))
                    for part in town.split("-"))

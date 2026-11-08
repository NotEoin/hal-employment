"""Two made-up graduates' files for the sectioned record's tests: a software graduate and a history graduate.

Each has a current CV and an older one. The older CV lists an extra old job the newer one dropped, gives a
detail the newer one leaves out (a job's town, a skill) and disagrees with it on others (a grade, a mark), so
a test can see that an older CV only fills gaps. Every name, employer, place and address is invented; email
addresses use the reserved `example.org` domain and phone numbers Ofcom's drama range.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Graduate:
    """One made-up graduate's files, by what they are."""

    name: str
    cv: str
    older_cv: str


AVERY = Graduate(
    name="Avery Quillon",
    cv="""# Avery Quillon

avery.quillon@example.org | 07700 900481 | Fenmouth

CV, June 2025

## Work experience

Junior developer, Made-up Tides Ltd, Sept 2024 - present
- Built a tide alert service in Python

Lab helper, University of Nowhere Fen, Oct 2022 - June 2024
- Helped first-year students in programming labs

## Education

BSc (Hons) Computing, University of Nowhere Fen, 2021 - 2024, First
Modules: Databases (70), Algorithms (65)

## Skills

Programming: Python, SQL

## Projects

Tide gauge
- A tide gauge that reads a made-up bay's tides
""",
    older_cv="""# Avery Quillon

avery.quillon@example.org

CV, January 2023

## Work experience

Lab helper, University of Nowhere Fen, Fenmouth, Oct 2022 - present
- Marked lab sheets for the first years

Cafe assistant, Made-up Beans Cafe, Fenmouth, 2019 - 2021
- Served coffee and ran the till

## Education

BSc Computing, University of Nowhere Fen, 2021 - present, 2:1
Modules: Databases (60)

## Skills

Programming: Python, Java
""")

MARNIE = Graduate(
    name="Marnie Quillfeather",
    cv="""# Marnie Quillfeather

marnie.quillfeather@example.org | 07700 900123 | Harrowgate

CV, March 2025

## Work experience

Archive volunteer, Fenwick Lantern Museum Trust, Sept 2023 - present
- Catalogued 400 letters from the 1840s
- Guided visitors around the reading room

Shop assistant, Made-up Books Ltd, June 2021 - August 2023
- Advised customers on local history titles

## Education

BA (Hons) History, University of Nowhere Fen, 2021 - 2024, 2:1
Modules: Medieval Europe (68), Archival Methods (72)

## Skills

Research: archival research, palaeography
Languages: French
""",
    older_cv="""# Marnie Quillfeather

marnie.quillfeather@example.org

CV, May 2022

## Work experience

Shop assistant, Made-up Books Ltd, Harrowgate, June 2021 - present
- Ran the till on Saturdays

Paper round, Harrowgate Gazette, Harrowgate, 2018 - 2020
- Delivered papers before school

## Education

BA History, University of Nowhere Fen, 2021 - present
Modules: Medieval Europe (55)

## Skills

Languages: French, German
""")

GRADUATES = (AVERY, MARNIE)

"""Two made-up graduates' files for the sectioned record's tests: a software graduate and a history graduate.

Each has a current CV, an older one, a cover letter or personal statement, a transcript (whose marks differ
from the CV's), a certificate, a reference, and a contract or job description for one job. The older CV
lists an extra old job the newer one dropped, gives a detail the newer one leaves out (a job's town, a skill)
and disagrees with it on others (a grade, a mark), so a test can see that an older CV only fills gaps. Every
name, employer, place and address is invented; email addresses use the reserved `example.org` domain and
phone numbers Ofcom's drama range.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Graduate:
    """One made-up graduate's files, by what they are."""

    name: str
    cv: str
    older_cv: str
    letter: str = ""
    transcript: str = ""
    certificate: str = ""
    reference: str = ""
    job_document: str = ""         # a contract or job description for one job


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
""",
    letter="""# Letter to Made-up Harbour Software

12 February 2025

Dear hiring team,

I would like to join Made-up Harbour Software as a graduate developer. At Made-up Tides Ltd I built a tide
alert service in Python, and I enjoy turning messy readings into something people can trust.

Yours sincerely,
Avery Quillon
""",
    transcript="""# Academic transcript

University of Nowhere Fen

Student: Avery Quillon
Programme: BSc (Hons) Computing
Period of study: 2021 - 2024
Final classification: First

| Code | Module | Credits | Mark |
| --- | --- | --- | --- |
| CSC2010 | Databases | 20 | 74 |
| CSC2020 | Algorithms | 20 | 68 |
| CSC3099 | Dissertation | 40 | 77 |
""",
    certificate="""# Certificate in Data Protection Essentials

This is to certify that Avery Quillon has completed the course.

Issued by the Made-up Data Institute

5 March 2024
""",
    reference="""# Reference for Avery Quillon

To whom it may concern,

Avery has worked with us as a Junior developer at Made-up Tides Ltd since September 2024. Avery is careful,
asks good questions and writes tests without being asked.

Jo Brackwater
Lead developer, Made-up Tides Ltd
""",
    job_document="""# Job description

Job title: Junior developer
Employer: Made-up Tides Ltd

## Main duties

- Answer customer questions about tide data
- Review other developers' code
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
""",
    letter="""# Personal statement

March 2025

Old letters fascinate me because they keep the voices that official records leave out. Volunteering at the
Fenwick Lantern Museum Trust, I catalogued 400 of them and learned to read the hands of the 1840s.
""",
    transcript="""# Transcript of study

University of Nowhere Fen

Programme: BA (Hons) History
Final classification: Upper second class (2:1)
Date of award: 1 July 2024

HIS1010 Medieval Europe 66
HIS2040 Archival Methods 75
HIS3001 Dissertation: The parish registers of Harrowgate Vale 71
""",
    certificate="""# Certificate in Archive Handling

Awarded to Marnie Quillfeather by the Made-up Records Society.

12 May 2024
""",
    reference="""# Reference

To whom it may concern,

I am pleased to recommend Marnie Quillfeather, who has volunteered as an Archive volunteer at the Fenwick
Lantern Museum Trust since September 2023. Her care with fragile letters has been exemplary.

Sam Oakhurst
Archivist, Fenwick Lantern Museum Trust
""",
    job_document="""# Contract of employment

Employer: Harrowgate Gazette
Position: Paper delivery round
Start date: 1 March 2018
End date: 31 August 2020

Your duties are delivering papers, collecting payments and keeping the round book.
""")

GRADUATES = (AVERY, MARNIE)

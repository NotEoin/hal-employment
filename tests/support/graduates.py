"""Two made-up graduates' files for the sectioned record's tests: a software graduate and a history graduate.

Each has a current CV, an older one, a cover letter or personal statement, a transcript (whose marks differ
from the CV's), a certificate, a reference, and a contract or job description for one job. The older CV
lists an extra old job the newer one dropped, gives a detail the newer one leaves out (a job's town, a skill)
and disagrees with it on others (a grade, a mark), so a test can see that an older CV only fills gaps.

For the education sections, `COURSES` holds their courses as cards (the software graduate's Access to HE
Diploma and BSc, the history graduate's BA), with their coursework, lecture material and official papers as
texts below it: a report and code, two dissertations, essays, slides and seminar notes. `education(con, key)`
adds a course's card as a read of the person's CV would, and returns its id.

`sectioned(con, who, folder)` builds a graduate's whole record through the sections (CVs, a letter, a course
with a starred dissertation, other coursework, lecture material and a starred project: the software
graduate's code project, the history graduate's exhibition added by hand), for whole-pack tests.

Every name, employer, place, module code, mark and address is invented; email addresses use the reserved
`example.org` domain and phone numbers Ofcom's drama range.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from jobhunt.profile import store


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


# The education sections' courses and files -----------------------------------------------------------------------

CV_REL = "Sources/CVs/Made-up CV.md"

COURSES = {
    "access": {"qualification": "Access to HE Diploma", "subject": "Science", "institution": "Fenbridge College",
               "start": "2020-09", "end": "2021-06"},
    "bsc": {"qualification": "BSc", "subject": "Made-up Computing", "institution": "University of Nowhere Fen",
            "start": "2021-09", "end": "2024-06",
            "modules": [{"name": "Programming Foundations (COMP1010)"}, {"name": "Data Analysis (COMP2040)"},
                        {"name": "Dissertation (COMP3990)"}]},
    "ba": {"qualification": "BA", "subject": "History", "institution": "University of Nowhere Fen",
           "start": "2021-09", "end": "2024-06",
           "modules": [{"name": "Early Modern Records (HIST2040)"}, {"name": "Rivers and Trade (HIST2110)"},
                       {"name": "Dissertation (HIST3990)"}]},
}

TRANSCRIPT = """# University of Nowhere Fen: academic transcript

Student: Avery Quillon
Programme: BSc Made-up Computing
Date of issue: 30 June 2024

| Code | Module | Credits | Mark |
| --- | --- | --- | --- |
| COMP1010 | Programming Foundations | 20 | 71 |
| COMP2040 | Data Analysis | 20 | 68 |
| COMP3990 | Dissertation | 40 | 74 |

Classification: First Class Honours
"""

CERTIFICATE = """# Degree certificate

The University of Nowhere Fen confers on Marnie Quillfeather the degree of Bachelor of Arts in History
with Upper Second Class Honours.

Awarded 12 July 2024.
"""

# The software graduate's report for COMP2040, which states its mark.
REPORT = """# Rainfall and ferry delays on the Fen

COMP2040 coursework report, March 2024.

## Abstract

This report models how rainfall delays the made-up Fen ferries. I cleaned two years of timetable data and
fitted a linear regression in R, which explained most of the delays on wet days.

## Method

I used regression in R and plotted residuals with ggplot2.

Mark: 68
"""

# The software graduate's code, uploaded as a folder: one source of two files.
CODE = {"tide-model/README.md": "# Tide model\n\nA small program that predicts high tide at Fen harbour from "
                                "made-up gauge readings, written for COMP1010 in November 2021.\n",
        "tide-model/tide.py": "def high_tide(readings):\n    return max(readings)\n"}

DISSERTATION = """# Predicting ferry delays with open weather data

Dissertation, BSc Made-up Computing, May 2024.

## Abstract

This dissertation builds a model that predicts ferry delays from open weather data. I collected six months
of made-up sailings, trained a random forest in Python and compared it with a simple timetable baseline.
"""

SLIDES = """# COMP2040 Week 3: Linear regression

Dr Ivo Pellingrove, ivo.pellingrove@example.org, room 2.14

## Fitting a line

Least squares finds the line that makes the squared residuals smallest.

## Checking the fit

Plot the residuals against the fitted values.
"""

ESSAY = """# The parish registers of Harrowgate Vale

HIST2040 essay, submitted 14 March 2023.

Close reading of the baptism registers of Harrowgate Vale, 1750 to 1850, shows how the parish grew after the
made-up canal opened. I compared the registers with tithe maps through archival research at the county record
office.

Mark: 72%
"""

HISTORY_DISSERTATION = """# Bread, rivers and riot: grain prices on the Fen, 1790 to 1801

Dissertation for the BA History, April 2024.

This dissertation reads the made-up Fen corn returns and quarter sessions papers to ask why grain riots
followed the floods of 1795. Archival research in three record offices and a thematic analysis of
magistrates' letters show that prices, not the floods themselves, set the riots off.
"""

SEMINAR_NOTES = """# HIST2110 Seminar 4: Rivers and trade

Notes from the seminar.

## Navigation acts

How the made-up Fen navigation acts changed who could carry grain.

## Reading port books

Port books record cargoes, masters and duties paid.
"""


def education(con: sqlite3.Connection, key: str) -> int:
    """Adds one course of `COURSES` as a read of the person's CV would; its card's id."""
    return store.add_found(con, "education", dict(COURSES[key]), {"source_rel": CV_REL, "doc_type": "cv",
                                                                  "role": "own"})


# Whole sectioned records -------------------------------------------------------------------------------------------

# The history graduate's exhibition, added by hand on Projects.
EXHIBITION = {"name": "Lantern letters exhibition",
              "what": "A made-up exhibition of 40 letters from the Fenwick archive, with labels I wrote for "
                      "each letter.",
              "when": "Spring 2025", "part": "Curator", "tools": "archival research, label writing"}


def _upload(con: sqlite3.Connection, section: str, name: str, text: str, kind: str, target: int | None = None,
            **changes) -> int:
    """`text` added to `section` as the file `name`, changed by `changes` (a star, the dissertation toggle) and
    read; its source's id."""
    from jobhunt.sections import pipeline
    from jobhunt.sections import store as sources

    source_id = pipeline.add_uploads(con, section, [pipeline.Incoming(name, text.encode())], doc_kind=kind,
                                     target_id=target)["added"][0]
    for key, value in changes.items():
        sources.change(con, source_id, {key: value})
    pipeline.read(con, source_id)
    return source_id


def sectioned(con: sqlite3.Connection, who: Graduate, folder: Path) -> None:
    """Builds `who`'s whole record through the sections, as the person would on Your record, and checks every
    card: their CVs, their letter, a course with a starred dissertation, lecture material and a starred project
    (Avery's code project in `folder`, Marnie's exhibition added by hand). Run it with a fake AI in place."""
    from jobhunt.sections import pipeline
    from jobhunt.sections import store as sources

    from . import repos

    _upload(con, "cv", "cv.md", who.cv, "cv")
    _upload(con, "cv", "older-cv.md", who.older_cv, "cv")
    _upload(con, "letter", "letter.md", who.letter, "cover_letter" if who is AVERY else "statement")
    if who is AVERY:
        course = education(con, "bsc")
        _upload(con, "coursework", "ferries.md", DISSERTATION, "dissertation", course, starred=True)
        _upload(con, "coursework", "rainfall.md", REPORT, "report", course)
        _upload(con, "lecture", "week3.md", SLIDES, "slides", course)
        code = repos.make(folder / "tide-model", {k.split("/", 1)[1]: v for k, v in CODE.items()})
        project = pipeline.add_link(con, "project", str(code), doc_kind="repo")["added"][0]
    else:
        course = education(con, "ba")
        _upload(con, "coursework", "bread.md", HISTORY_DISSERTATION, "dissertation", course, starred=True)
        _upload(con, "coursework", "registers.md", ESSAY, "essay", course)
        _upload(con, "lecture", "seminar4.md", SEMINAR_NOTES, "notes", course)
        project = pipeline.add_link(con, "project", EXHIBITION["name"], doc_kind="manual")["added"][0]
        sources.change(con, project, {"form": EXHIBITION})
    pipeline.read(con, project)
    for kind in store.KINDS:
        ids = [it["id"] for it in store.items(con, kind, hidden=False)]
        if ids:
            store.confirm_many(con, ids)
    for card in store.items(con, "project", hidden=False):
        if not card["star"] and any(e.get("section") == "project" for e in card["evidence"]):
            store.set_star(con, card["id"], True)

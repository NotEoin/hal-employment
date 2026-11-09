"""Sort a big folder for me: where each file and project folder of a dump is proposed to go, and that a proposal
is never a fact.

Two made-up dumps: Avery Quillon's (software: CVs, a degree's lecture notes and coursework, a dissertation saved
twice, a code project and its older copy, a certificate, a GP letter) and Marnie Quillfeather's (history: a CV,
seminar notes, an essay and its copy, a transcript, a reference, an exhibition project folder, a fit note).
Every name, place and file is invented. The AI is the fake one, answering the `sort_pick` prompt.
"""
import json
import os
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from support.fake_ai import fake_ai
from test_ingest import home, put
from test_record import tiny_pdf

from jobhunt.core import settings
from jobhunt.profile import store as profile_store
from jobhunt.sections import pipeline, sort_copies, sort_kinds, sort_review, sort_rules, sort_units, sorter, sorts

NONE = {"sort_pick": json.dumps({"choice": "none"})}
DAY_S = 24 * 3600
WORDS = "This made-up essay weighs the sources and the evidence on each side of the question. " * 12
OLD, NEW = 1_700_000_000, 1_730_000_000      # November 2023 and October 2024
README = "# Tidewatch\n\nA tide-gauge dashboard for a harbour club. " + "It reads gauge data and draws charts. " * 22
CV = ("# {name}\n\nCurriculum vitae\n\n## Work experience\n\n{job}\n\n## Education\n\n{degree}, University of Nowhere "
      "Fen, 2021 to 2024.\n\n## Skills\n\n{skills}\n")
AVERY_CV = CV.format(name="Avery Quillon", job="Junior developer, Made-up Tides Ltd, 2024.",
                     degree="BSc Computing", skills="Python, SQL")
AVERY_OLD_CV = CV.format(name="Avery Quillon", job="Shop assistant, Made-up Corner Shop, 2022.",
                         degree="BSc Computing", skills="Python")
MARNIE_CV = CV.format(name="Marnie Quillfeather", job="Visitor guide, Fenwick Lantern Museum Trust, 2023.",
                      degree="BA History", skills="Archival research")
GP_LETTER = ("# Statement of fitness for work\n\nPatient name: Made-up Person\n\nDiagnosed with a made-up "
             "condition. Not fit for work for two weeks. Repeat prescription at the clinic.\n")
TRANSCRIPT = ("# Transcript of study\n\nMarnie Quillfeather, BA History.\n\n| Module | Credits | Mark |\n"
              "|---|---|---|\n| HIS2040 Archival Methods | 20 | 72 |\n")
REFERENCE = ("# Reference for Marnie Quillfeather\n\nTo whom it may concern,\n\nI have known Marnie for two years "
             "at the Fenwick Lantern Museum Trust and recommend her without hesitation.\n")
CERTIFICATE = "# First aid\n\nThis is to certify that Avery Quillon has completed a made-up first aid course.\n"


def committed(folder: Path, author: tuple[str, str], date: str) -> Path:
    """Makes a folder of made-up files a code project with one commit by `author` at `date`."""
    repos.git(folder, "init", "-q")
    repos.git(folder, "add", "-A", author=author, date=date)
    repos.git(folder, "commit", "-q", "-m", "work", author=author, date=date)
    return folder


def dated(path: Path, when: float) -> Path:
    """Sets a file's time, so which copy is newest is known."""
    os.utime(path, (when, when))
    return path


def paper(title: str, venue: str) -> bytes:
    """The first page of a made-up published paper, as a PDF."""
    return tiny_pdf([title, "Ada Brindle, Cole Fenwick and Dana Hollis", venue, "Abstract",
                     "We study a made-up question with a made-up method.", "As Smith et al. and Jones et al. show,",
                     "the results hold.", "References", "Smith, A. et al. (2019). A made-up paper."])


def notes_folders(d: Path, parent: str, names: tuple[str, ...]) -> None:
    """Sibling folders of made-up notes under `parent`, each with a README."""
    for name in names:
        put(d, f"{parent}/{name}/README.md", f"# {name}\n\nMade-up notes on {name}, with dates and sources.\n")
        put(d, f"{parent}/{name}/timeline.md", f"# Timeline\n\nWhat happened, made up, for {name}.\n")


def with_libraries(d: Path, project: str, readme: str, libraries: tuple[str, ...]) -> None:
    """A project folder of the person's own with a README, holding made-up third-party libraries in its build
    folder."""
    put(d, f"{project}/README.md", readme)
    for name in libraries:
        put(d, f"{project}/_build/libs/{name}/README.md", f"# {name}\n\nA made-up library.\n")
        put(d, f"{project}/_build/libs/{name}/package.json", f'{{"name": "{name.lower()}"}}\n')


def software_dump(root: Path) -> Path:
    """Avery Quillon's folder of everything."""
    d = root / "Avery stuff"
    dated(put(d, "CV/Avery Quillon CV.md", AVERY_CV), NEW)
    dated(put(d, "CV/Avery Quillon CV (1).md", AVERY_CV), OLD)                 # the same bytes again
    dated(put(d, "CV/old/Avery CV 2022.md", AVERY_OLD_CV), OLD)
    put(d, "Applications/Cover letter Made-up Tides.md", "Dear hiring team,\n\nI would like to apply.\n")
    course = "Uni/BSc Computing/Year 2/CS2041 Databases"
    for week in (1, 2, 3):
        put(d, f"{course}/Lectures/week {week} notes.md", f"# Week {week}\n\nNormal forms and joins, part {week}.\n")
    put(d, f"{course}/Coursework/Database design report.md", "# Database design report\n\nMy design for a "
                                                             "made-up library, with an ER diagram.\n\n" + WORDS)
    thesis = "Uni/BSc Computing/Year 3/Dissertation"
    dated(put(d, f"{thesis}/Dissertation final.md", "# Tide prediction\n\n" + WORDS + "Final.\n"), NEW)
    dated(put(d, f"{thesis}/Dissertation.md", "# Tide prediction\n\n" + WORDS + "Draft.\n"), OLD)
    for name, when in (("tidewatch", NEW), ("tidewatch (2)", OLD)):
        put(d, f"Projects/{name}/README.md", README)
        put(d, f"Projects/{name}/pyproject.toml", "[project]\nname = \"tidewatch\"\n")
        put(d, f"Projects/{name}/src/gauge.py", "def read():\n    return 1\n")
        put(d, f"Projects/{name}/src/charts.py", "def draw():\n    return 2\n")
        for f in (d / "Projects" / name).rglob("*"):
            dated(f, when)
    committed(d / "Projects/tidewatch", repos.AVERY, "2024-10-27T10:00:00Z")
    put(d, "Certificates/First aid certificate.md", CERTIFICATE)
    put(d, "Medical/GP letter.md", GP_LETTER)
    put(d, "Downloads/beach.jpg", b"\xff\xd8\xff\xe0made-up picture bytes")
    put(d, "Downloads/model.bin", b"\x00\x01weights")
    put(d, "Misc/thoughts.md", "Some thoughts on the weekend.\n")
    return d


def history_dump(root: Path) -> Path:
    """Marnie Quillfeather's folder of everything."""
    d = root / "Marnie files"
    put(d, "Marnie Quillfeather CV.md", MARNIE_CV)
    course = "BA History/HIS2040 Archival Methods"
    for week in (1, 2, 3):
        put(d, f"{course}/Seminar notes week {week}.md",
            f"# Seminar {week}\n\nReading parish registers, part {week}.\n")
    essay = "# The parish registers of Harrowgate Vale\n\nAn essay on baptisms, 1750 to 1850.\n\n" + WORDS
    dated(put(d, f"{course}/Essays/Parish registers essay.md", essay), NEW)
    dated(put(d, f"{course}/Essays/Parish registers essay (1).md", essay), OLD)
    put(d, "BA History/Transcript of study.md", TRANSCRIPT)
    put(d, "Volunteering/Reference from the museum.md", REFERENCE)
    put(d, "Portfolio/Lantern exhibition/README.md", "# Lantern exhibition\n\nAn exhibition I curated.\n")
    put(d, "Portfolio/Lantern exhibition/catalogue.md", "# Catalogue\n\nTwelve lanterns, made-up.\n")
    put(d, "Health/Fit note.md", GP_LETTER)
    return d


def by_rel(found: sorter.Sorted) -> dict[str, sorter.Proposal]:
    """The proposals by their path inside the folder sorted."""
    return {p.rel: p for p in found.proposals}


def where(p: sorter.Proposal) -> tuple:
    """Where a proposal goes: (state, section, kind of document)."""
    return p.state, p.section, p.doc_kind


class Sorter(unittest.TestCase):
    def education(self, con, qualification: str, subject: str) -> int:
        """A degree on a made-up CV; its card's id."""
        return profile_store.add_found(con, "education", {"qualification": qualification, "subject": subject,
                                                          "institution": "University of Nowhere Fen",
                                                          "start": "2021", "end": "2024"},
                                       {"source_rel": "Sources/CVs/CV.md", "doc_type": "cv"})

    def facts(self, con) -> tuple[int, int, int]:
        """Cards, evidence and sources in the record."""
        return tuple(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("profile_items", "profile_evidence", "record_sources"))

    def test_a_software_graduates_dump_is_proposed_section_by_section(self):
        with home() as (con, vault, files), fake_ai({"sort_pick": json.dumps({"choice": "none"})}) as backend:
            settings.set("you.name", "Avery Quillon")
            degree = self.education(con, "BSc", "Computing")
            before = self.facts(con)
            found = by_rel(sorter.propose(con, software_dump(files), local=True))
            d = "Avery stuff/"
            self.assertEqual(where(found[d + "CV/Avery Quillon CV.md"]), ("proposed", "cv", "cv"))
            self.assertEqual(where(found[d + "CV/old/Avery CV 2022.md"]), ("proposed", "cv", "cv"),
                             "an older CV with other words is proposed too: it fills gaps")
            copy = found[d + "CV/Avery Quillon CV (1).md"]
            self.assertEqual((copy.state, copy.copy_of), ("copy", d + "CV/Avery Quillon CV.md"))
            self.assertEqual(where(found[d + "Applications/Cover letter Made-up Tides.md"]),
                             ("proposed", "letter", "cover_letter"))
            lecture = found[d + "Uni/BSc Computing/Year 2/CS2041 Databases/Lectures/week 2 notes.md"]
            self.assertEqual((*where(lecture), lecture.target_id), ("proposed", "lecture", "notes", degree))
            report = found[d + "Uni/BSc Computing/Year 2/CS2041 Databases/Coursework/Database design report.md"]
            self.assertEqual((*where(report), report.target_id), ("proposed", "coursework", "report", degree))
            final = found[d + "Uni/BSc Computing/Year 3/Dissertation/Dissertation final.md"]
            self.assertEqual((*where(final), final.target_id), ("proposed", "coursework", "dissertation", degree))
            self.assertEqual(found[d + "Uni/BSc Computing/Year 3/Dissertation/Dissertation.md"].state, "copy",
                             "the older save of the dissertation is a copy of the newest")
            repo = found[d + "Projects/tidewatch"]
            self.assertEqual((*where(repo), repo.held, repo.star), ("proposed", "project", "repo", "folder", True))
            self.assertEqual(found[d + "Projects/tidewatch (2)"].copy_of, d + "Projects/tidewatch")
            self.assertNotIn(d + "Projects/tidewatch/src/gauge.py", found, "a project's files are the project")
            self.assertEqual(where(found[d + "Certificates/First aid certificate.md"]),
                             ("proposed", "document", "certificate"))
            gp = found[d + "Medical/GP letter.md"]
            self.assertEqual(gp.state, "left_out")
            self.assertIn("private", gp.why)
            self.assertIn("picture", found[d + "Downloads/beach.jpg"].why)
            self.assertNotIn(d + "Downloads/model.bin", found)
            self.assertEqual(where(found[d + "Misc/thoughts.md"])[:2], ("left_out", ""))
            asked = backend.requests("sort_pick")
            self.assertEqual(len(asked), 1, "only the file the rules could not place goes to the AI")
            sent = " ".join(r.system + r.user for r in backend.log).lower()
            self.assertNotIn("diagnosed", sent, "a private file never reaches the AI")
            self.assertEqual(self.facts(con), before, "a proposal is never a fact")
            self.assertFalse((vault / "Sources").exists())

    def test_a_history_graduates_dump_is_proposed_section_by_section(self):
        with home() as (con, _vault, files), fake_ai({"sort_pick": json.dumps({"choice": "none"})}):
            degree = self.education(con, "BA", "History")
            museum = profile_store.add_found(con, "job", {"title": "Visitor guide",
                                                          "employer": "Fenwick Lantern Museum Trust"},
                                             {"source_rel": "Sources/CVs/CV.md", "doc_type": "cv"})
            before = self.facts(con)
            found = by_rel(sorter.propose(con, history_dump(files), local=True))
            d = "Marnie files/"
            self.assertEqual(where(found[d + "Marnie Quillfeather CV.md"]), ("proposed", "cv", "cv"))
            notes = found[d + "BA History/HIS2040 Archival Methods/Seminar notes week 1.md"]
            self.assertEqual((*where(notes), notes.target_id), ("proposed", "lecture", "notes", degree))
            essay = found[d + "BA History/HIS2040 Archival Methods/Essays/Parish registers essay.md"]
            self.assertEqual((*where(essay), essay.target_id), ("proposed", "coursework", "essay", degree))
            self.assertEqual(found[d + "BA History/HIS2040 Archival Methods/Essays/Parish registers essay (1).md"]
                             .state, "copy")
            self.assertEqual(where(found[d + "BA History/Transcript of study.md"])[:2], ("proposed", "official"))
            reference = found[d + "Volunteering/Reference from the museum.md"]
            self.assertEqual((*where(reference), reference.target_id), ("proposed", "job_doc", "reference", museum),
                             "a reference naming a job's employer goes on that job")
            exhibition = found[d + "Portfolio/Lantern exhibition"]
            self.assertEqual((*where(exhibition), exhibition.held), ("proposed", "project", "upload", "files"))
            self.assertEqual(found[d + "Health/Fit note.md"].state, "left_out")
            self.assertEqual(self.facts(con), before, "a proposal is never a fact")

    def test_nothing_in_a_library_or_build_folder_is_proposed_and_its_project_is_one_row(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            with_libraries(d, "site", "# Site\n\nA made-up site.\n", ("Leafline", "Chartwell"))
            put(d, "dist/notes.md", "# Notes\n\nMade-up build output.\n")
            put(d, "Code/lib/pyparse/README.md", "# pyparse\n\nA made-up library.\n")
            put(d, "Code/lib/pyparse/setup.py", "from setuptools import setup\n")
            put(d, "Code/lib/helpers.md", "# Helpers\n\nMy own notes on helpers.\n")
            found = sorter.propose(con, d, local=True)
            rels = by_rel(found)
            self.assertEqual(where(rels["Dump/site"]), ("proposed", "project", "upload"),
                             "a project is proposed whole, its libraries inside it")
            self.assertEqual(sorted(r for r in rels if "_build" in r or "dist" in r or "pyparse" in r), [])
            self.assertIn("Dump/Code/lib/helpers.md", rels, "a lib folder of the person's own files is looked at")
            self.assertEqual(found.skipped[sort_rules.VENDORED_SKIP], 3, "dist/notes.md and the package's files")

    def test_a_published_paper_is_left_out_as_reading_and_an_essay_citing_papers_is_not(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            settings.set("you.name", "Avery Quillon")
            course = files / "Uni/BSc Computing/Year 3/Dissertation"
            put(course, "papers/Probing vision models.pdf", paper("Probing made-up vision models",
                                                                  "arXiv:2301.01234v2"))
            put(course, "papers/Parish records.pdf", paper("Parish records and the poor",
                                                           "Journal of Made-up Local History, Vol. 12"))
            essay = "# Tide prediction essay\n\n" + WORDS + "\n\nAs Smith et al. and Jones et al. show.\n\n" \
                    "## References\n\nSmith, A. et al. (2019). Journal of Made-up Tides. doi:10.5555/jmt.1\n"
            put(course, "Tide prediction essay.md", essay)
            found = by_rel(sorter.propose(con, files / "Uni", local=True))
            d = "Uni/BSc Computing/Year 3/Dissertation/"
            for name in ("papers/Probing vision models.pdf", "papers/Parish records.pdf"):
                self.assertEqual((found[d + name].state, found[d + name].why),
                                 ("left_out", "A published paper. Hal keeps your own work, not your reading."))
            self.assertEqual(where(found[d + "Tide prediction essay.md"]), ("proposed", "coursework", "essay"))

    def test_a_reading_folder_makes_its_papers_reading_even_with_too_few_signs_for_a_paper(self):
        # Regression: real papers in "Dissertation/papers/" gave too few first-page signs, and the "Dissertation"
        # folder made them coursework (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            settings.set("you.name", "Avery Quillon")
            thesis = files / "Uni/BSc Computing/Year 3/Dissertation"
            bare = ["Introduction", "We study a made-up question at length. " * 30]
            put(thesis, "papers/Learning Transferable Harbour Models From Made-up Tide Supervision.pdf",
                tiny_pdf(["Learning transferable harbour models", *bare]))
            put(thesis, "papers/Tide3DVLM_An_Efficient_Vision-Language_Model_for_Made-up_Tides.pdf",
                tiny_pdf(["Tide3DVLM", *bare]))
            history = files / "Uni/BA History/HIS2040 Archival Methods"
            put(history, "Readings/Brindle Parish Relief.pdf", tiny_pdf(["Parish relief", *bare]))
            put(history, "Readings/Week 3 reading notes.md", "# Week 3 reading\n\nMy made-up notes on Brindle.\n")
            put(history, "Literature/The Poor Law and the Vestry in a Made-up Parish.md",
                "# The Poor Law and the vestry\n\n" + WORDS)
            put(thesis, "Dissertation report.pdf", tiny_pdf(["Tide prediction", "Introduction", *bare]))
            found = by_rel(sorter.propose(con, files / "Uni", local=True))
            d, h = "Uni/BSc Computing/Year 3/Dissertation/", "Uni/BA History/HIS2040 Archival Methods/"
            for rel in (d + "papers/Learning Transferable Harbour Models From Made-up Tide Supervision.pdf",
                        d + "papers/Tide3DVLM_An_Efficient_Vision-Language_Model_for_Made-up_Tides.pdf",
                        h + "Readings/Brindle Parish Relief.pdf",
                        h + "Literature/The Poor Law and the Vestry in a Made-up Parish.md"):
                self.assertEqual((found[rel].state, found[rel].why), ("left_out", sort_rules.PAPER_WORDS), rel)
            self.assertEqual(where(found[h + "Readings/Week 3 reading notes.md"]), ("proposed", "lecture", "notes"),
                             "the person's notes on a reading are what they studied")
            self.assertEqual(found[d + "Dissertation report.pdf"].section, "coursework",
                             "outside a reading folder a document needs a paper's signs")
            self.assertTrue(sort_rules.paper_title("Learning_Transferable_Visual_Models_From_Natural_Language"))
            self.assertFalse(sort_rules.paper_title("CSC3095 Final Report On Made-up Tide Prediction"))
            self.assertFalse(sort_rules.paper_title("Essay on the made-up parish registers of the vale"))

    def test_a_poster_named_after_someone_else_is_left_out_and_ones_own_is_not(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            module = files / "Uni/BA History/HIS2040 Archival Methods"
            put(module, "CSC3095_Jordan_Pellow_Project_Poster.pdf", tiny_pdf(["Harbour fog", "Jordan Pellow"]))
            put(module, "Vestry Poster.pdf", tiny_pdf(["Women in the parish vestry", "Talia Wren"]))
            put(module, "HIS2040_Marnie_Quillfeather_Poster.pdf", tiny_pdf(["Parish registers", "Marnie Quillfeather"]))
            found = by_rel(sorter.propose(con, files / "Uni", local=True))
            m = "Uni/BA History/HIS2040 Archival Methods/"
            self.assertEqual(found[m + "CSC3095_Jordan_Pellow_Project_Poster.pdf"].state, "proposed",
                             "with no name in the settings nothing is taken for someone else's")
            settings.set("you.name", "Marnie Quillfeather")
            found = by_rel(sorter.propose(con, files / "Uni", local=True))
            self.assertEqual(found[m + "CSC3095_Jordan_Pellow_Project_Poster.pdf"].why,
                             "Someone else's work: it is named after Jordan Pellow, not you.")
            self.assertEqual(found[m + "Vestry Poster.pdf"].state, "left_out", "an author line names its maker")
            self.assertNotIn("Someone else", found[m + "HIS2040_Marnie_Quillfeather_Poster.pdf"].why,
                             "the person's own poster is never someone else's")

    def test_what_a_course_gave_out_is_lecture_material_at_most(self):
        with home() as (con, _vault, files), fake_ai(NONE) as backend:
            course = files / "Uni/BSc Computing/Year 3/CSC3095 Project"
            put(course, "Linked materials/Project Posters and Demonstrations.pdf",
                tiny_pdf(["Project posters and demonstrations", "Guidance for every student."]))
            put(course, "Potential_Projects_for_2022-2023.pdf", tiny_pdf(["Potential projects", "1. A tide model."]))
            put(course, "Project List 2024.md", "# Project list 2024\n\n- A made-up fog model.\n")
            put(course, "Research Methods in Computing Projects.pdf", tiny_pdf(["Research methods", "Planning."]))
            put(course, "Essays/Essay brief.md", "# Essay brief\n\nWrite 2,000 words on a made-up question.\n")
            put(course, "Marking scheme.md", "# Marking scheme\n\nA made-up rubric.\n")
            found = sorter.propose(con, files / "Uni", local=True)
            self.assertEqual({where(p) for p in found.proposals}, {("proposed", "lecture", "notes")})
            self.assertEqual(backend.requests("sort_pick"), [], "a course document never needs the AI")

    def test_a_downloaded_materials_folder_is_never_a_project_and_its_files_are_lecture_material(self):
        # Regression: "<Module> - <CODE>/Linked materials" with a README and code was a code project (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            linked = "learning platform/2023/Databases - CS2041/Linked materials"
            put(d, f"{linked}/README.md", "# Practical 3\n\nRun the starter code, then answer the questions.\n")
            put(d, f"{linked}/package.json", '{"name": "practical-3"}\n')
            put(d, f"{linked}/starter.js", "console.log(1);\n")
            module = "Downloads/HIS2040 Archival Methods/Resources"
            put(d, f"{module}/README.md", "# Archival Methods\n\nThe module page, saved.\n")
            put(d, f"{module}/Seminar guide.md", "# Seminar guide\n\nHow each seminar runs, made up.\n")
            found = sorter.propose(con, d, local=True)
            rels = by_rel(found)
            self.assertEqual([p.rel for p in found.proposals if p.section == "project"], [])
            for name in ("README.md", "starter.js"):
                self.assertEqual(rels[f"Dump/{linked}/{name}"].section, "lecture", name)
            self.assertEqual(where(rels[f"Dump/{module}/Seminar guide.md"]), ("proposed", "lecture", "notes"))

    def test_a_notes_vault_is_never_a_project_and_its_notes_go_by_their_own_signals(self):
        # Regression: a whole Obsidian vault with a README at its root was one project (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            put(d, "obsidian/Master/.obsidian/app.json", "{}\n")
            put(d, "obsidian/Master/README.md", "# Master\n\nMy made-up notes.\n" + README)
            put(d, "obsidian/Master/Databases/README.md", "# Databases\n\nMade-up notes.\n")
            put(d, "obsidian/Master/Databases/Normal forms.md", "# Normal forms\n\nFirst normal form, made up.\n")
            put(d, "obsidian/Master/Avery Quillon CV.md", AVERY_CV)
            put(d, "obsidian/Master/tool/pyproject.toml", "[project]\nname = \"tool\"\n")
            put(d, "obsidian/Master/tool/main.py", "print(1)\n")
            put(d, "Exports/Notion/README.md", "# Workspace\n\nA made-up export.\n")
            for n, page in enumerate(("Archive visits", "Census returns")):
                put(d, f"Exports/Notion/{page} {n:032x}.md", f"# {page}\n\nMade-up notes on {page}.\n")
            put(d, "Logseq graph/logseq/config.edn", "{}\n")
            put(d, "Logseq graph/README.md", "# Graph\n\nMade-up.\n")
            put(d, "Logseq graph/pages/Tithe maps.md", "# Tithe maps\n\nMade-up notes.\n")
            found = sorter.propose(con, d, local=True)
            rels = by_rel(found)
            self.assertEqual([p.rel for p in found.proposals if p.section == "project"], ["Dump/obsidian/Master/tool"],
                             "only a code project inside a vault is a project")
            self.assertEqual(where(rels["Dump/obsidian/Master/Databases/Normal forms.md"]),
                             ("proposed", "lecture", "notes"))
            self.assertEqual(where(rels["Dump/obsidian/Master/Avery Quillon CV.md"]), ("proposed", "cv", "cv"))
            self.assertEqual(where(rels[f"Dump/Exports/Notion/Census returns {1:032x}.md"]),
                             ("proposed", "lecture", "notes"))
            self.assertEqual(where(rels["Dump/Logseq graph/pages/Tithe maps.md"]), ("proposed", "lecture", "notes"))

    def test_sibling_folders_of_notes_are_one_proposal_at_their_parent(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            notes_folders(files / "Dump", "Notes/known-cyber-attacks", ("2017 WannaCry", "2014 Heartbleed",
                                                                         "2020 SolarWinds"))
            notes_folders(files / "Dump", "Uni/BA History/Year 2/archive-visits", ("1851 census", "1871 census",
                                                                                     "Tithe map"))
            notes_folders(files / "Dump", "Portfolio", ("Lantern exhibition", "Walking tour"))
            found = by_rel(sorter.propose(con, files / "Dump", local=True))
            self.assertEqual(where(found["Dump/Notes/known-cyber-attacks"]), ("proposed", "project", "upload"))
            self.assertEqual(where(found["Dump/Uni/BA History/Year 2/archive-visits"]),
                             ("proposed", "coursework", "notes"), "under a course folder it is coursework")
            self.assertFalse([r for r in found if "WannaCry" in r or "census" in r], "no sub-folder is a row")
            self.assertEqual(where(found["Dump/Portfolio/Walking tour"]), ("proposed", "project", "upload"),
                             "two project folders stay two projects")

    def test_practice_exercises_downloads_and_a_notes_apps_folders_are_never_showcase_projects(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            for task in ("two-sum", "anagrams"):
                put(d, f"Code/hackerrank/{task}/README.md", f"# {task}\n\n{README}")
                put(d, f"Code/hackerrank/{task}/solution.py", "def solve():\n    return 1\n")
                put(d, f"Code/hackerrank/{task}/test_solution.py", "def test():\n    assert 1\n")
            put(d, "Code/Advent of Code 2023/day1.py", "print(1)\n")
            put(d, "Downloads/Harbour Crane - 4417852/README.txt", "Harbour Crane by a made-up maker.\n")
            put(d, "Downloads/Harbour Crane - 4417852/LICENSE.txt", "Made-up licence.\n")
            put(d, "Vault/Templates/README.md", "# Templates\n\nMade-up note templates.\n")
            put(d, "Vault/Templates/Daily note.md", "# {{date}}\n\n## Today\n")
            put(d, "Vault/+Inbox/README.md", "# Inbox\n\nThings to file.\n")
            put(d, "Vault/_Session-Logs/README.md", "# Logs\n\nMade-up session logs.\n")
            found = sorter.propose(con, d, local=True)
            rels = by_rel(found)
            practice = rels["Dump/Code/hackerrank"]
            self.assertEqual((*where(practice), practice.star), ("proposed", "project", "upload", False))
            self.assertEqual(practice.why, sort_units.WORDS[sort_units.PRACTICE])
            self.assertEqual(where(rels["Dump/Code/Advent of Code 2023"])[:2], ("proposed", "project"))
            self.assertEqual(rels["Dump/Downloads/Harbour Crane - 4417852"].state, "left_out")
            self.assertFalse([r for r in rels if "two-sum" in r or "Crane - 4417852/" in r])
            self.assertFalse([p.rel for p in found.proposals if p.section == "project" and "Vault" in p.rel],
                             "a notes app's own folders are never projects")
            self.assertEqual(rels["Dump/Vault/Templates/Daily note.md"].state, "left_out")

    def test_only_the_persons_own_documented_projects_outside_a_course_are_starred_at_most_eight(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            for n in range(sorter.STARS_MAX + 2):
                put(d, f"Projects/p{n}/README.md", README)
                put(d, f"Projects/p{n}/main.py", "print(1)\n")
                committed(d / f"Projects/p{n}", repos.AVERY, f"2024-0{n % 9 + 1}-01T10:00:00Z")
            for n in (1, 2):
                put(d, f"Uni/Year 1/practical-{n}/README.md", README)
                put(d, f"Uni/Year 1/practical-{n}/main.py", "print(1)\n")
                committed(d / f"Uni/Year 1/practical-{n}", repos.AVERY, "2023-01-01T10:00:00Z")
            put(d, "Projects/shared/README.md", README)
            put(d, "Projects/shared/main.py", "print(1)\n")
            committed(d / "Projects/shared", repos.ROBIN, "2024-01-01T10:00:00Z")
            put(d, "Projects/short/README.md", "# Short\n\nA made-up tool.\n")
            put(d, "Projects/short/main.py", "print(1)\n")
            committed(d / "Projects/short", repos.AVERY, "2024-01-01T10:00:00Z")
            put(d, "Projects/copied/README.md", README)
            put(d, "Projects/copied/pyproject.toml", "[project]\nname = \"copied\"\n")
            found = by_rel(sorter.propose(con, d, local=True))
            self.assertEqual([r for r, p in found.items() if p.star], [], "with no name Hal knows no commits")
            settings.set("you.name", "Avery Quillon")
            found = by_rel(sorter.propose(con, d, local=True))
            starred = sorted(r for r, p in found.items() if p.star)
            self.assertEqual(len(starred), sorter.STARS_MAX)
            self.assertTrue(all(r.startswith("Dump/Projects/p") for r in starred),
                            "never a practical, a classmate's project, a short README or one with no commits")

    def test_code_in_a_course_folder_is_coursework_and_numbered_folders_are_one_row(self):
        # Regression: ten "practical-N" folders and an "assignment-3" repo under a degree's year folder were
        # project rows, one each (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            settings.set("you.name", "Avery Quillon")
            degree = self.education(con, "BSc", "Computing")
            d = files / "Dump"
            practicals = "BSc Computing/year 1/Programming portfolio/part 1/2301234_practicals/part1"
            for n in range(1, 11):
                put(d, f"{practicals}/practical-{n}/README.md", f"# Practical {n}\n\n{README}")
                put(d, f"{practicals}/practical-{n}/main.py", f"print({n})\n")
            put(d, "BSc Computing/Year 2/CS2041 Databases/assignment-3/README.md", README)
            put(d, "BSc Computing/Year 2/CS2041 Databases/assignment-3/schema.py", "TABLES = 3\n")
            committed(d / "BSc Computing/Year 2/CS2041 Databases/assignment-3", repos.AVERY, "2023-03-01T10:00:00Z")
            put(d, "BSc Computing/Year 2/CSC2033 Team project/README.md", README)
            put(d, "BSc Computing/Year 2/CSC2033 Team project/app.js", "console.log(1);\n")
            for n in (1, 2, 3):
                put(d, f"Code/lab{n}/README.md", f"# Lab {n}\n\nA made-up exercise.\n")
                put(d, f"Code/lab{n}/lab.py", f"print({n})\n")
            from test_sections_sort import sorted_folder  # imported here: that module imports this one
            sort_id = sorted_folder(con, d)
            rows = {r["rel"]: r for r in sorts.rows(con, sort_id, limit=sorts.PAGE_MAX)["rows"]}
            course = [r for r in rows.values() if "BSc Computing" in r["rel"]]
            self.assertEqual(len(course), 12)
            self.assertEqual({(r["section"], r["doc_kind"], r["held"], r["target_id"], r["star"]) for r in course},
                             {("coursework", "other", "files", degree, False)}, "never a project, never starred")
            shown = sort_review.rows(con, sort_id, section="coursework", limit=sorts.PAGE_MAX)["rows"]
            self.assertIn(("part1", 10), [(r["name"], r.get("count")) for r in shown], "one row for the practicals")
            self.assertEqual(len(shown), 3, "the practicals, assignment-3 and the team project")
            labs = sort_review.rows(con, sort_id, section="project", limit=sorts.PAGE_MAX)["rows"]
            self.assertEqual([(r["name"], r["count"], r["rel"]) for r in labs], [("lab1 to lab3", 3, "Dump/Code")],
                             "numbered folders outside a course are one project row")

    def test_copies_of_a_project_in_other_trees_are_one_row_even_with_no_history(self):
        # Regression: "repos/portfolio-repos/Hal", "personal projects/Hal" and "repos/unversioned/Hal-12gb", most
        # with no .git, were three project rows (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            helper = {"README.md": "# Harbourhelp\n\nA made-up helper that drafts letters to a harbour club.\n",
                      "help/app.py": "def main():\n    return 'help'\n", "help/db.py": "TABLES = ['letters']\n"}
            for folder, when in (("repos/portfolio-repos/Harbourhelp", NEW), ("personal projects/Harbourhelp", OLD),
                                 ("repos/unversioned/Harbourhelp-12gb", NEW + DAY_S)):
                for name, text in helper.items():
                    dated(put(d, f"{folder}/{name}", text), when)
            repos.git(d / "repos/portfolio-repos/Harbourhelp", "init", "-q")
            repos.git(d / "repos/portfolio-repos/Harbourhelp", "remote", "add", "origin",
                      "https://example.org/avery/harbourhelp.git")
            about = "# Course fetcher\n\nSaves made-up course files from a learning platform, one module at a time."
            dated(put(d, "Code/course fetcher/README.md", about + "\n"), NEW)
            dated(put(d, "Code/course fetcher/fetch.py", "def fetch():\n    return 2\n"), NEW)
            dated(put(d, "old/course-fetcher-main/README.md", about + " It logs in first.\n"), OLD)
            dated(put(d, "old/course-fetcher-main/src/get.py", "def get():\n    return 1\n"), OLD)
            for folder, when in (("games/sprite_game", OLD), ("old/sprite_game", NEW),
                                 ("backup/Sprite-Game-final", NEW)):
                dated(put(d, f"{folder}/README.md", f"# Sprite game\n\nA made-up game, copy in {folder}.\n"), when)
                dated(put(d, f"{folder}/sprite.py", "SPEED = 3\n"), when)
            for folder in ("Projects/website", "Clients/website"):
                put(d, f"{folder}/README.md", f"# Website\n\nA made-up site for {folder.split('/')[0]}.\n")
                put(d, f"{folder}/index.md", f"# Home of {folder}\n")
            for n in (1, 2):
                put(d, f"Code/lab{n}/README.md", "# Lab\n\nA made-up exercise.\n")
                put(d, f"Code/lab{n}/lab.py", f"print({n})\n")
            found = by_rel(sorter.propose(con, d, local=True))
            kept = {r for r, p in found.items() if p.section == "project" and p.state == "proposed"}
            self.assertEqual(kept, {"Dump/repos/portfolio-repos/Harbourhelp", "Dump/Code/course fetcher",
                                    "Dump/games/sprite_game", "Dump/Projects/website", "Dump/Clients/website",
                                    "Dump/Code/lab1", "Dump/Code/lab2"},
                             "one of each project; a name alone, or a number, never makes a copy")
            for copy, of in (("repos/unversioned/Harbourhelp-12gb", "repos/portfolio-repos/Harbourhelp"),
                             ("personal projects/Harbourhelp", "repos/portfolio-repos/Harbourhelp"),
                             ("old/course-fetcher-main", "Code/course fetcher"),
                             ("old/sprite_game", "games/sprite_game"),
                             ("backup/Sprite-Game-final", "games/sprite_game")):
                self.assertEqual((found["Dump/" + copy].state, found["Dump/" + copy].copy_of), ("copy", "Dump/" + of))
            self.assertEqual(sort_copies.copy_name("Hal-v3-final (2)"), "hal")
            self.assertEqual(sort_copies.copy_name("lab2"), "lab2")

    def test_copies_that_differ_in_content_are_one_row_by_name_or_readme_heading(self):
        # Regression: versions of "Hal", "Lidar-Autonavigation-System", "sprite_game" and others, each with
        # other files, were one row each in a real dump, as their content did not overlap (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            n = iter(range(1000))

            def project(folder: str, title: str, when: float) -> None:
                dated(put(d, f"{folder}/README.md", f"# {title}\n\nA made-up project, this copy {next(n)}.\n"), when)
                dated(put(d, f"{folder}/main{next(n)}.py", f"print({next(n)})\n"), when)

            for folder, when in (("repos/portfolio-repos/Harbourhelp", NEW), ("personal projects/Harbourhelp", OLD),
                                 ("repos/unversioned/Harbourhelp-12gb", NEW + DAY_S)):
                project(folder, "Harbourhelp", when)
            for folder, when in (("Code/Tide-Autopilot-System", NEW), ("Uni stuff/Tide-Autopilot-System", OLD),
                                 ("backup/Tide-Autopilot-System", NEW + DAY_S), ("Code/AQ-TIDE-Autopilot-System", OLD)):
                project(folder, "Tide Autopilot System", when)
            project("Code/AQ-Harbourhelp", "AQ Harbour helper bot", NEW)       # a prefix alone is no copy
            for folder, when in (("games/sprite_game", NEW), ("Desktop/sprite-game", OLD), ("copies/Sprite Game", OLD)):
                project(folder, "Sprite game", when)
            for folder, when in (("Code/canvas downloader", NEW), ("Desktop/canvas-downloader", OLD),
                                 ("Code/tts-alerts", NEW), ("old/notebook_tts/tts-alerts", OLD)):
                project(folder, folder.rsplit("/", 1)[1], when)
            for name in ("lab1", "lab2"):
                project(f"Exercises/{name}", "Lab", NEW)
            for name in ("practical-1", "practical-2"):
                project(f"Exercises/{name}", "Practical", NEW)
            for folder in ("Projects/website", "Clients/website"):
                project(folder, "Website", NEW)
            found = by_rel(sorter.propose(con, d, local=True))
            kept = {r for r, p in found.items() if p.section == "project" and p.state == "proposed"}
            self.assertEqual(kept, {f"Dump/{r}" for r in (
                "repos/portfolio-repos/Harbourhelp", "Code/Tide-Autopilot-System", "Code/AQ-Harbourhelp",
                "games/sprite_game", "Code/canvas downloader", "Code/tts-alerts", "Exercises/lab1", "Exercises/lab2",
                "Exercises/practical-1", "Exercises/practical-2", "Projects/website",
                "Clients/website")}, "one row a project; never two numbered folders or two generic names")
            for copy, of in (("personal projects/Harbourhelp", "repos/portfolio-repos/Harbourhelp"),
                             ("repos/unversioned/Harbourhelp-12gb", "repos/portfolio-repos/Harbourhelp"),
                             ("backup/Tide-Autopilot-System", "Code/Tide-Autopilot-System"),
                             ("Code/AQ-TIDE-Autopilot-System", "Code/Tide-Autopilot-System"),
                             ("copies/Sprite Game", "games/sprite_game"), ("Desktop/sprite-game", "games/sprite_game"),
                             ("Desktop/canvas-downloader", "Code/canvas downloader"),
                             ("old/notebook_tts/tts-alerts", "Code/tts-alerts")):
                self.assertEqual((found["Dump/" + copy].state, found["Dump/" + copy].copy_of), ("copy", "Dump/" + of))

    def test_the_person_can_keep_an_older_copy_instead(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            for folder, when in (("Code/Harbourhelp", NEW), ("Desktop/Harbourhelp", OLD), ("old/Harbourhelp", OLD)):
                dated(put(d, f"{folder}/README.md", f"# Harbourhelp\n\nA made-up helper, in {folder}.\n"), when)
            sort_id = sorts.create(con, "folder", [str(d)], "Dump")
            sorts.run(con, sort_id)
            row = next(r for r in sort_review.rows(con, sort_id, section="project")["rows"]
                       if r["name"] == "Harbourhelp")
            self.assertEqual((row["rel"], [c["rel"] for c in row["copies"]]),
                             ("Dump/Code/Harbourhelp", ["Dump/Desktop/Harbourhelp", "Dump/old/Harbourhelp"]))
            kept = sorts.keep_copy(con, row["copies"][0]["id"])
            self.assertEqual((kept["rel"], kept["state"], [c["rel"] for c in kept["copies"]]),
                             ("Dump/Desktop/Harbourhelp", "proposed",
                              ["Dump/Code/Harbourhelp", "Dump/old/Harbourhelp"]))
            with self.assertRaises(ValueError):
                sorts.keep_copy(con, kept["id"])

    def test_a_document_naming_three_of_the_projects_is_a_listing_not_a_project(self):
        # Regression: "PORTFOLIO-PLAN.md" and "PROJECT-DOSSIER.md", about many projects, were project rows
        # (2026-11-09).
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            for name in ("tidewatch", "fog-bell", "Lantern exhibition"):
                put(d, f"Projects/{name}/README.md", f"# {name}\n\nA made-up piece of work called {name}.\n")
                put(d, f"Projects/{name}/make.py", f"NAME = {name!r}\n")
            put(d, "PORTFOLIO-PLAN.md", "# Portfolio plan\n\nLead with Tidewatch, then Fog bell; the Lantern "
                                        "exhibition (2023) goes last.\n")
            put(d, "Projects/Tidewatch portfolio piece.md", "# Tidewatch\n\nWhat I built, and the fog-bell idea "
                                                            "it grew from.\n")
            found = by_rel(sorter.propose(con, d, local=True))
            self.assertEqual((found["Dump/PORTFOLIO-PLAN.md"].state, found["Dump/PORTFOLIO-PLAN.md"].why),
                             ("left_out", "Describes several of your projects. Hal reads each project from its own "
                                          "files."))
            self.assertEqual(where(found["Dump/Projects/Tidewatch portfolio piece.md"]),
                             ("proposed", "project", "upload"), "naming two projects is no listing")

    def test_the_review_says_once_that_projects_with_no_history_got_no_showcase_guess(self):
        # A real dump's projects had no git history, so none was pre-starred and nothing said why (2026-11-09).
        from test_sections_sort import sorted_folder  # imported here: that module imports this one
        with home() as (con, _vault, files), fake_ai(NONE):
            settings.set("you.name", "Avery Quillon")
            put(files, "Git only/tidewatch/README.md", README)
            put(files, "Git only/tidewatch/gauge.py", "def read():\n    return 1\n")
            committed(files / "Git only/tidewatch", repos.AVERY, "2024-10-27T10:00:00Z")
            self.assertEqual(sort_review.notes(con, sorted_folder(con, files / "Git only")), [])
            put(files, "Mixed/tidewatch/README.md", README)
            put(files, "Mixed/tidewatch/gauge.py", "def read():\n    return 1\n")
            committed(files / "Mixed/tidewatch", repos.AVERY, "2024-10-27T10:00:00Z")
            put(files, "Mixed/Lantern exhibition/README.md", "# Lantern exhibition\n\nAn exhibition I curated.\n")
            sort_id = sorted_folder(con, files / "Mixed")
            self.assertEqual(sort_review.notes(con, sort_id), [{"id": "no_history", "words": "Hal could not see who "
                                                               "wrote these (no history). Mark your best work as "
                                                               "Showcase."}])
            settings.set("you.links.github", "https://github.com/avery-quillon-example")
            self.assertEqual([n["id"] for n in sort_review.notes(con, sort_id)], ["no_history", "github"])
            pipeline.add_link(con, "project", "avery-quillon-example", doc_kind="github_account")
            self.assertEqual([n["id"] for n in sort_review.notes(con, sort_id)], ["no_history"],
                             "once the account is on Projects, Hal stops suggesting it")

    def test_a_word_in_a_name_is_not_enough_for_a_transcript_or_a_contract(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            put(d, "Misc/smart contract.md", "# Smart contracts\n\nCode on a blockchain that holds parties to terms.\n")
            put(d, "Work/Employment contract.md", "# Contract of employment\n\nBetween the employer, Made-up Tides "
                                                  "Ltd, and the employee, Avery Quillon. Salary: 24,000 per annum. "
                                                  "Hours of work: 37.5 hours per week.\n")
            put(d, "Uni/Year 1/CSC1021 Programming/Coursework/UMLActivityAnswer.md",
                "# UML activity diagram answer\n\nModule CSC1021. Question 2 is worth 10 marks of the credits.\n\n"
                + WORDS)
            put(d, "Uni/Transcript of study.md", TRANSCRIPT)
            found = by_rel(sorter.propose(con, d, local=True))
            self.assertNotEqual(found["Dump/Misc/smart contract.md"].doc_kind, "contract")
            self.assertFalse(sort_kinds.contract_content("Code that holds parties to terms."))
            self.assertEqual(where(found["Dump/Work/Employment contract.md"]), ("proposed", "document", "contract"))
            self.assertEqual(where(found["Dump/Uni/Year 1/CSC1021 Programming/Coursework/UMLActivityAnswer.md"]),
                             ("proposed", "coursework", "notes"), "a question worth 10 marks is no transcript")
            self.assertEqual(where(found["Dump/Uni/Transcript of study.md"]), ("proposed", "official", "transcript"))

    def test_a_file_named_as_a_lecture_is_studied_unless_it_shows_a_submission(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            work = files / "Uni/Year 2/CS2041 Databases/Coursework"
            put(work, "Lecture 9 Project Planning.md", "# Lecture 9\n\nProject planning: milestones and risks.\n")
            put(work, "Week 3 report.md", "# Week 3 report\n\nStudent number: 2301234\n\n" + WORDS)
            found = by_rel(sorter.propose(con, files / "Uni", local=True))
            c = "Uni/Year 2/CS2041 Databases/Coursework/"
            self.assertEqual(where(found[c + "Lecture 9 Project Planning.md"]), ("proposed", "lecture", "notes"))
            self.assertEqual(where(found[c + "Week 3 report.md"]), ("proposed", "coursework", "report"))

    def test_notes_on_a_topic_are_studied_and_coursework_needs_a_submission_and_more_than_a_stub(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            d = files / "Dump"
            put(d, "Uni/Year 2/Topics/Agile project management.md", "# Agile\n\n" + WORDS)
            put(d, "Vault/Topics/Normalisation.md", "# Normalisation\n\nFirst normal form, made up.\n")
            put(d, "Uni/Year 2/Coursework/Short report.md", "# Short report\n\nA title and a line.\n")
            put(d, "Uni/Year 2/Coursework/Long report.md", "# Long report\n\n" + WORDS)
            found = by_rel(sorter.propose(con, d, local=True))
            self.assertEqual(where(found["Dump/Uni/Year 2/Topics/Agile project management.md"]),
                             ("proposed", "lecture", "notes"))
            self.assertEqual(where(found["Dump/Vault/Topics/Normalisation.md"]), ("proposed", "lecture", "notes"),
                             "a topic note outside a course folder is studied too")
            self.assertEqual(where(found["Dump/Uni/Year 2/Coursework/Short report.md"]),
                             ("proposed", "lecture", "notes"), "a stub is never coursework")
            self.assertEqual(where(found["Dump/Uni/Year 2/Coursework/Long report.md"]),
                             ("proposed", "coursework", "report"))

    def test_coursework_with_no_course_on_the_record_waits_for_one(self):
        with home() as (con, _vault, files), fake_ai({"sort_pick": json.dumps({"choice": "none"})}):
            found = by_rel(sorter.propose(con, history_dump(files), local=True))
            essay = found["Marnie files/BA History/HIS2040 Archival Methods/Essays/Parish registers essay.md"]
            self.assertEqual((essay.section, essay.target_id), ("coursework", None))
            self.assertTrue(sorter.needs_card(essay.section, essay.target_id))
            self.assertEqual(found["Marnie files/Volunteering/Reference from the museum.md"].section, "document",
                             "a reference for no job on the record is a document")

    def test_two_courses_are_told_apart_by_their_folders(self):
        with home() as (con, _vault, files), fake_ai():
            access = self.education(con, "Access to HE Diploma", "Humanities")
            degree = self.education(con, "BA", "History")
            for course in ("Access Humanities", "BA History"):
                for week in (1, 2, 3):
                    put(files, f"Study/{course}/Lectures/week {week} notes.md", f"# Week {week}\n\nNotes.\n")
            found = by_rel(sorter.propose(con, files / "Study", local=True))
            self.assertEqual(found["Study/Access Humanities/Lectures/week 1 notes.md"].target_id, access)
            self.assertEqual(found["Study/BA History/Lectures/week 1 notes.md"].target_id, degree)

    def test_the_ai_only_picks_among_the_sections_and_its_choice_is_labelled(self):
        with home() as (con, _vault, files), fake_ai({"sort_pick": json.dumps({"choice": "project/upload"})}) as b:
            put(files, "Writing/zine.md", "A made-up zine about harbour birds.\n")
            put(files, "Writing/odd.md", "Another made-up page.\n")
            found = by_rel(sorter.propose(con, files / "Writing", local=True))
            self.assertEqual((found["Writing/zine.md"].section, found["Writing/zine.md"].by), ("project", "ai"))
            prompt = b.log[0]
            self.assertIn("coursework/essay", prompt.user, "the AI is given the sections' kinds to pick from")
        with home() as (con, _vault, files), fake_ai({"sort_pick": json.dumps({"choice": "a job: Senior manager"})}):
            put(files, "Writing/zine.md", "A made-up zine about harbour birds.\n")
            found = by_rel(sorter.propose(con, files / "Writing", local=True))
            self.assertEqual(found["Writing/zine.md"].state, "left_out", "an answer off the list is no answer")

    def test_a_zips_code_project_is_proposed_as_its_files(self):
        with home() as (con, _vault, files), fake_ai():
            put(files, "unpacked/tidewatch/README.md", README)
            put(files, "unpacked/tidewatch/pyproject.toml", "[project]\nname = \"tidewatch\"\n")
            found = by_rel(sorter.propose(con, files / "unpacked", local=False, prefix=""))
            self.assertEqual((found["tidewatch"].doc_kind, found["tidewatch"].held), ("upload", "files"))

    def test_the_drop_folder_proposes_only_files_it_has_not_proposed_before(self):
        with home() as (con, _vault, files), fake_ai():
            first = put(files, "Inbox/Avery Quillon CV.md", AVERY_CV)
            put(files, "Inbox/First aid certificate.md", CERTIFICATE)
            found = by_rel(sorter.propose(con, files / "Inbox", local=True, known={str(first)}))
            self.assertEqual(sorted(found), ["Inbox/First aid certificate.md"])


if __name__ == "__main__":
    unittest.main()

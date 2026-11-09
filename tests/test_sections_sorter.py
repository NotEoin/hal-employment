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
from support.fake_ai import fake_ai
from test_ingest import home, put

from jobhunt.profile import store as profile_store
from jobhunt.sections import sort_rules, sorter

NONE = {"sort_pick": json.dumps({"choice": "none"})}
DAY_S = 24 * 3600
OLD, NEW = 1_700_000_000, 1_730_000_000      # November 2023 and October 2024
README = "# Tidewatch\n\nA tide-gauge dashboard for a harbour club. " + "It reads gauge data and draws charts. " * 20
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


def dated(path: Path, when: float) -> Path:
    """Sets a file's time, so which copy is newest is known."""
    os.utime(path, (when, when))
    return path


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
                                                             "made-up library, with an ER diagram.\n")
    dated(put(d, "Uni/BSc Computing/Year 3/Dissertation/Dissertation final.md", "# Tide prediction\n\nFinal.\n"), NEW)
    dated(put(d, "Uni/BSc Computing/Year 3/Dissertation/Dissertation.md", "# Tide prediction\n\nDraft.\n"), OLD)
    for name, when in (("tidewatch", NEW), ("tidewatch (2)", OLD)):
        put(d, f"Projects/{name}/README.md", README)
        put(d, f"Projects/{name}/pyproject.toml", "[project]\nname = \"tidewatch\"\n")
        put(d, f"Projects/{name}/src/gauge.py", "def read():\n    return 1\n")
        put(d, f"Projects/{name}/src/charts.py", "def draw():\n    return 2\n")
        for f in (d / "Projects" / name).rglob("*"):
            dated(f, when)
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
    essay = "# The parish registers of Harrowgate Vale\n\nAn essay on baptisms, 1750 to 1850.\n"
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

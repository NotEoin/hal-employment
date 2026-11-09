"""From a big folder to Your record, end to end, for a software graduate and a history graduate: every file's
proposal, nothing a fact before it is accepted, everything accepted read in its section, and a record the older
bulk read built moved to sections and undone.

The dumps are `test_sections_sorter`'s made-up Avery Quillon (software) and Marnie Quillfeather (history); each
mixes CVs, coursework in course folders, lecture notes, a code project or project folder, a certificate or a
reference, a medical letter that must be left out, and duplicate copies. The AI is the fake one.
"""
import json
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from support.fake_ai import fake_ai
from test_ingest import CV_TEXT, OWNER, REPLY, home, put, run
from test_record import tiny_pdf
from test_sections_sort import add_and_read, sections_of, sorted_folder
from test_sections_sorter import (
    README,
    WORDS,
    committed,
    history_dump,
    notes_folders,
    paper,
    software_dump,
    with_libraries,
)

from jobhunt.core import settings
from jobhunt.profile import store as profile_store
from jobhunt.sections import move, pipeline, sort_review, sorts

NONE = {"sort_pick": json.dumps({"choice": "none"})}
STATES = ("proposed", "left_out", "copy")

# Where each file of each dump is proposed: (state, section, kind of document), by its name.
AVERY = {"Avery Quillon CV.md": ("proposed", "cv", "cv"), "Avery CV 2022.md": ("proposed", "cv", "cv"),
         "Avery Quillon CV (1).md": ("copy", "cv", "cv"),
         "Cover letter Made-up Tides.md": ("proposed", "letter", "cover_letter"),
         "week 1 notes.md": ("proposed", "lecture", "notes"), "week 2 notes.md": ("proposed", "lecture", "notes"),
         "week 3 notes.md": ("proposed", "lecture", "notes"),
         "Database design report.md": ("proposed", "coursework", "report"),
         "Dissertation final.md": ("proposed", "coursework", "dissertation"),
         "Dissertation.md": ("copy", "coursework", "dissertation"),
         "tidewatch": ("proposed", "project", "repo"), "tidewatch (2)": ("copy", "project", "repo"),
         "First aid certificate.md": ("proposed", "document", "certificate"),
         "GP letter.md": ("left_out", "", ""), "beach.jpg": ("left_out", "", ""), "thoughts.md": ("left_out", "", ""),
         "harbour-site": ("proposed", "project", "upload"),
         "Project Posters and Demonstrations.pdf": ("proposed", "lecture", "notes"),
         "Potential_Projects_for_2022-2023.pdf": ("proposed", "lecture", "notes"),
         "Project List 2024.md": ("proposed", "lecture", "notes"),
         "Research Methods in Computing Projects.pdf": ("proposed", "lecture", "notes"),
         "CSC3095_Jordan_Pellow_Project_Poster.pdf": ("left_out", "", ""),
         "CLIP-Dissect probing.pdf": ("left_out", "", ""), "CheXpert Plus.pdf": ("left_out", "", ""),
         "known-cyber-attacks": ("proposed", "project", "upload"), "hackerrank": ("proposed", "project", "upload"),
         "Harbour Crane - 4417852": ("left_out", "", ""), "Daily note.md": ("left_out", "", ""),
         "README.md": ("left_out", "", ""), "practical-1": ("proposed", "project", "repo"),
         "practical-2": ("proposed", "project", "repo"), "fogbell": ("proposed", "project", "repo"),
         "smart contract.md": ("proposed", "lecture", "notes"),
         "UMLActivityAnswer.md": ("proposed", "coursework", "notes"),
         "Lecture 9 Project Planning.md": ("proposed", "lecture", "notes"),
         "Agile project management.md": ("proposed", "lecture", "notes")}
MARNIE = {"Marnie Quillfeather CV.md": ("proposed", "cv", "cv"),
          "Seminar notes week 1.md": ("proposed", "lecture", "notes"),
          "Seminar notes week 2.md": ("proposed", "lecture", "notes"),
          "Seminar notes week 3.md": ("proposed", "lecture", "notes"),
          "Parish registers essay.md": ("proposed", "coursework", "essay"),
          "Parish registers essay (1).md": ("copy", "coursework", "essay"),
          "Transcript of study.md": ("proposed", "official", "transcript"),
          "Reference from the museum.md": ("proposed", "document", "reference"),
          "Lantern exhibition": ("proposed", "project", "upload"), "Fit note.md": ("left_out", "", ""),
          "Digital edition": ("proposed", "project", "upload"),
          "Brindle 2019 Parish records and the poor.pdf": ("left_out", "", ""),
          "Fenwick 2021 Baptism registers.pdf": ("left_out", "", ""),
          "Module handbook 2023.md": ("proposed", "lecture", "notes"),
          "Dissertation guidance.md": ("proposed", "lecture", "notes"),
          "Essay brief.md": ("proposed", "lecture", "notes"), "HIS2040_Talia_Wren_Poster.pdf": ("left_out", "", ""),
          "archive-visits": ("proposed", "project", "upload"), "Lantern stand - 5512093": ("left_out", "", ""),
          "Seminar template.md": ("left_out", "", ""), "README.md": ("left_out", "", ""),
          "Walking tour": ("proposed", "project", "upload"), "marriage contract.md": ("proposed", "lecture", "notes"),
          "Palaeography answer sheet.md": ("proposed", "lecture", "notes"),
          "Lecture 4 Writing an essay.md": ("proposed", "lecture", "notes"),
          "Writing history essays.md": ("proposed", "lecture", "notes")}
# The showcase guesses: only a project of the person's own, with their commits and a README that says what it is.
STARRED = {"software": ["tidewatch"], "history": []}


def download(d: Path, folder: str) -> None:
    """A made-up 3D-print download, named as a sharing site names one."""
    put(d, f"{folder}/README.txt", "A made-up design by a made-up maker, to print at home.\n")
    put(d, f"{folder}/LICENSE.txt", "Made-up licence.\n")


def messy_software_dump(root: Path) -> Path:
    """Avery Quillon's folder of everything, with each kind of file a real dump misplaced."""
    d = software_dump(root)
    with_libraries(d, "Projects/harbour-site", "# Harbour site\n\nA made-up website I built for a sailing club.\n",
                   ("Leafline", "Chartwell", "Tidekit"))
    put(d, "Projects/harbour-site/index.html", "<h1>Harbour club</h1>\n")
    put(d, "Old laptop/dist/release notes.md", "# Release notes\n\nMade-up build output.\n")
    put(d, "Code/lib/pyparse/README.md", "# pyparse\n\nA made-up parsing library.\n")
    put(d, "Code/lib/pyparse/setup.py", "from setuptools import setup\n")
    project = "Uni/BSc Computing/Year 3/CSC3095 Project"
    put(d, f"{project}/Linked materials/Project Posters and Demonstrations.pdf",
        tiny_pdf(["Project posters and demonstrations", "Guidance for every student on the poster session."]))
    put(d, f"{project}/Potential_Projects_for_2022-2023.pdf", tiny_pdf(["Potential projects", "1. A tide model."]))
    put(d, f"{project}/Project List 2024.md", "# Project list 2024\n\n- A made-up fog model.\n")
    put(d, f"{project}/Research Methods in Computing Projects.pdf", tiny_pdf(["Research methods", "Planning."]))
    put(d, f"{project}/CSC3095_Jordan_Pellow_Project_Poster.pdf", tiny_pdf(["Harbour fog", "Jordan Pellow"]))
    papers = "Uni/BSc Computing/Year 3/Dissertation/papers"
    put(d, f"{papers}/CLIP-Dissect probing.pdf", paper("Probing made-up vision models", "arXiv:2301.01234v2"))
    put(d, f"{papers}/CheXpert Plus.pdf", paper("A made-up chest X-ray dataset", "Journal of Made-up Imaging, Vol. 4"))
    notes_folders(d, "Notes/known-cyber-attacks", ("2017 WannaCry", "2014 Heartbleed", "2020 SolarWinds",
                                                   "2021 Log4Shell"))
    for task in ("two-sum", "anagrams"):
        put(d, f"Code/hackerrank/{task}/README.md", f"# {task}\n\nA made-up practice problem.\n")
        put(d, f"Code/hackerrank/{task}/solution.py", "def solve():\n    return 1\n")
        put(d, f"Code/hackerrank/{task}/test_solution.py", "def test():\n    assert 1\n")
    download(d, "Downloads/Harbour Crane - 4417852")
    put(d, "Vault/Templates/README.md", "# Templates\n\nMade-up note templates.\n")
    put(d, "Vault/Templates/Daily note.md", "# {{date}}\n\n## Today\n")
    for n in (1, 2):
        practical = f"Uni/BSc Computing/Year 1/CSC1021 Programming/practical-{n}"
        put(d, f"{practical}/README.md", README)
        put(d, f"{practical}/main.py", "print(1)\n")
        committed(d / practical, repos.AVERY, "2022-11-01T10:00:00Z")
    put(d, "Projects/fogbell/README.md", README)
    put(d, "Projects/fogbell/bell.py", "print(1)\n")
    put(d, "Projects/fogbell/horn.py", "print(2)\n")
    put(d, "Notes/smart contract.md", "# Smart contracts\n\nCode on a blockchain that holds parties to terms.\n")
    put(d, "Uni/BSc Computing/Year 1/CSC1021 Programming/Coursework/UMLActivityAnswer.md",
        "# UML activity diagram answer\n\nModule CSC1021. Question 2 is worth 10 marks of the credits.\n\n" + WORDS)
    put(d, "Uni/BSc Computing/Year 2/CS2041 Databases/Coursework/Lecture 9 Project Planning.md",
        "# Lecture 9\n\nProject planning: milestones and risks.\n")
    put(d, "Uni/BSc Computing/Year 2/Topics/Agile project management.md", "# Agile\n\n" + WORDS)
    return d


def messy_history_dump(root: Path) -> Path:
    """Marnie Quillfeather's folder of everything, with each kind of file a real dump misplaced."""
    d = history_dump(root)
    with_libraries(d, "Digital edition", "# Digital edition\n\nA made-up online edition of parish letters I "
                   "transcribed.\n", ("Pagefind", "Lunrish"))
    put(d, "Digital edition/letters.md", "# Letters\n\nTwelve made-up letters from 1820.\n")
    module = "BA History/HIS2040 Archival Methods"
    put(d, f"{module}/Readings/Brindle 2019 Parish records and the poor.pdf",
        paper("Parish records and the poor", "Journal of Made-up Local History, Vol. 12, No. 3"))
    put(d, f"{module}/Readings/Fenwick 2021 Baptism registers.pdf",
        paper("Baptism registers revisited", "doi:10.5555/jmsh.2021.9"))
    put(d, "BA History/Course materials/Module handbook 2023.md", "# Module handbook\n\nHow the modules run.\n")
    put(d, "BA History/HIS3001 Dissertation/Dissertation guidance.md", "# Dissertation guidance\n\nMade-up advice.\n")
    put(d, f"{module}/Essays/Essay brief.md", "# Essay brief\n\nWrite 2,000 words on a made-up question.\n")
    put(d, f"{module}/Seminar posters/HIS2040_Talia_Wren_Poster.pdf",
        tiny_pdf(["Women in the parish vestry", "Talia Wren", "HIS2040 poster"]))
    notes_folders(d, "Research/archive-visits", ("1851 census trip", "1871 census trip", "Tithe map visit"))
    download(d, "Downloads/Lantern stand - 5512093")
    put(d, "Notes vault/Templates/README.md", "# Templates\n\nMade-up templates.\n")
    put(d, "Notes vault/Templates/Seminar template.md", "# Seminar\n\n## Reading\n")
    put(d, "Portfolio/Walking tour/README.md", "# Walking tour\n\n" + README)
    put(d, "Portfolio/Walking tour/route.md", "# Route\n\nTwelve made-up stops.\n")
    put(d, "Notes vault/marriage contract.md", "# Marriage contracts\n\nIn the made-up 1700s a marriage contract "
                                               "set out what each party brought.\n")
    put(d, f"{module}/Palaeography answer sheet.md", "# Palaeography\n\nThis module has 20 credits; answer for 10 "
                                                      "marks.\n")
    put(d, f"{module}/Essays/Lecture 4 Writing an essay.md", "# Lecture 4\n\nHow to write a made-up essay.\n")
    put(d, f"{module}/Topics/Writing history essays.md", "# Writing history essays\n\n" + WORDS)
    return d


def proposals(con, sort_id: int) -> dict[str, tuple]:
    """Every row of a sort as (state, section, kind), by its file's name."""
    return {r["name"]: (r["state"], r["section"], r["doc_kind"]) for state in STATES
            for r in sorts.rows(con, sort_id, state=state, limit=sorts.PAGE_MAX)["rows"]}


def facts(con) -> tuple[int, int]:
    """Cards and evidence in the profile."""
    return tuple(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("profile_items", "profile_evidence"))


class FromADumpToTheRecord(unittest.TestCase):
    def education(self, con, qualification: str, subject: str) -> int:
        """A degree on a made-up CV, as an accepted CV would give it; its card's id."""
        return profile_store.add_found(con, "education", {"qualification": qualification, "subject": subject,
                                                          "institution": "University of Nowhere Fen"},
                                       {"source_rel": "Sources/CVs/CV.md", "doc_type": "cv"})

    def check(self, dump, expected: dict, degree: tuple[str, str], owner: str) -> None:
        with home() as (con, _vault, files), fake_ai(NONE) as backend:
            settings.set("you.name", owner)
            self.education(con, *degree)
            before = facts(con)
            sort_id = sorted_folder(con, dump(files))
            self.assertEqual(proposals(con, sort_id), expected)
            self.assertEqual(sorted(r["name"] for r in sorts.rows(con, sort_id, limit=sorts.PAGE_MAX)["rows"]
                                    if r["star"]), STARRED["software" if "Avery" in owner else "history"])
            self.assertEqual((facts(con), sections_of(con)), (before, []), "a proposal is never a fact")
            self.assertEqual(sorts.accept(con, sort_id)["need_card"], 0)
            add_and_read(con, sort_id)
            wanted = sorted((s, k) for state, s, k in expected.values() if state == "proposed")
            self.assertEqual(sections_of(con), wanted, "each accepted file is a source of its section")
            self.assertGreater(facts(con)[1], before[1], "read in its section, an accepted file gives facts")
            sent = " ".join(r.system + r.user for r in backend.log).lower()
            self.assertNotIn("diagnosed", sent, "the medical letter never reaches the AI")

    def test_a_software_graduates_dump_goes_where_proposed_and_only_once_accepted(self):
        self.check(messy_software_dump, AVERY, ("BSc", "Computing"), "Avery Quillon")

    def test_a_history_graduates_dump_goes_where_proposed_and_only_once_accepted(self):
        self.check(messy_history_dump, MARNIE, ("BA", "History"), "Marnie Quillfeather")

    def test_with_no_course_on_the_record_the_courses_its_folders_name_come_first(self):
        for dump, owner, title in ((messy_software_dump, "Avery Quillon", "BSc Computing"),
                                   (messy_history_dump, "Marnie Quillfeather", "BA History")):
            with self.subTest(owner=owner), home() as (con, _vault, files), fake_ai(NONE):
                settings.set("you.name", owner)
                sort_id = sorted_folder(con, dump(files))
                found = sort_review.courses_found(con, sort_id)
                self.assertEqual([c["title"] for c in found], [title])
                sort_review.accept_course(con, sort_id, found[0]["key"])
                self.assertEqual(sorts.accept(con, sort_id)["need_card"], 0, "every waiting file found its course")

    def test_a_record_the_bulk_read_built_is_moved_and_one_undo_puts_it_back(self):
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(REPLY), **NONE}):
            settings.set("you.name", OWNER)
            dump = history_dump(files)
            put(dump, "CV.md", CV_TEXT)
            run(con, [dump])
            self.assertTrue(move.can_move(con), "the bulk read filed facts naming its notes")
            cards = {r[0]: r[1] for r in con.execute("SELECT id, state FROM profile_items")}
            before = con.execute("SELECT count(*) FROM profile_evidence WHERE source_id IS NULL").fetchone()[0]
            notes = sorted(p.relative_to(vault).as_posix() for p in (vault / "Sources").rglob("*.md"))
            sort_id = sorts.create(con, "move", [], "older record")
            sorts.run(con, sort_id)
            moved = proposals(con, sort_id)
            self.assertNotIn("Fit note.md", moved, "a file the bulk read kept out was never a note to move")
            self.assertEqual(sections_of(con), [])
            sorts.accept(con, sort_id)
            states = move.before_accept(con, sort_id)
            for source_id in sorts.add_accepted(con, sort_id):
                pipeline.read(con, source_id)
            move.after_accept(con, sort_id, states)
            move.finish(con, sort_id)
            self.assertTrue(sections_of(con))
            self.assertEqual(con.execute("SELECT count(*) FROM profile_evidence WHERE source_id IS NULL").fetchone()[0],
                             0, "every older note's evidence is set aside")
            move.undo(con, sort_id)
            self.assertEqual(sections_of(con), [])
            self.assertEqual(con.execute("SELECT count(*) FROM profile_evidence WHERE source_id IS NULL").fetchone()[0],
                             before)
            self.assertEqual({i: s for i, s in con.execute("SELECT id, state FROM profile_items") if i in cards},
                             cards, "every card is back in its state")
            self.assertEqual(sorted(p.relative_to(vault).as_posix() for p in (vault / "Sources").rglob("*.md")),
                             notes, "the older notes are as they were, and no section note is left")


if __name__ == "__main__":
    unittest.main()

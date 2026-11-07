"""The bulk ingest: folders and zips read into the record as notes, safely, in order, and resumably.

Every test builds made-up files in its own temporary home; the people, employers and documents are
invented. The AI is the fake one, answering the `ingest_doc` prompt with canned JSON, or none at all.
"""
import contextlib
import functools
import http.client
import io
import json
import struct
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.actions import finished
from support.api import HalServer
from support.fake_ai import fake_ai

from jobhunt import db, record
from jobhunt.core import paths, runner, settings
from jobhunt.ingest import actions as ingest_actions
from jobhunt.ingest import (
    attach,
    discover,
    engine,
    frontmatter,
    items,
    notes,
    rank,
    roles,
    sources,
    understand,
    vaultnotes,
)

OWNER = "Avery Quillon"
CV_TEXT = f"""# {OWNER}

Curriculum vitae

## Experience

Gallery assistant, Brindle Arts Centre, Norwich, June 2023 to August 2024. Led weekend tours for 40 visitors.

## Education

BA History of Art, University of Elsewhere, 2020 to 2023, First class.
"""
REPLY = {"doc_type": "cv", "category": "CVs and letters", "title": "Avery Quillon CV", "period": "2020..2024",
         "summary": "A CV listing gallery work and a history of art degree.",
         "tags": ["cv", "history-of-art", "gallery", "tours", "visitor-experience", "norwich", "arts",
                  "public-speaking", "education", "first-class", "2023", "heritage"],
         "entities": {"organisations": ["Brindle Arts Centre", "University of Elsewhere"], "places": ["Norwich"],
                      "people": [], "projects": [], "courses": ["BA History of Art"], "skills": ["Public speaking"]},
         "about_owner": True,
         "facts": [{"kind": "job", "fields": {"title": "Gallery assistant", "employer": "Brindle Arts Centre"},
                    "field": "title", "quote": "Gallery assistant, Brindle Arts Centre"},
                   {"kind": "achievement", "fields": {"title": "Won the Wexley Prize"}, "field": "title",
                    "quote": "Won the Wexley Prize in 2022"}]}


NOTE_REPLY = {**REPLY, "doc_type": "notes", "category": "Study notes", "title": "Rivers and meanders",
              "summary": "Notes on how rivers meander and leave oxbow lakes.", "facts": [],
              "entities": {k: [] for k in understand.ENTITY_KINDS}}


@contextlib.contextmanager
def home():
    """A temporary home with a migrated database and an empty record; yields (connection, record, files)."""
    with tempfile.TemporaryDirectory(prefix="hal-ingest-") as tmp, paths.set_override(Path(tmp)):
        paths.ensure()
        vault, files = Path(tmp) / "Record", Path(tmp) / "Files"
        vault.mkdir()
        files.mkdir()
        con = db.connect()
        try:
            with mock.patch.object(record, "VAULT", vault, create=True), \
                    mock.patch.object(record, "INDEX", Path(tmp) / "index.json", create=True):
                yield con, vault, files
        finally:
            runner.idle(10)
            con.close()
            record._CACHE.clear()


def put(folder: Path, rel: str, data: str | bytes) -> Path:
    """Writes a made-up file at `rel` under `folder`."""
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def run(con, folders=(), **kw) -> dict:
    """One ingest of `folders`, in this thread."""
    return engine.run(con, kw.pop("batch", "b1"), folders=[str(f) for f in folders], step=lambda text: None,
                      progress=lambda done, total, text: None, **kw)


def rows(con, batch="b1") -> dict:
    """The batch's items by their path inside the folder given."""
    return {r["rel"]: r for r in con.execute("SELECT * FROM ingest_items WHERE batch = ?", (batch,))}


def zip_bytes(members: dict[str, bytes]) -> bytes:
    """A zip holding `members`, names as given (even unsafe ones)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class Sources(unittest.TestCase):
    def test_a_zip_slip_member_is_refused_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "uni.zip"
            bad.write_bytes(zip_bytes({"../escaped.txt": b"x", "/abs.txt": b"y", "C:/win.txt": b"z",
                                       "Year 1/essay.txt": b"An essay on the Brindle collection."}))
            out = sources.unpack(bad, Path(tmp) / "out")
            self.assertEqual(out.written, 1)
            self.assertEqual(len(out.refused), 3)
            self.assertFalse((Path(tmp) / "escaped.txt").exists())
            self.assertTrue((Path(tmp) / "out" / "Year 1" / "essay.txt").exists())
            self.assertTrue(any("points outside the zip" in p for p in out.problems()))

    def test_a_symlink_in_a_zip_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                info = zipfile.ZipInfo("link")
                info.external_attr = (0o120777 << 16)
                z.writestr(info, "/etc/passwd")
            (Path(tmp) / "l.zip").write_bytes(buf.getvalue())
            out = sources.unpack(Path(tmp) / "l.zip", Path(tmp) / "out")
            self.assertEqual((out.written, out.links), (0, ["l.zip/link"]))

    def test_a_member_past_its_cap_is_dropped_even_when_its_stated_size_lies(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "big.zip").write_bytes(zip_bytes({"big.txt": b"a" * 5000}))
            with mock.patch.object(sources, "ZIP_FILE_MAX", 1000):
                out = sources.unpack(Path(tmp) / "big.zip", Path(tmp) / "out")
            self.assertEqual(out.too_big, ["big.zip/big.txt"])
            self.assertFalse((Path(tmp) / "out" / "big.txt").exists())

    def test_nested_zips_are_unpacked_down_to_the_depth_cap(self):
        inner = zip_bytes({"deepest.txt": b"three down"})
        for _ in range(sources.ZIP_DEPTH_MAX - 1):
            inner = zip_bytes({"inner.zip": inner})
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.zip").write_bytes(zip_bytes({"top.txt": b"top", "inner.zip": inner}))
            out = sources.unpack(Path(tmp) / "a.zip", Path(tmp) / "out")
            found = sorted(p.relative_to(Path(tmp) / "out").as_posix() for p in (Path(tmp) / "out").rglob("*.txt"))
            self.assertIn("top.txt", found)
            self.assertEqual(len(out.too_deep), 1, "the zip at depth four is named, not unpacked")
            self.assertFalse(list((Path(tmp) / "out").rglob("*.zip")))


class Discovery(unittest.TestCase):
    def test_an_all_zero_file_is_reported_not_downloaded_and_never_read(self):
        with home() as (con, vault, files), fake_ai({}) as backend:
            put(files, "Cloud/CV.pdf", bytes(4096))
            run(con, [files])
            self.assertEqual(rows(con)["Files/Cloud/CV.pdf"]["state"], "not_downloaded")
            self.assertEqual(backend.requests(), [])
            self.assertFalse(list(vault.rglob("*.md")), "no note is written for a file not downloaded")

    def test_duplicates_are_linked_once_to_the_first_copy(self):
        with home() as (con, vault, files), fake_ai({}):
            put(files, "CV.md", CV_TEXT)
            put(files, "Backup/old.md", CV_TEXT)
            put(files, "Backup again/old.md", CV_TEXT)
            run(con, [files])
            found = rows(con)
            states = sorted(r["state"] for r in found.values())
            self.assertEqual(states, ["done", "duplicate", "duplicate"])
            first = next(r for r in found.values() if r["state"] == "done")
            note = (vault / first["note"]).read_text(encoding="utf-8")
            also = note.split("## Also at", 1)[1].split("## Original", 1)[0]
            self.assertEqual(note.count("## Also at"), 1)
            self.assertEqual(also.count("\n- `"), 2, "each other copy is named once")
            run(con, [files], batch="b2")
            self.assertEqual({r["state"] for r in rows(con, "b2").values()}, {"duplicate"},
                             "a second ingest of the same files reads none of them again")
            self.assertEqual(note, (vault / first["note"]).read_text(encoding="utf-8"))

    def test_junk_folders_and_binaries_are_left_out(self):
        with home() as (con, _vault, files), fake_ai({}):
            put(files, "Thesis/node_modules/x/readme.md", "# Not mine")
            put(files, "Thesis/model.safetensors", b"\x01\x02")
            put(files, "Thesis/thesis.md", "# My thesis\n\nOn rivers.")
            out = run(con, [files])
            self.assertEqual(list(rows(con)), ["Files/Thesis/thesis.md"])
            self.assertEqual(out["left_out"], {"junk folder": 1, "program or data file": 1})

    def test_settings_tool_files_and_bundled_code_are_left_out(self):
        with home() as (con, _vault, files), fake_ai({}):
            put(files, "Vault/.claude/settings.local.json", "{}")
            put(files, "Vault/.rag/chunks.jsonl", "{}")
            put(files, "Vault/.mcp.json", "{}")
            put(files, "Vault/CLAUDE.md", "# How to work in this vault")
            put(files, "Vault/CLAUDE-Archive.md", "# Older instructions")
            put(files, "Site/jquery-1.10.2.min.js", "!function(){}")
            put(files, "Site/react-entry-be1bde4765d8e661.js", "!function(){}")
            put(files, "Site/notes-20241105.js", "// my own script")
            put(files, "Vault/Rivers.md", "# Rivers\n\nMeanders and oxbow lakes.")
            out = run(con, [files])
            self.assertEqual(sorted(rows(con)), ["Files/Site/notes-20241105.js", "Files/Vault/Rivers.md"])
            self.assertEqual(out["left_out"], {"junk folder": 2, "settings or tool file": 5})

    def test_a_repository_becomes_one_unit_summarised_from_its_readme(self):
        with home() as (con, vault, files), fake_ai({}):
            put(files, "tidewatch/.git/HEAD", "ref: refs/heads/main")
            put(files, "tidewatch/README.md", "# Tidewatch\n\nPredicts tides for a made-up harbour.")
            put(files, "tidewatch/pyproject.toml", '[project]\nname = "tidewatch"\ndescription = "Tide tables"\n')
            for n in range(5):
                put(files, f"tidewatch/src/mod{n}.py", f"def f{n}():\n    return {n}\n")
            run(con, [files])
            found = rows(con)
            self.assertEqual([(r["kind"], r["state"]) for r in found.values()], [("repo", "done")])
            note = (vault / found["Files/tidewatch"]["note"]).read_text(encoding="utf-8")
            self.assertTrue(found["Files/tidewatch"]["note"].startswith("Sources/Code/"))
            self.assertIn("Predicts tides", note)
            self.assertIn("Python (5 files)", note)

    def test_a_folder_of_one_courses_notes_is_a_course_unit(self):
        with home() as (con, vault, files), fake_ai({}):
            for n in range(1, 4):
                put(files, f"HIS201/week {n} notes.md", f"# Week {n}\n\nSeminar on the medieval wool trade.")
            run(con, [files])
            found = rows(con)
            course = found["Files/HIS201"]
            self.assertEqual((course["kind"], course["state"]), ("course", "done"))
            text = (vault / course["note"]).read_text(encoding="utf-8")
            self.assertEqual(text.count("- [[Sources/Study notes/Week"), 3)
            member = (vault / found["Files/HIS201/week 1 notes.md"]["note"]).read_text(encoding="utf-8")
            self.assertIn("## Part of", member)


class Ranking(unittest.TestCase):
    def test_ranking_puts_a_cv_before_notes(self):
        self.assertLess(rank.rule_for("Docs/My CV 2024.pdf").rank, rank.rule_for("Docs/lecture notes.pdf").rank)
        self.assertEqual(rank.rule_for("Docs/Transcript.pdf").category, "Education")
        self.assertEqual(rank.rule_for("Year 2/Lecture notes/CV.docx").doc_type, "cv")

    def test_the_cv_is_read_first_whatever_its_folder_order(self):
        with home() as (con, _vault, files), fake_ai({}):
            put(files, "a notes/week 1 notes.md", "# Week 1\n\nNotes.")
            put(files, "z/Robin CV.md", CV_TEXT)
            order = []
            engine.run(con, "b1", folders=[str(files)], step=lambda t: None,
                       progress=lambda done, total, text: order.append(text))
            self.assertEqual(order[0], "Files/z/Robin CV.md")

    def test_messy_folders_do_not_make_rich_documents_of_everything(self):
        # a real 37,000-file dump sent 7,800 files to the model: project trees, LICENSE files, a
        # "resume_checkpoint.json" and transcriptions of posters all ranked as rich documents (2026-11-05)
        def kind(rel, text=None):
            with tempfile.TemporaryDirectory() as tmp:
                path = put(Path(tmp), Path(rel).name, text) if text is not None else None
                return rank.rule_for(rel, path).doc_type
        self.assertEqual(kind("Personal projects/archive/notes/week 3.md"), "notes")
        self.assertEqual(kind("Personal projects/archive/Vault/Sources/Poster.md"), "notes")
        self.assertEqual(kind("Projects/tool/LICENSE.md"), "other")
        self.assertEqual(kind("runs/resume_checkpoint.json"), "other")
        self.assertEqual(kind("commands/resume.md", "Carry on from where the last session stopped."), "notes")
        self.assertEqual(kind("Archive/Transcripts/Oral history interview (transcript).md", "Q: When did you"),
                         "notes")
        self.assertEqual(kind("uni/Transcript_10293 (2nd year).pdf"), "transcript")
        self.assertEqual(kind("Documents/Academic transcript.docx"), "transcript")
        self.assertEqual(kind("Jobs/Bar CV.md", "Avery Quillon\n\nProfile\n\nKind.\n\nExperience\n\nBar"), "cv")
        long_start = "# Avery Quillon\n\n" + "A long profile. " * 120 + "\n\n## Education\n\nBA History\n\n" \
            + "- A module. " * 80 + "\n\n## Experience\n\nArchive assistant"
        self.assertEqual(kind("CVs/Avery Quillon CV 2026.md", long_start), "cv")
        self.assertEqual(kind("Third year/Dissertation.pdf"), "report")
        self.assertEqual(kind("Certificates/First aid.pdf"), "certificate")
        self.assertEqual(kind("Topics/TLS certificate.md", "A TLS certificate binds a key to a name."), "notes")
        self.assertEqual(kind("Posters/say it hear it.pdf"), "other")
        self.assertEqual(kind("Sources/podcast transcript.pdf"), "other")
        self.assertEqual(kind("Higher Education Achievement Report (HEAR).pdf"), "transcript")
        self.assertEqual(kind("Major Project and Dissertation/Lecture 05 Research Methods.md"), "notes")
        self.assertEqual(kind("Major Project and Dissertation/Marcus Joiner.md", "Met Marcus about the brief."),
                         "notes")

    def test_a_light_pdf_is_read_from_its_first_pages_without_converting_it_all(self):
        # book-length PDFs took over ten seconds each through a full conversion (2026-11-05)
        from jobhunt.ingest import convert

        pdf = Path(__file__).parent / "fixtures" / "cv" / "Marnie-Quillfeather-CV.pdf"
        with mock.patch.object(record, "text_of", side_effect=AssertionError("a full conversion")):
            text = convert.text_of(pdf, rank.rule_for("Reading/Some book.pdf"))
        self.assertIn("Quillfeather", text)

    def test_text_with_lone_surrogates_from_a_pdf_can_still_be_written(self):
        from jobhunt.ingest import convert

        with mock.patch.object(convert.pdfread, "first_pages_text", return_value="Poster \ud835 text"):
            text = convert.text_of(Path("Reading/poster.pdf"), rank.rule_for("Reading/poster.pdf"))
        self.assertEqual(text, "Poster ? text")
        text.encode("utf-8")

    def test_notes_are_tagged_and_linked_by_their_subject_not_by_the_dump(self):
        # a 37,000-file dump gave every note its outer folders as tags ("dump", "onedrive"), copied a markdown
        # note's own frontmatter into its text, linked file-named titles such as "tags", and took a lecture
        # called "Reference Types" for a referee's letter (2026-11-05)
        from jobhunt.ingest import convert

        tags = understand.light_tags("Downloads/OneDrive/Areas/Year 2/History of Art/week 3.md", "Paintings.",
                                     rank.RULES[9])
        self.assertIn("history-of-art", tags)
        self.assertNotIn("onedrive", tags)
        with tempfile.TemporaryDirectory() as tmp:
            path = put(Path(tmp), "week 3.md", "---\ntags: [study]\ndate: 2026-06-10\n---\n# Week 3\n\nPaintings.")
            text = convert.text_of(path, rank.RULES[9])
            self.assertTrue(text.startswith("# Week 3"))
            vault = Path(tmp) / "Record"
            put(vault, "Sources/Study notes/tags.md", "---\ntitle: tags\n---\n")
            put(vault, "Organisations/Brindle Arts Centre.md", "---\ntitle: Brindle Arts Centre\n---\n")
            self.assertEqual(set(notes.known_titles(vault)), {"brindle arts centre"})
            lecture = put(Path(tmp), "12. Reference Types.md", "# Reference types\n\nA reference points at a value.")
            self.assertEqual(rank.rule_for("Programming/12. Reference Types.md", lecture).doc_type, "notes")
            letter = put(Path(tmp), "Reference.md", "To whom it may concern,\n\nI have known Avery for two years.")
            self.assertEqual(rank.rule_for("Jobs/Reference.md", letter).doc_type, "reference")

    def test_a_quote_split_by_markdown_across_lines_is_still_found(self):
        # every job of a Markdown CV came out "Check this": the heading and the dates were on two lines
        text = "#### Gallery assistant, Brindle Arts Centre\n**May 2022 - June 2024**\n- Ran tours."
        self.assertTrue(understand.quote_found("Gallery assistant, Brindle Arts Centre May 2022 - June 2024", text))
        self.assertFalse(understand.quote_found("Gallery assistant, Brindle Arts Centre Ma", text))

    def test_one_entity_spelt_several_ways_has_one_note(self):
        # "3D Modeling" and "3D modeling" were two skill notes (2026-11-05)
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            for n, name in enumerate(["Oral History", "oral history", "Oral-history"]):
                notes.add_mentions(vault, {"title": f"Note {n}", "entities": {}, "facts": [
                    {"kind": "skill", "fields": {"name": name}}]}, f"Sources/x/{n}.md")
            self.assertEqual([p.name for p in (vault / "Skills").iterdir()], ["Oral History.md"])

    def test_only_what_belongs_in_the_record_gets_an_entity_note(self):
        desc = {"title": "Lecture 3", "role": "taught",
                "entities": {"people": ["Dr Pell"], "places": ["Room 4.005"], "organisations": ["Nintendo"],
                             "courses": ["GEO101 Rivers", "Rivers (GEO101)", "GEO 101"], "skills": ["Slides"]},
                "facts": [{"kind": "skill", "fields": {"name": "Fieldwork"}}]}
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            notes.add_mentions(vault, desc, "Sources/x/1.md")
            notes.add_mentions(vault, {**desc, "entities": {**desc["entities"], "courses": ["Rivers (GEO101)"]}},
                               "Sources/x/2.md")
            made = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*.md"))
        self.assertEqual(made, ["Courses/GEO101 Rivers.md", "Skills/Fieldwork.md"])

    def test_code_text_is_fenced_so_its_brackets_are_not_links(self):
        lua = "local corners = {[[1, 6]], [[2, 7]]}\n-- ```not a fence```"
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            desc = {**understand.light("pilot.lua", lua, rank.CODE), "role": "own"}
            rel = notes.write_source(vault, desc, text=lua, source="/files/pilot.lua", sha256="", original=None)
            body = (vault / rel).read_text(encoding="utf-8")
        self.assertIn("````\nlocal corners = {[[1, 6]], [[2, 7]]}", body)

    def test_a_persons_own_note_at_an_entity_path_is_never_written_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            own = put(vault, "Projects/Oral history archive.md", "# Oral history archive\n\nMy own words.\n")
            desc = {"title": "Interview notes", "entities": {"projects": ["Oral history archive"]}}
            notes.add_mentions(vault, desc, "Sources/Study notes/Interview notes.md")
            self.assertEqual(own.read_text(encoding="utf-8"), "# Oral history archive\n\nMy own words.\n")

    def test_a_project_copied_without_its_version_control_is_still_one_unit(self):
        self.assertTrue(discover.is_repo(["README.md", "LICENSE", "docs", "media"]))
        self.assertTrue(discover.is_repo(["README.md", "main.lua", "util.lua"]))
        self.assertFalse(discover.is_repo(["README.md", "week 1.md", "week 2.md"]))
        self.assertFalse(discover.is_repo([".obsidian", ".git", "README.md", ".gitignore", "Areas"]))

    def test_a_plain_text_cv_with_a_dull_name_ranks_as_a_cv_by_its_first_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = put(Path(tmp), "notes.txt", "Curriculum vitae\n\nAvery Quillon")
            self.assertEqual(rank.rule_for("notes.txt", path).doc_type, "cv")


class Understanding(unittest.TestCase):
    def test_an_empty_conversion_never_reaches_the_model(self):
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}) as backend:
            put(files, "Scanned CV.pdf", b"%PDF-1.4\n%%EOF\n")
            run(con, [files])
            self.assertEqual(rows(con)["Files/Scanned CV.pdf"]["state"], "no_text")
            self.assertEqual(backend.requests("ingest_doc"), [])
        with self.assertRaises(ValueError):
            understand.ask("x.md", "   ", rank.RULES[0])

    def test_a_document_is_asked_about_again_when_the_ai_comes_back(self):
        # a long ingest read every document of a gateway restart by rules alone (2026-11-05)
        from jobhunt import ai

        reply = json.dumps(REPLY)
        with mock.patch.object(understand, "RETRY_WAITS_S", (0, 0)), \
                mock.patch.object(understand, "_answered", True), \
                mock.patch.object(ai, "ready", return_value="Hal's gateway does not answer."), \
                mock.patch.object(ai, "chat_json", side_effect=[ai.LLMUnavailable("down"), json.loads(reply)]) as ask:
            desc = understand.ask("CV.md", CV_TEXT, rank.RULES[0])
        self.assertEqual((desc["by"], ask.call_count), ("model", 2))
        with mock.patch.object(understand, "RETRY_WAITS_S", (0, 0)), \
                mock.patch.object(ai, "ready", return_value=None), \
                mock.patch.object(ai, "chat_json", side_effect=ai.LLMUnavailable("too long")) as ask:
            desc = understand.ask("CV.md", CV_TEXT, rank.RULES[0])
        self.assertEqual((desc["by"], ask.call_count), ("rules", 1))
        # an AI that never answered is not waited for: with none set up, every document would wait minutes
        with mock.patch.object(understand, "RETRY_WAITS_S", (0, 0)), \
                mock.patch.object(understand, "_answered", False), \
                mock.patch.object(ai, "ready", return_value="No AI is set up."), \
                mock.patch.object(ai, "chat_json", side_effect=ai.LLMUnavailable("none")) as ask:
            desc = understand.ask("CV.md", CV_TEXT, rank.RULES[0])
        self.assertEqual((desc["by"], ask.call_count), ("rules", 1))

    def test_a_quote_not_in_the_text_marks_the_fact_check_this(self):
        found = []
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}), \
                mock.patch("jobhunt.profile.store.add_found", lambda c, k, f, e: found.append((k, e))):
            settings.set("you.name", OWNER)
            put(files, "CV.md", CV_TEXT)
            run(con, [files])
            note = (vault / rows(con)["Files/CV.md"]["note"]).read_text(encoding="utf-8")
            fm, _ = frontmatter.parse(note)
        self.assertIn("**Check this:** Achievement: Won the Wexley Prize.", note)
        self.assertNotIn("Check this:** Job", note)
        self.assertEqual(fm["hal"], "needs-review")
        self.assertEqual([(k, e["verified"]) for k, e in found], [("job", True), ("achievement", False)])
        self.assertTrue(found[0][1]["source_rel"].startswith("Sources/CVs and letters/"))
        self.assertEqual(found[0][1]["quote"], "Gallery assistant, Brindle Arts Centre")

    def test_the_profile_keeps_which_quotes_were_found_in_the_file(self):
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}):
            settings.set("you.name", OWNER)
            put(files, "CV.md", CV_TEXT)
            run(con, [files])
            kept = con.execute("SELECT i.kind, e.verified FROM profile_evidence e JOIN profile_items i "
                               "ON i.id = e.item_id ORDER BY e.id").fetchall()
        self.assertEqual([(r["kind"], r["verified"]) for r in kept], [("job", 1), ("achievement", 0)])

    def test_a_fact_the_profile_refuses_leaves_the_rest_of_the_document(self):
        # the profile store raises ValueError for a fact it cannot name; it stopped the whole document
        from jobhunt.profile import store

        real, kinds = store.add_found, []

        def picky(con, kind, fields, evidence):
            if kind == "achievement":
                raise ValueError("An achievement needs a title.")
            kinds.append(kind)
            return real(con, kind, fields, evidence)

        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}), \
                mock.patch.object(store, "add_found", picky):
            settings.set("you.name", OWNER)
            put(files, "CV.md", CV_TEXT)
            run(con, [files])
            self.assertEqual(rows(con)["Files/CV.md"]["state"], "done")
        self.assertEqual(kinds, ["job"])

    def test_the_reading_prompt_says_a_score_in_their_own_work_is_no_achievement(self):
        # "The final iteration achieved a peak fitness of 0.981" became a "Peak Fitness Achievement" (2026-11-06)
        from jobhunt.ai import prompts
        text = prompts.text(understand.PURPOSE, name=OWNER)
        self.assertIn(f"recognition given to {OWNER}, never a result or score in their own work", text)

    def test_an_organisation_the_document_never_names_is_left_out_of_a_fact(self):
        # a module report naming no university gave a degree from "Trinity College Dublin" (2026-11-06)
        text = "Coursework for GEO101 by Avery Quillon, Wexley Museum placement."
        got = understand.facts([
            {"kind": "education", "fields": {"qualification": "BA Geography", "institution": "Trinity College"},
             "quote": "GEO101", "field": "qualification"},
            {"kind": "job", "fields": {"title": "Placement", "employer": "Wexley Museum"}, "quote": "placement",
             "field": "title"}], text, "Uni of Fenwick/year 2/report.md")
        self.assertEqual([f["fields"] for f in got], [{"qualification": "BA Geography"},
                                                      {"title": "Placement", "employer": "Wexley Museum"}])
        got = understand.facts([{"kind": "education", "fields": {"institution": "Uni of Fenwick"}, "quote": "x"}],
                               text, "Uni of Fenwick/year 2/report.md")
        self.assertEqual(got[0]["fields"], {"institution": "Uni of Fenwick"}, "the path names it")

    def test_skills_and_basics_reach_the_profile_in_its_own_shape(self):
        # the model wrote skills with "evidence" and basics as {name, email, ...}; the profile refused all of
        # them and a whole ingest found no skills (2026-11-05)
        got = understand.facts([
            {"kind": "skill", "fields": {"name": "Archival research", "level": "", "evidence": "Catalogued"},
             "quote": "Catalogued", "field": "name"},
            {"kind": "basic", "fields": {"name": "Avery Quillon", "email": "avery@example.org", "phone": ""},
             "quote": "Avery Quillon", "field": "name"}], "Avery Quillon. Catalogued letters.")
        self.assertEqual([(f["kind"], f["fields"]) for f in got],
                         [("skill", {"name": "Archival research"}),
                          ("basic", {"key": "name", "value": "Avery Quillon"}),
                          ("basic", {"key": "email", "value": "avery@example.org"})])

    def test_someone_elses_document_is_not_filed_and_gives_nothing(self):
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        found = []
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps({**REPLY, "role": "others",
                                                                                "author": "Morwenna Tregarth"})}), \
                mock.patch("jobhunt.profile.store.add_found", lambda c, k, f, e: found.append(k)):
            settings.set("you.name", OWNER)
            put(files, "Friend CV.md", other)
            run(con, [files])
            row = rows(con)["Files/Friend CV.md"]
            self.assertEqual((row["state"], row["role"], row["note"]), ("not_mine", "others", None))
            self.assertEqual([p for p in vault.rglob("*.md") if p.stem != "Index"], [])
        self.assertEqual(found, [])

    def test_a_document_the_ai_calls_theirs_is_theirs_only_when_it_names_them(self):
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        words = roles.identity(OWNER)
        self.assertEqual(roles.decide("own", doc_type="cv", text=other, words=words), "unsure")
        self.assertEqual(roles.decide(True, doc_type="cv", text=CV_TEXT, words=words), "own")
        self.assertEqual(roles.decide("maybe", doc_type="cv", text=CV_TEXT, words=words), "unsure")
        self.assertEqual(roles.decide("taught", doc_type="slides", text=other, words=words), "taught")
        self.assertEqual(roles.decide(None, doc_type="slides", text="", words=words), "taught")
        self.assertEqual(roles.decide(None, doc_type="notes", text="", words=words), "unsure")
        self.assertEqual(roles.decide(None, doc_type="notes", text="", words=words, in_course=True), "taught")

    def test_a_document_is_someone_elses_only_when_it_names_who_made_it(self):
        """A project folder naming no one was called someone else's and left the record (2026-11-06)."""
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        words = roles.identity(OWNER)
        decide = functools.partial(roles.decide, "others", doc_type="cv", words=words)
        self.assertEqual(decide(text=other, author="Morwenna Tregarth"), "others")
        self.assertEqual(decide(text=other, author=""), "unsure", "no maker named")
        self.assertEqual(decide(text=other, author="Ifan Rhys"), "unsure", "a maker the text does not name")
        self.assertEqual(decide(text=CV_TEXT, author=OWNER), "unsure", "the person made it")
        self.assertEqual(decide(text=f"{OWNER}/practical-1\nStarter code by Morwenna Tregarth",
                                author="Morwenna Tregarth"), "unsure", "the path names the person")
        words = roles.identity(OWNER, "230466099")
        self.assertTrue(roles.names_person("Java/230466099_cs1035_practicals/part1", words), "underscores part words")
        self.assertFalse(roles.names_person("Java/1230466099_cs1035/part1", words))

    def test_a_project_naming_no_maker_is_filed_and_gives_nothing(self):
        reply = {**REPLY, "doc_type": "project", "role": "others", "author": "",
                 "facts": [{"kind": "project", "fields": {"name": "Tide gauge bot"}, "field": "name",
                            "quote": "Tide gauge bot"}]}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(reply)}):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            put(files, "bot/README.md", "# Tide gauge bot\n\nReads the made-up bay's gauges every hour.")
            run(con, [files])
            row = rows(con)["Files/bot/README.md"]
            facts = con.execute("SELECT count(*) FROM profile_items").fetchone()[0]
        self.assertEqual((row["state"], row["role"], facts), ("done", "unsure", 0))

    def test_another_name_or_a_username_from_their_cv_makes_a_document_theirs(self):
        with home() as (con, _vault, _files):
            from jobhunt.profile import store
            store.add_found(con, "link", {"label": "GitHub", "url": "https://github.com/aquill0n"},
                            {"source_rel": "Sources/CV.md", "doc_type": "cv"})
            store.add_found(con, "link", {"label": "Blog", "url": "https://github.com/someoneelse"},
                            {"source_rel": "Sources/Notes.md", "doc_type": "notes"})
            words = roles.identity(OWNER, "c9912345, Al", con)
        self.assertIn("aquill0n", words)
        self.assertIn("c9912345", words)
        self.assertNotIn("someoneelse", words, "only links from the person's own CVs and letters")
        self.assertNotIn("Al", words, "too short to match safely")
        self.assertTrue(roles.names_person("Submitted by c9912345 for module GEO101", words))
        self.assertTrue(roles.names_person("Commits by: aquill0n <a@example.org>", words))
        self.assertFalse(roles.names_person("Avery-ish things", ["Avery Quillon"]))

    def test_the_username_in_the_persons_settings_names_them_before_any_cv_is_read(self):
        # a fresh read with the CVs held back called the person's own repositories unsure (2026-11-06)
        reply = {**REPLY, "doc_type": "repository", "category": "Code", "title": "tide-gauge", "role": "own",
                 "facts": []}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(reply)}):
            settings.set("you.name", OWNER)
            settings.set("you.links.github", "https://github.com/aquill0n")
            repo = files / "tide-gauge"
            put(repo, "README.md", "# Tide gauge\n\nReads a made-up sensor.")
            put(repo, ".git/logs/HEAD", "0000000 1111111 aquill0n <a@example.org> 1700000000 +0000"
                                        "\tcommit (initial): start\n")
            run(con, [files])
            role = rows(con)["Files/tide-gauge"]["role"]
        self.assertEqual(role, "own")

    def test_a_repository_says_who_made_its_commits_so_the_persons_code_is_theirs(self):
        with home() as (_con, _vault, files):
            repo = files / "tide-gauge"
            put(repo, "README.md", "# Tide gauge\n\nReads a made-up sensor.")
            put(repo, "pyproject.toml", "[project]\nname = 'tide-gauge'\n")
            put(repo, ".git/logs/HEAD", "0000000 1111111 Avery Quillon <avery@example.org> 1700000000 +0000"
                                        "\tcommit (initial): start\n")
            put(repo, ".git/config", '[remote "origin"]\n\turl = https://github.com/aquill0n/tide-gauge.git\n')
            text = engine.convert.repo_text(repo)
        self.assertIn("Commits by: Avery Quillon <avery@example.org>", text)
        self.assertIn("Published at: https://github.com/aquill0n/tide-gauge.git", text)
        self.assertEqual(roles.decide("own", doc_type="repository", text=text, words=roles.identity(OWNER)), "own")

    def test_course_material_gives_only_the_skills_it_teaches(self):
        slides = "# Lecture 3: Rivers\n\nDr Pell, pell@example.org. Meanders, oxbow lakes, fieldwork methods."
        reply = {**NOTE_REPLY, "doc_type": "slides", "role": "taught",
                 "facts": [{"kind": "basic", "fields": {"key": "email", "value": "pell@example.org"},
                            "field": "value", "quote": "pell@example.org"},
                           {"kind": "job", "fields": {"title": "Lecturer", "employer": "Dr Pell"},
                            "field": "title", "quote": "Dr Pell"},
                           {"kind": "skill", "fields": {"name": "Fieldwork methods"}, "field": "name",
                            "quote": "fieldwork methods"}]}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(reply)}):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            put(files, "GEO101/Lecture 3.md", slides)
            run(con, [files])
            got = con.execute("SELECT i.kind, e.role FROM profile_items i JOIN profile_evidence e "
                              "ON e.item_id = i.id").fetchall()
        self.assertEqual([tuple(r) for r in got], [("skill", "taught")])

    def test_contact_details_come_only_from_a_cv_or_a_letter(self):
        report = f"# Group report\n\nBy {OWNER} and Sam Pike (sam.pike@example.org)."
        reply = {**REPLY, "doc_type": "report", "category": "Coursework", "role": "own",
                 "facts": [{"kind": "basic", "fields": {"key": "email", "value": "sam.pike@example.org"},
                            "field": "value", "quote": "sam.pike@example.org"},
                           {"kind": "project", "fields": {"name": "Group report"}, "field": "name",
                            "quote": "Group report"}]}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(reply)}):
            settings.set("you.name", OWNER)
            put(files, "Group report.md", report)
            run(con, [files])
            kinds = [r[0] for r in con.execute("SELECT kind FROM profile_items")]
        self.assertEqual(kinds, ["project"])

    def test_notes_have_list_tags_and_links_to_entity_notes(self):
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}):
            settings.set("you.name", OWNER)
            put(files, "CV.md", CV_TEXT)
            run(con, [files])
            rel = rows(con)["Files/CV.md"]["note"]
            text = (vault / rel).read_text(encoding="utf-8")
            fm, body = frontmatter.parse(text)
            self.assertIn("tags:\n  - cv\n  - history-of-art\n", text)
            self.assertIsInstance(fm["tags"], list)
            self.assertGreaterEqual(len(fm["tags"]), understand.TAGS_MIN)
            self.assertEqual(fm["organisations"], ["Brindle Arts Centre", "University of Elsewhere"])
            self.assertTrue(fm["ingested"].endswith("Z"))
            self.assertIn("[[Organisations/Brindle Arts Centre|Brindle Arts Centre]]", body)
            entity = (vault / "Organisations" / "Brindle Arts Centre.md").read_text(encoding="utf-8")
            self.assertIn("type: entity", entity)
            self.assertIn(f"- {notes.link(rel, 'Avery Quillon CV')}", entity)
            index = (vault / "Sources" / "CVs and letters" / "Index.md").read_text(encoding="utf-8")
            self.assertIn("Avery Quillon CV", index)
            self.assertFalse(record.should_index(entity), "entity notes are links, not content to search")
            self.assertTrue(record.sync()["notes"] >= 1)

    def test_an_organisation_the_document_never_names_gets_no_note_or_tag(self):
        # a module report naming no university came back from "Trinity College Dublin" (2026-11-05)
        report = "Avery Quillon - a1234567\n\nA report on genetic algorithms for module HIS2034."
        guess = {**REPLY, "doc_type": "report", "category": "Coursework", "title": "Algorithms report",
                 "tags": [*REPLY["tags"], "glenmore-university", "kestrel-town"],
                 "entities": {**REPLY["entities"], "organisations": ["Glenmore University"],
                              "places": ["Kestrel Town"], "courses": ["HIS2034"]}, "facts": []}
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(guess)}):
            settings.set("you.name", OWNER)
            put(files, "BA History/year 2/HIS2034/Algorithms report.md", report)
            run(con, [files])
            fm, _ = frontmatter.parse((vault / rows(con)["Files/BA History/year 2/HIS2034/Algorithms report.md"]
                                       ["note"]).read_text(encoding="utf-8"))
            self.assertEqual(fm["organisations"], [])
            self.assertNotIn("glenmore-university", fm["tags"])
            self.assertNotIn("kestrel-town", fm["tags"])
            self.assertFalse((vault / "Organisations" / "Glenmore University.md").exists())
            self.assertFalse((vault / "Places" / "Kestrel Town.md").exists())
            self.assertTrue((vault / "Courses" / "HIS2034.md").exists(), "a course its path names is kept")

    def test_a_light_note_gets_tags_without_any_model(self):
        with home() as (con, vault, files), fake_ai({}) as backend:
            put(files, "Revision/week 3 notes.md", "# Rivers\n\nMeanders and oxbow lakes, 2022 fieldwork.")
            run(con, [files])
            fm, _ = frontmatter.parse((vault / rows(con)["Files/Revision/week 3 notes.md"]["note"])
                                      .read_text(encoding="utf-8"))
            self.assertEqual(backend.requests(), [])
            self.assertIn("year-2022", fm["tags"])
            self.assertIn("revision", fm["tags"])

    def test_the_link_pass_links_a_known_title_once_outside_headings_and_code(self):
        titles = {"brindle arts centre": "Organisations/Brindle Arts Centre.md"}
        text = ("---\ntitle: x\n---\n# Brindle Arts Centre\n\nAt Brindle Arts Centre. Again Brindle Arts Centre.\n"
                "`Brindle Arts Centre`\n")
        out = notes.link_note(text, titles, "Sources/Other/x.md")
        self.assertEqual(out.count("[[Organisations/Brindle Arts Centre|Brindle Arts Centre]]"), 1)
        self.assertIn("# Brindle Arts Centre\n", out)
        self.assertIn("`Brindle Arts Centre`", out)


    def test_linking_a_long_note_against_thousands_of_titles_is_quick(self):
        # one regular expression of every title, compiled per note, took hours on a 12,000-note dump (2026-11-05)
        titles = {f"made up place {n}": f"Places/Made up place {n}.md" for n in range(15000)}
        index = notes.TitleIndex(titles)
        text = "---\ntitle: x\n---\n" + "Walked past made up place 14999 and other words. " * 4000
        start = time.monotonic()
        out = notes.link_note(text, index, "Sources/Other/x.md")
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(out.count("[[Places/Made up place 14999|made up place 14999]]"), 1)


class ReadingEverything(unittest.TestCase):
    def test_with_read_all_on_notes_go_to_the_ai_and_code_files_still_do_not(self):
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(NOTE_REPLY)}) as backend:
            settings.set("record.read_all", True)
            put(files, "Revision/week 3 notes.md", "# Rivers\n\nMeanders and oxbow lakes, 2022 fieldwork.")
            put(files, "scratch/sort.py", "def sort(xs):\n    return sorted(xs)\n")
            run(con, [files])
            got = rows(con)
            self.assertEqual(got["Files/Revision/week 3 notes.md"]["model"], 1)
            self.assertEqual(got["Files/scratch/sort.py"]["model"], 0)
            self.assertEqual(len(backend.requests("ingest_doc")), 1)

    def test_reading_again_rewrites_a_rules_note_in_place_and_keeps_its_links(self):
        notes_text = "# Rivers\n\nMeanders and oxbow lakes, 2022 fieldwork."
        with home() as (con, vault, files):
            with fake_ai({}):
                put(files, "GEO101/week 1 notes.md", notes_text)
                put(files, "GEO101/week 2 notes.md", "# Coasts\n\nLongshore drift along the made-up bay.")
                put(files, "GEO101/week 3 notes.md", "# Glaciers\n\nCirques and aretes in the made-up valley.")
                put(files, "Old/week 1 notes copy.md", notes_text)
                run(con, [files])
            first = rows(con)["Files/GEO101/week 1 notes.md"]["note"]
            before = (vault / first).read_text(encoding="utf-8")
            self.assertIn("## Part of", before)
            self.assertIn("## Also at", before)
            self.assertEqual(items.by_rules(con), 3)
            with fake_ai({"ingest_doc": json.dumps(NOTE_REPLY)}) as backend:
                run(con, [], batch="b2", again=True)
            self.assertEqual(len(backend.requests("ingest_doc")), 3, "each document once, not its copy")
            after = (vault / first).read_text(encoding="utf-8")
            fm, body = frontmatter.parse(after)
            self.assertEqual(fm["title"], "Rivers and meanders")
            self.assertIn("Notes on how rivers meander", body)
            self.assertIn("## Part of", body)
            self.assertIn("## Also at", body)
            self.assertEqual(len(list((vault / "Sources").rglob("Rivers and meanders*.md"))), 0,
                             "the note keeps its own path, so links to it still lead to it")
            self.assertEqual(items.by_rules(con), 0)
            self.assertEqual({r["model"] for r in rows(con, "b2").values()}, {1})

    def test_a_note_the_ai_could_not_read_again_is_left_as_it_was(self):
        with home() as (con, vault, files):
            with fake_ai({}):
                put(files, "Revision/week 3 notes.md", "# Rivers\n\nMeanders and oxbow lakes, 2022 fieldwork.")
                run(con, [files])
            note = rows(con)["Files/Revision/week 3 notes.md"]["note"]
            before = (vault / note).read_text(encoding="utf-8")
            with fake_ai({}):            # the AI answers nothing: the note stays a rules note
                run(con, [], batch="b2", again=True)
            self.assertEqual((vault / note).read_text(encoding="utf-8"), before)
            self.assertEqual(items.by_rules(con), 1, "it is read again next time")

    def test_reading_a_note_again_replaces_the_facts_it_gave(self):
        # a note read again kept its first reading's facts beside the new ones, so a document since judged
        # someone else's still gave the person its project (2026-11-05)
        poster = f"# Poster\n\nBy Sam Pike, with {OWNER}. Tide gauges along the made-up bay."
        mine = {**REPLY, "doc_type": "report", "role": "own",
                "facts": [{"kind": "project", "fields": {"name": "Tide gauges"}, "field": "name",
                           "quote": "Tide gauges"}]}
        with home() as (con, vault, files):
            settings.set("you.name", OWNER)
            with fake_ai({"ingest_doc": json.dumps(mine)}):
                put(files, "Tide report.md", poster)
                run(con, [files])
            self.assertEqual([r[0] for r in con.execute("SELECT kind FROM profile_items")], ["project"])
            con.execute("UPDATE ingest_items SET model = 0")
            con.commit()
            put(files, "Tide report.md", "# Poster\n\nBy Sam Pike. Tide gauges along the made-up bay.")
            with fake_ai({"ingest_doc": json.dumps({**mine, "role": "others", "author": "Sam Pike"})}):
                run(con, [], batch="b2", again=True)
            self.assertEqual(con.execute("SELECT count(*) FROM profile_items").fetchone()[0], 0)
            row = rows(con, "b2")["Files/Tide report.md"]
            self.assertEqual((row["state"], row["note"]), ("not_mine", None))
            self.assertEqual([p for p in (vault / "Sources").rglob("*.md") if p.stem != "Index"], [])

    def test_several_documents_are_with_the_ai_at_once_up_to_the_limit(self):
        lock, now, most = threading.Lock(), [0], [0]

        def slow(req):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.2)
            with lock:
                now[0] -= 1
            return json.dumps({**REPLY, "title": req.user.split("\n")[0].rsplit("/", 1)[-1], "facts": []})
        with home() as (con, _vault, files), fake_ai({"ingest_doc": slow}):
            settings.set("ai.parallel", 3)
            for n in range(6):
                put(files, f"Essays/essay {n}.md", f"# Essay {n}\n\nOn the made-up town of Wexley, part {n}.")
            run(con, [files])
            self.assertEqual({r["state"] for r in rows(con).values()}, {"done"})
        self.assertGreater(most[0], 1, "more than one document was with the AI at a time")
        self.assertLessEqual(most[0], 3)


DRAFT = "# Tidal flats of the made-up bay\n\nChapter 2. Method: transects walked at low tide, 2023."
DRAFT_REPLY = {**REPLY, "doc_type": "dissertation", "category": "Coursework", "title": "Tidal flats draft",
               "role": "unsure", "facts": [{"kind": "project", "fields": {"name": "Tidal flats survey"},
                                            "field": "name", "quote": "transects walked at low tide"}]}


CORE = ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>{made}</dc:creator>'
        '<cp:lastModifiedBy>{saved}</cp:lastModifiedBy></cp:coreProperties>')
WORD = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paras}'
        '</w:body></w:document>')
ODF_META = ('<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
            '<meta:initial-creator>{made}</meta:initial-creator><dc:creator>{saved}</dc:creator>'
            '</office:meta></office:document-meta>')


def office_file(path: Path, made: str, saved: str, words: str = "") -> Path:
    """A made-up Word, PowerPoint or Excel file whose properties name `made` and `saved`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in words.splitlines() if line)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("docProps/core.xml", CORE.format(made=made, saved=saved))
        z.writestr("word/document.xml", WORD.format(paras=paras))
    return path


def pdf_by(author: str, words: str) -> bytes:
    """A one-page PDF of `words` whose information dictionary names `author`."""
    content = b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % words.encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
               b"/Contents 5 0 R >>", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
               b"<< /Author (%s) >>" % author.encode()]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class StoredAuthors(unittest.TestCase):
    """Who a file's own properties say made it starts its text, so a file naming no one in its words can name
    the person (marked coursework rarely has its author's name in it, 2026-11-06)."""

    def test_office_files_say_who_made_and_last_saved_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            for suffix in (".docx", ".pptx", ".xlsx"):
                with self.subTest(suffix=suffix):
                    made = office_file(Path(tmp) / f"work{suffix}", OWNER, "aquill0n")
                    self.assertEqual(engine.convert.author_lines(made),
                                     f"Document author: {OWNER}\nLast saved by: aquill0n")
            same = office_file(Path(tmp) / "same.docx", OWNER, OWNER.lower())
            self.assertEqual(engine.convert.author_lines(same), f"Document author: {OWNER}")

    def test_an_opendocument_file_says_who_made_and_last_saved_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            odt = Path(tmp) / "essay.odt"
            with zipfile.ZipFile(odt, "w") as z:
                z.writestr("meta.xml", ODF_META.format(made="Morwenna Tregarth", saved=OWNER))
            self.assertEqual(engine.convert.stored_by(odt), ("Morwenna Tregarth", OWNER))

    def test_a_pdf_says_who_made_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "poster.pdf"
            pdf.write_bytes(pdf_by(OWNER, "Tides of the made-up bay"))
            self.assertEqual(engine.convert.stored_by(pdf), (OWNER, ""))
            text = engine.convert.text_of(pdf, rank.OTHER)
        self.assertTrue(text.startswith(f"Document author: {OWNER}\n\n"), text)
        self.assertIn("Tides of the made-up bay", text)

    def test_names_an_office_program_or_a_computer_fills_in_are_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            for made, saved in (("Microsoft Office User", "DESKTOP-4F7G2KQ"), ("12345678", "Owner"),
                                ("", "user"), ("Author", "Administrator")):
                with self.subTest(made=made, saved=saved):
                    path = office_file(Path(tmp) / "x.docx", made, saved, "A made-up line.")
                    self.assertEqual(engine.convert.author_lines(path), "")
                    self.assertEqual(engine.convert.text_of(path, rank.RULES[6]), "A made-up line.")

    def test_a_file_with_no_words_stays_no_text_whoever_saved_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = office_file(Path(tmp) / "empty.docx", OWNER, OWNER)
            self.assertEqual(engine.convert.text_of(path, rank.RULES[6]), "")

    def test_coursework_naming_no_one_in_its_words_is_theirs_by_who_saved_it(self):
        reply = {**DRAFT_REPLY, "doc_type": "coursework", "role": "own"}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(reply)}) as backend:
            settings.set("you.name", OWNER)
            office_file(files / "Coursework 2.docx", "Morwenna Tregarth", OWNER, DRAFT)
            office_file(files / "Coursework 3.docx", "Microsoft Office User", "", DRAFT.replace("2023", "2024"))
            run(con, [files])
            got = rows(con)
            sent = backend.requests("ingest_doc")
        self.assertEqual(got["Files/Coursework 2.docx"]["role"], "own")
        self.assertEqual(got["Files/Coursework 3.docx"]["role"], "unsure")
        self.assertTrue(any(f"Last saved by: {OWNER}" in str(r) for r in sent), "the AI sees who saved it")

    def test_course_material_keeps_no_note_of_who_saved_it(self):
        reply = {**NOTE_REPLY, "doc_type": "notes", "role": "taught"}
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(reply)}):
            settings.set("record.read_all", True)
            office_file(files / "GEO101/handout.docx", "Morwenna Tregarth", "Morwenna Tregarth", "Meanders.")
            run(con, [files])
            note = (vault / rows(con)["Files/GEO101/handout.docx"]["note"]).read_text(encoding="utf-8")
        self.assertNotIn("Morwenna", note)
        self.assertIn("Meanders.", note)


COURSEWORK = f"# Coursework 2: river survey\n\nBy {OWNER}. Transects of the made-up river, measured in 2023."


def module_folder(files: Path) -> Path:
    """A module's folder of three lecture notes, a handout and coursework named as the week's notes are."""
    folder = files / "GEO101"
    for n, topic in enumerate(("Rivers", "Coasts", "Glaciers"), start=1):
        put(folder, f"Week {n} notes.md", f"# {topic}\n\nMade-up notes on {topic.lower()}.")
    put(folder, "Week 4 handout.md", "# Fieldwork safety\n\nA made-up handout.")
    put(folder, "Week 4 coursework.md", COURSEWORK)
    return folder


class CourseWork(unittest.TestCase):
    """Coursework kept in a module's folder reaches the AI: a course folder's files were all filed by rules, so
    a dump's reports and practicals there never did (2026-11-06)."""

    def test_a_course_folders_coursework_is_read_as_work_and_its_notes_stay_light(self):
        self.assertIs(rank.in_unit("GEO101/Week 4 coursework.md", rank.RULES[9]), rank.RULES[6])
        self.assertIs(rank.in_unit("GEO101/Final project.pdf", rank.RULES[9]), rank.RULES[7])
        self.assertIs(rank.in_unit("GEO101/Essay plan.docx", rank.RULES[9]), rank.RULES[8])
        self.assertIs(rank.in_unit("GEO101/Lecture 5 writing reports.pdf", rank.RULES[9]), rank.RULES[9])
        self.assertIs(rank.in_unit("GEO101/Project slides.pptx", rank.RULES[10]), rank.RULES[10])
        self.assertIs(rank.in_unit("GEO101/Week 2 notes.md", rank.RULES[9]), rank.RULES[9])

    def test_coursework_in_a_module_folder_is_read_by_the_ai_and_keeps_its_course_link(self):
        reply = {**REPLY, "doc_type": "coursework", "category": "Coursework", "title": "River survey",
                 "role": "own", "facts": []}
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(reply)}) as backend:
            settings.set("you.name", OWNER)
            module_folder(files)
            run(con, [files])
            got = rows(con)
            asked = len(backend.requests("ingest_doc"))
            work = got["Files/GEO101/Week 4 coursework.md"]
            note = (vault / work["note"]).read_text(encoding="utf-8")
            course = (vault / got["Files/GEO101"]["note"]).read_text(encoding="utf-8")
        self.assertEqual((work["model"], work["role"], work["unit"] is not None), (1, "own", True))
        self.assertEqual(asked, 1, "the notes and the handout are still filed by rules")
        self.assertEqual({got[f"Files/GEO101/Week {n} notes.md"]["model"] for n in (1, 2, 3)}, {0})
        self.assertIn("## Part of", note)
        self.assertIn(work["note"].removesuffix(".md"), course)

    def test_with_read_all_on_every_file_of_the_folder_is_read_once(self):
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(NOTE_REPLY)}) as backend:
            settings.set("record.read_all", True)
            module_folder(files)
            run(con, [files])
        self.assertEqual(len(backend.requests("ingest_doc")), 5)


PICTURE = b"\x89PNG\r\n\x1a\n" + bytes(range(64))        # a made-up picture: only its name and bytes matter


def camera_jpeg(make: str) -> bytes:
    """A made-up JPEG whose Exif names the camera's maker, as a phone's photo does."""
    value = make.encode() + b"\x00"
    entry_at = 8 + 2 + 12 + 4                                  # after the header, the count, one entry and the link
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1) \
        + struct.pack("<HHII", 0x010F, 2, len(value), entry_at) + struct.pack("<I", 0) + value
    segment = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(segment) + 2) + segment + b"\xff\xd9"


class SupportingFiles(unittest.TestCase):
    """Data, figures, logs, models and camera photos are listed with their work, never notes of their own: a
    dump gave 337 notes of photos and renders and notes of a supplement's CSV exports (2026-11-06)."""

    def test_the_kinds_of_supporting_file_are_told_from_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "Portfolio" / "sunset.jpg"
            photo.parent.mkdir()
            photo.write_bytes(camera_jpeg("Canon"))
            self.assertEqual(attach.camera_make(photo), "Canon")
            kind = functools.partial(attach.kind_of, path=Path(tmp) / "none")
            self.assertEqual(attach.kind_of("Files/Portfolio/sunset.jpg", photo), "picture", "a camera's photo")
            self.assertEqual(kind("Files/Portfolio/poster.png"), "", "artwork is read for its words")
            self.assertEqual(kind("Files/Certificates/First aid certificate.jpg"), "")
            self.assertEqual(kind("Files/Certificates/IMG_2041.jpg"), "picture", "named as a camera names it")
            self.assertEqual(kind("Files/Diss/fig1.png"), "picture")
            self.assertEqual(kind("Files/Diss/results.csv"), "data")
            self.assertEqual(kind("Files/Diss/results/summary.txt"), "data", "in a results folder")
            self.assertEqual(kind("Files/Diss/results/write-up.pdf"), "", "a document in a results folder")
            self.assertEqual(kind("Files/Bot/fix.diff"), "diff")
            self.assertEqual(kind("Files/Bot/run.log"), "log")
            self.assertEqual(kind("Files/Bot/case.stl"), "design")
            self.assertEqual(kind("Files/Bot/adder.circ"), "design")
            self.assertEqual(kind("Files/Bot/notes.md"), "")

    def test_a_projects_data_and_figures_are_listed_in_its_note_and_give_nothing(self):
        reply = {**DRAFT_REPLY, "role": "own", "title": "Tidal flats"}
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(reply)}) as backend:
            settings.set("you.name", OWNER)
            put(files, "Dissertation/Final dissertation.md", f"{DRAFT}\n\nBy {OWNER}.")
            put(files, "Dissertation/supplement/results.csv", "site,depth\n1,0.4\n")
            put(files, "Dissertation/supplement/figures/fig1.png", PICTURE)
            put(files, "Dissertation/IMG_2041.jpg", camera_jpeg("Canon"))
            put(files, "Dissertation/rig/mount.stl", "solid mount\nendsolid mount\n")
            run(con, [files])
            got = rows(con)
            report = got["Files/Dissertation/Final dissertation.md"]
            text = (vault / report["note"]).read_text(encoding="utf-8")
            sources = sorted(p.relative_to(vault).as_posix() for p in (vault / "Sources").rglob("*.md")
                             if p.stem != "Index")
            asked = len(backend.requests("ingest_doc"))
        for rel in ("supplement/results.csv", "supplement/figures/fig1.png", "IMG_2041.jpg", "rig/mount.stl"):
            row = got[f"Files/Dissertation/{rel}"]
            self.assertEqual((row["state"], row["dup_of"], row["note"]), ("attached", report["id"], report["note"]))
            self.assertIn(f"- `{rel}`", text)
        self.assertEqual(sources, [report["note"]], "no note of its own for any supporting file")
        self.assertEqual(asked, 1)
        self.assertLess(text.index("## Files"), text.index("## Original"))

    def test_a_courses_figures_join_its_note_and_loose_ones_are_skipped(self):
        with home() as (con, vault, files), fake_ai({}):
            module_folder(files)
            put(files, "GEO101/plot.png", PICTURE)
            put(files, "Camera/DSC_0042.jpg", PICTURE)
            put(files, "export.csv", "a,b\n1,2\n")
            run(con, [files])
            got = rows(con)
            course = (vault / got["Files/GEO101"]["note"]).read_text(encoding="utf-8")
            shown = items.counts(con, "b1")["states"]
        self.assertEqual((got["Files/GEO101/plot.png"]["state"], got["Files/GEO101/plot.png"]["note"]),
                         ("attached", got["Files/GEO101"]["note"]))
        self.assertIn("- `plot.png`", course)
        self.assertEqual((got["Files/Camera/DSC_0042.jpg"]["state"], got["Files/Camera/DSC_0042.jpg"]["error"]),
                         ("skipped", "a picture with no words"))
        self.assertEqual(got["Files/export.csv"]["state"], "skipped")
        self.assertEqual(shown["attached"], 1)

    def test_a_certificate_picture_with_words_is_still_read_and_one_without_is_skipped(self):
        def ocr(path):
            return "Certificate of completion\n\nFirst aid at work, 2025." if "first" in path.name.lower() else ""

        with home() as (con, _vault, files), fake_ai({}), mock.patch.object(record, "text_of", ocr):
            put(files, "First aid certificate.png", PICTURE)
            put(files, "Swimming certificate.png", PICTURE + b"\x01")
            run(con, [files])
            got = rows(con)
        self.assertEqual(got["Files/First aid certificate.png"]["state"], "done")
        swim = got["Files/Swimming certificate.png"]
        self.assertEqual((swim["state"], swim["note"]), ("skipped", None))

    def test_reading_a_projects_note_again_keeps_its_list_of_files(self):
        with home() as (con, vault, files):
            with fake_ai({}):
                put(files, "Dissertation/Final dissertation.md", DRAFT)
                put(files, "Dissertation/results.csv", "site,depth\n1,0.4\n")
                run(con, [files])
            with fake_ai({"ingest_doc": json.dumps(DRAFT_REPLY)}):
                run(con, [], batch="b2", again=True)
            report = rows(con, "b2")["Files/Dissertation/Final dissertation.md"]
            text = (vault / report["note"]).read_text(encoding="utf-8")
        self.assertEqual(report["model"], 1)
        self.assertIn("- `results.csv`", text)


STUB = """---
type: topic
status: stub
---
# Recursion

Concept stub, auto-created by a made-up tool.

## Notes

_(empty section)_

```dataview
LIST FROM [[Recursion]]
```
"""
FROM_SLIDES = """---
source_file: Course/CSC101/Slides/Lecture_4.pdf
role: lecture
---
# Lecture 4: Recursion

A function that calls itself, a base case that stops it, and the call stack that holds each call.
"""


class VaultNotes(unittest.TestCase):
    """A vault's tool-made notes: thousands of concept stubs and notes made from slides each became a note of
    the record and buried the rest (2026-11-07)."""

    def test_a_note_with_no_words_of_its_own_is_empty(self):
        self.assertTrue(vaultnotes.is_empty("Vault/Recursion.md", STUB))
        self.assertTrue(vaultnotes.is_empty("Vault/Index.md", "# Index\n\n- [[A]]\n- [[B]], [[C]]\n![[map.png]]\n"))
        self.assertFalse(vaultnotes.is_empty("Vault/Todo.md", "# Todo\n\nRing the placement office on Monday."))
        self.assertFalse(vaultnotes.is_empty("Vault/report.pdf", "Two words"), "only text and Markdown files")

    def test_a_note_names_the_slides_it_was_made_from(self):
        fm = frontmatter.parse(FROM_SLIDES)[0]
        self.assertEqual(vaultnotes.source_of(fm), "Course/CSC101/Slides/Lecture_4.pdf")
        self.assertTrue(vaultnotes.taught_from_source(fm))
        self.assertTrue(vaultnotes.taught_from_source({"source": "[[Week 3 slides.pptx]]"}))
        self.assertTrue(vaultnotes.taught_from_source({"source_path": "Uni/Exam 2022.pdf", "type": "exam"}))
        self.assertFalse(vaultnotes.taught_from_source({"source": "My dissertation.pdf"}), "not course material")
        self.assertFalse(vaultnotes.taught_from_source({"source": "Slides/Lecture_4.pdf", "hal": "ingested"}),
                         "a note Hal wrote names its source too")
        self.assertFalse(vaultnotes.taught_from_source({"source": "https://example.org/page"}))

    def test_stubs_are_skipped_and_notes_from_slides_are_course_material_without_the_ai(self):
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(NOTE_REPLY)}) as backend:
            settings.set("record.read_all", True)
            put(files, "Vault/Recursion.md", STUB)
            put(files, "Vault/Lecture 4 Recursion.md", FROM_SLIDES)
            put(files, "Vault/Rivers.md", "# Rivers\n\nMeanders and oxbow lakes from the 2022 fieldwork trip.")
            run(con, [files])
            got = rows(con)
            asked = len(backend.requests("ingest_doc"))
            shown = items.counts(con, "b1")["states"]
            again = items.by_rules(con)
        stub, derived = got["Files/Vault/Recursion.md"], got["Files/Vault/Lecture 4 Recursion.md"]
        self.assertEqual((stub["state"], stub["error"], stub["note"]), ("skipped", "an empty note", None))
        self.assertEqual((derived["state"], derived["role"], derived["model"]), ("done", "taught", items.BY_SOURCE))
        self.assertEqual(asked, 1, "only the person's own note goes to the AI")
        self.assertEqual(shown["skipped"], 1)
        self.assertEqual(again, 0, "a note settled by its source is not offered to the AI again")

    def test_the_page_counts_empty_notes_in_plain_words(self):
        with home() as (con, _vault, files), fake_ai({}):
            put(files, "Vault/Recursion.md", STUB)
            done = finished(ingest_actions.queue(folders=[files]))
            self.assertEqual(done["state"], "done", done["error"])
            shown = items.summary(con)
        self.assertEqual(shown["skipped"], {"an empty note": 1})


class Origins(unittest.TestCase):
    """Where the person said a folder came from settles whose its documents are (`roles.ORIGINS`).

    On a real dump, dissertation drafts, a poster and practicals naming no one were all unsure (2026-11-06).
    """

    def test_a_folder_of_their_own_work_counts_as_theirs_without_naming_them(self):
        words = roles.identity(OWNER)
        mine = functools.partial(roles.decide, text=DRAFT, words=words, origin=roles.MINE)
        self.assertEqual(mine("own", doc_type="dissertation"), "own", "no name needed")
        self.assertEqual(mine("unsure", doc_type="dissertation"), "own")
        self.assertEqual(mine("unsure", doc_type="slides"), "unsure", "slides are course material by kind")
        self.assertEqual(mine("taught", doc_type="notes"), "taught")
        self.assertEqual(mine(None, doc_type="report"), "own", "a work kind without the AI")
        self.assertEqual(mine(None, doc_type="repository"), "own")
        self.assertEqual(mine(None, doc_type="notes"), "unsure")
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        self.assertEqual(roles.decide("others", doc_type="cv", text=other, words=words, author="Morwenna Tregarth",
                                      origin=roles.MINE), "others", "someone else's keeps its rule")
        self.assertEqual(mine("others", doc_type="report", author=""), "unsure")

    def test_course_material_is_taught_unless_it_is_their_own_named_submission(self):
        words = roles.identity(OWNER)
        course = functools.partial(roles.decide, words=words, origin=roles.COURSE)
        self.assertEqual(course("own", doc_type="report", text=DRAFT), "taught", "own needs their name here")
        self.assertEqual(course("own", doc_type="report", text=f"Submitted by {OWNER}\n{DRAFT}"), "own")
        self.assertEqual(course("unsure", doc_type="report", text=DRAFT), "taught")
        self.assertEqual(course(None, doc_type="report", text=DRAFT), "taught")
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        self.assertEqual(course("others", doc_type="cv", text=other, author="Morwenna Tregarth"), "others")
        self.assertEqual(course("others", doc_type="cv", text=other, author=""), "taught")

    def test_a_mix_keeps_the_rules_of_before(self):
        words = roles.identity(OWNER)
        for said, doc_type, want in (("own", "dissertation", "unsure"), ("unsure", "dissertation", "unsure"),
                                     (None, "report", "unsure"), (None, "slides", "taught")):
            with self.subTest(said=said, doc_type=doc_type):
                self.assertEqual(roles.decide(said, doc_type=doc_type, text=DRAFT, words=words), want)
        self.assertEqual(roles.origin_of(None), "mixed")
        with self.assertRaises(ValueError):
            roles.origin_of("theirs")

    def test_a_draft_naming_no_one_in_their_own_folder_gives_its_facts(self):
        with home() as (con, _vault, files), fake_ai({"ingest_doc": json.dumps(DRAFT_REPLY)}):
            settings.set("you.name", OWNER)
            mine, mixed = files / "Dissertation", files / "Downloads"
            put(mine, "Draft 2 dissertation.md", DRAFT)
            put(mixed, "Dissertation draft.md", DRAFT.replace("2023", "2022"))
            run(con, [mine, mixed], origins={str(mine): "mine"})
            got = rows(con)
            kinds = [r[0] for r in con.execute("SELECT kind FROM profile_items")]
        self.assertEqual((got["Dissertation/Draft 2 dissertation.md"]["origin"],
                          got["Dissertation/Draft 2 dissertation.md"]["role"]), ("mine", "own"))
        self.assertEqual((got["Downloads/Dissertation draft.md"]["origin"],
                          got["Downloads/Dissertation draft.md"]["role"]), ("mixed", "unsure"))
        self.assertEqual(kinds, ["project"])

    def test_where_a_file_came_from_stays_with_it_when_it_is_read_again(self):
        with home() as (con, _vault, files):
            with fake_ai({}):
                put(files, "Labs/week 3 notes.md", "# Rivers\n\nMeanders and oxbow lakes, 2022 fieldwork.")
                run(con, [files], origins={str(files): "course"})
            self.assertEqual(rows(con)["Files/Labs/week 3 notes.md"]["role"], "taught")
            with fake_ai({"ingest_doc": json.dumps({**NOTE_REPLY, "role": "own"})}):
                run(con, [], batch="b2", again=True)
            row = rows(con, "b2")["Files/Labs/week 3 notes.md"]
        self.assertEqual((row["origin"], row["role"], row["model"]), ("course", "taught", 1))

    def test_a_zip_and_the_drop_folder_carry_where_their_files_came_from(self):
        with home() as (con, vault, files), fake_ai({}):
            settings.set("record.drop_origin", "mine")
            put(vault / sources.DROP_DEFAULT, "Poster report.md", "# Poster\n\nA made-up poster on tides.")
            put(files, "course.zip", zip_bytes({"Week 1/lab report.md": b"# Lab\n\nA made-up lab on tides."}))
            run(con, uploads=[str(files / "course.zip")], drop=True, origins={str(files / "course.zip"): "course"})
            got = {r["rel"].rsplit("/", 1)[-1]: (r["origin"], r["role"]) for r in rows(con).values()}
        self.assertEqual(got["Poster report.md"], ("mine", "own"))
        self.assertEqual(got["lab report.md"], ("course", "taught"))

    def test_the_folder_route_takes_where_each_folder_came_from(self):
        hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(record, "VAULT", Path(paths.record_default()), create=True))
        self.addCleanup(record._CACHE.clear)
        mine, course, mixed = hal.home / "Mine", hal.home / "Modules", hal.home / "Other"
        for folder in (mine, course, mixed):
            put(folder, "a.md", "# A")
        c = hal.signed_in()
        with mock.patch.object(ingest_actions.runner, "start", return_value="a1") as start:
            out = c.post("/api/v2/record/ingest", {"folders": [{"path": str(mine), "origin": "mine"},
                                                               {"path": str(course), "origin": "course"},
                                                               str(mixed)]})
        self.assertEqual(out.status, 200, out.text)
        params = start.call_args.kwargs["params"]
        self.assertEqual(params["origins"], {str(mine.resolve()): "mine", str(course.resolve()): "course"})
        bad = c.post("/api/v2/record/ingest", {"folders": [{"path": str(mine), "origin": "theirs"}]})
        self.assertEqual(bad.status, 400)

    def test_the_command_line_names_folders_of_their_own_work_and_course_material(self):
        from jobhunt.cli import record as cli_record

        sent = []

        def hal(method, path, body=None):
            sent.append(body)
            return (200, {"action_id": "a1"}) if method == "POST" else (200, {"state": "done"})

        with mock.patch.object(cli_record, "_hal", hal), mock.patch.object(cli_record.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            cli_record.ingest(["Mixed"], mine=["Mine"], course=["Modules"])
        self.assertEqual([(Path(f["path"]).name, f["origin"]) for f in sent[0]["folders"]],
                         [("Mixed", "mixed"), ("Mine", "mine"), ("Modules", "course")])


class Frontmatter(unittest.TestCase):
    def test_lists_and_awkward_scalars_round_trip(self):
        fields = {"title": "Report: phase 2 # draft", "tags": ["a", "b-c"], "about_owner": "true", "empty": [],
                  "period": "2023-09..2024-06"}
        parsed, body = frontmatter.parse(frontmatter.dump(fields) + "# Body\n")
        self.assertEqual(parsed, fields)
        self.assertEqual(body, "# Body\n")
        self.assertEqual(frontmatter.parse("---\ntags: [x, \"y, z\"]\n---\n")[0], {"tags": ["x", "y, z"]})


class Progress(unittest.TestCase):
    def test_the_ingest_carries_on_after_a_restart_without_reading_items_twice(self):
        with home() as (con, vault, files), fake_ai({}):
            for n in range(4):
                put(files, f"Essays/essay {n}.md", f"# Essay {n}\n\nOn the made-up town of Wexley, part {n}.")
            calls = []

            def stop_after_two(done, total, text):
                calls.append(text)
                if len(calls) == 3:
                    raise runner.Stopped()
            with self.assertRaises(runner.Stopped):
                engine.run(con, "b1", folders=[str(files)], step=lambda t: None, progress=stop_after_two)
            self.assertEqual(sorted(r["state"] for r in rows(con).values()), ["done", "done", "pending", "pending"])
            again = []
            engine.run(con, "b1", folders=[str(files)], step=lambda t: None,
                       progress=lambda done, total, text: again.append((done, total, text)))
            self.assertEqual([a[:2] for a in again], [(2, 4), (3, 4), (4, 4)])
            self.assertEqual({r["state"] for r in rows(con).values()}, {"done"})
            self.assertEqual(len(list((vault / "Sources").rglob("essay *.md"))) +
                             len(list((vault / "Sources").rglob("Essay *.md"))), 4)

    def test_time_left_counts_rich_and_light_documents_at_their_own_pace(self):
        # one pace for both kinds said 21 hours for an ingest that took two (2026-11-05)
        with home() as (con, _vault, _files):
            con.execute("INSERT INTO actions (id, kind, state, priority, step_n, resumable, created, started, params) "
                        "VALUES ('a1', 'record.ingest', 'running', 2, 0, 1, '2026-11-05T08:00:00Z', "
                        "'2026-11-05T08:00:00Z', '{\"batch\": \"b1\"}')")
            def item(n, rank, state, at):
                con.execute("INSERT INTO ingest_items (batch, path, rel, kind, rank, stage, state, created, updated) "
                            "VALUES ('b1', ?, ?, 'file', ?, ?, ?, '', ?)",
                            (f"/f/{n}", f"f/{n}", rank, "written" if state == "done" else "discovered", state, at))
            item(1, 0, "done", "2026-11-05T08:01:00Z")
            item(2, 6, "done", "2026-11-05T08:01:10Z")     # a rich document: 10 s
            item(3, 9, "done", "2026-11-05T08:01:11Z")     # a light one: 1 s
            for n in range(4, 8):
                item(n, 6, "pending", "")                  # 4 rich left
            for n in range(8, 108):
                item(n, 15, "pending", "")                 # 100 light left
            con.commit()
            with mock.patch.object(items.clock, "now_utc", return_value=items.clock.parse("2026-11-05T08:01:20Z")):
                self.assertEqual(items.summary(con)["eta_s"], 4 * 10 + 100 * 1)

    def test_the_action_reports_done_total_and_the_page_summary(self):
        with home() as (con, _vault, files), fake_ai({}):
            put(files, "CV.md", CV_TEXT)
            put(files, "Cloud/Offer letter.pdf", bytes(100))
            done = finished(ingest_actions.queue(folders=[files]))
            self.assertEqual(done["state"], "done", done["error"])
            self.assertEqual((done["done_n"], done["done_total"]), (2, 2))
            shown = items.summary(con)
            self.assertEqual((shown["state"], shown["done"], shown["total"], shown["not_downloaded"]),
                             ("done", 2, 2, 1))
            self.assertEqual(shown["not_downloaded_paths"], ["Files/Cloud/Offer letter.pdf"])
            self.assertEqual(shown["categories"], {"CVs and letters": 1})

    def test_a_file_dropped_in_the_drop_folder_is_picked_up_and_moved_in(self):
        with home() as (con, vault, _files), fake_ai({}):
            drop = vault / sources.DROP_DEFAULT
            put(drop, "Certificate of completion.md", "# First aid\n\nCompleted 2025.")
            self.assertTrue(engine.drop_has_new(con))
            done = finished(ingest_actions.queue_drop())
            self.assertEqual(done["state"], "done", done["error"])
            self.assertFalse((drop / "Certificate of completion.md").exists())
            self.assertTrue((vault / record.ORIGINALS / "Certificates" / "Certificate of completion.md").exists())
            self.assertFalse(engine.drop_has_new(con))
            self.assertIsNone(ingest_actions.queue_drop())


class Routes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.folder = Path(paths.record_default())
        self.enterContext(mock.patch.object(record, "VAULT", self.folder, create=True))
        self.addCleanup(record._CACHE.clear)
        self.enterContext(fake_ai({}))
        self.files = self.hal.home / "Old laptop"

    def test_there_is_no_ingest_until_one_is_started(self):
        shown = self.hal.signed_in().get("/api/v2/record/ingest").json()
        self.assertEqual((shown["state"], shown["done"], shown["total"]), ("none", 0, 0))

    def test_a_folder_on_this_computer_is_read_and_the_page_shows_how_it_went(self):
        put(self.files, "CV.md", CV_TEXT)
        put(self.files, "OneDrive/Reference.pdf", bytes(64))
        c = self.hal.signed_in()
        started = c.post("/api/v2/record/ingest", {"folders": [str(self.files)]})
        self.assertEqual(started.status, 200, started.text)
        self.assertEqual(finished(started.json()["action_id"])["state"], "done")
        shown = c.get("/api/v2/record/ingest").json()
        self.assertEqual((shown["state"], shown["done"], shown["total"], shown["not_downloaded"]), ("done", 2, 2, 1))
        self.assertEqual(shown["not_downloaded_paths"], ["Old laptop/OneDrive/Reference.pdf"])
        self.assertEqual(shown["stages"]["written"], 1)
        self.assertTrue(list((self.folder / "Sources").rglob("*.md")))

    def test_the_local_key_starts_an_ingest_and_follows_it_without_a_window(self):
        # `python -m jobhunt ingest` runs a long ingest in the running Hal, where its progress shows
        from jobhunt.web import auth

        put(self.files, "CV.md", CV_TEXT)
        c = self.hal.client()
        key = {"Authorization": f"Bearer {auth.local_api_key()}"}
        self.assertEqual(c.get("/api/v2/record/ingest").status, 401)
        started = c.post("/api/v2/record/ingest", {"folders": [str(self.files)]}, headers=key)
        self.assertEqual(started.status, 200, started.text)
        self.assertEqual(finished(started.json()["action_id"])["state"], "done")
        self.assertEqual(c.get("/api/v2/record/ingest", headers=key).json()["state"], "done")

    def test_reading_again_is_refused_when_the_ai_has_read_everything(self):
        c = self.hal.signed_in()
        self.assertEqual(c.post("/api/v2/record/ingest/again", {}).status, 400)
        with mock.patch.object(items, "by_rules", return_value=3), \
                mock.patch.object(ingest_actions, "queue", return_value="a1") as queue:
            started = c.post("/api/v2/record/ingest/again", {})
        self.assertEqual(started.json(), {"action_id": "a1"})
        self.assertTrue(queue.call_args.kwargs["again"])

    def test_pasted_links_start_an_ingest_and_an_empty_paste_is_refused(self):
        c = self.hal.signed_in()
        self.assertEqual(c.post("/api/v2/record/links", {"urls": []}).status, 400)
        with mock.patch.object(ingest_actions, "queue", return_value="a1") as queue:
            started = c.post("/api/v2/record/links", {"urls": "https://example.org/me  https://example.org/blog"})
        self.assertEqual(started.json(), {"action_id": "a1"})
        self.assertEqual(queue.call_args.kwargs["urls"], ["https://example.org/me", "https://example.org/blog"])

    def test_reading_a_folder_by_path_is_refused_from_another_computer_and_inside_the_record(self):
        put(self.files, "CV.md", CV_TEXT)
        laptop = self.hal.signed_in(remote=True, remote_host="laptop")
        self.assertEqual(laptop.post("/api/v2/record/ingest", {"folders": [str(self.files)]}).status, 409)
        c = self.hal.signed_in()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.assertEqual(c.post("/api/v2/record/ingest", {"folders": [str(self.folder)]}).status, 400)
        self.assertEqual(c.post("/api/v2/record/ingest", {"folders": [str(self.files / "missing")]}).status, 400)
        self.assertEqual(c.post("/api/v2/record/ingest", {"folders": []}).status, 400)

    def test_an_uploaded_zip_is_unpacked_safely_and_read(self):
        from test_evidence_routes import upload

        from jobhunt.evidence import routes as evidence

        evidence.make_default()
        data = zip_bytes({"Year 1/CV.md": CV_TEXT.encode(), "../../escape.md": b"# Out"})
        status, out = upload(self.hal, self.hal.signed_in(), {"uni.zip": data})
        self.assertEqual(status, 200, out)
        self.assertIsNone(out["action_id"], "a zip alone starts the ingest, not a plain read")
        self.assertEqual(finished(out["ingest_id"])["state"], "done")
        shown = self.hal.signed_in().get("/api/v2/record/ingest").json()
        self.assertEqual(shown["states"].get("done"), 1)
        self.assertTrue(any("points outside the zip" in e["error"] for e in shown["errors"]))
        self.assertFalse((self.hal.home / "escape.md").exists())
        self.assertFalse(list(sources.uploads_dir().glob("*.zip")), "the upload is removed once it is read")

    def test_an_uploaded_zip_keeps_where_the_person_said_it_came_from(self):
        from jobhunt.evidence import routes as evidence

        evidence.make_default()
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="origin"\r\n\r\ncourse\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file0"; filename="GEO101.zip"\r\n'
                f"Content-Type: application/zip\r\n\r\n").encode() + zip_bytes({"a.md": b"# A"}) \
            + f"\r\n--{boundary}--\r\n".encode()
        c = self.hal.signed_in()
        con = http.client.HTTPConnection("127.0.0.1", self.hal.port, timeout=30)
        with mock.patch.object(ingest_actions, "queue", return_value="a1") as queue:
            try:
                con.request("POST", "/api/v2/record/files", body=body, headers={
                    "Host": f"127.0.0.1:{self.hal.port}", "X-Hal": "1",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Cookie": "; ".join(f"{k}={v}" for k, v in c.jar.items())})
                reply = con.getresponse()
                self.assertEqual(reply.status, 200, reply.read())
            finally:
                con.close()
        (zip_path, origin), = queue.call_args.kwargs["origins"].items()
        self.assertEqual((Path(zip_path).name, origin), ("GEO101.zip", "course"))

    def test_only_the_upload_route_takes_a_body_past_the_usual_cap(self):
        from jobhunt.evidence import routes as evidence
        from jobhunt.web import router

        self.assertGreater(router.find("POST", "/api/v2/record/files")[0].max_body, router.MAX_BODY)
        self.assertEqual(router.find("POST", "/api/v2/record/ingest")[0].max_body, router.MAX_BODY)
        self.assertEqual(evidence.UPLOAD_MAX_BODY, 256 * router.MB)


if __name__ == "__main__":
    unittest.main()

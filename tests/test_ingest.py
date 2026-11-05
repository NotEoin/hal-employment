"""The bulk ingest: folders and zips read into the record as notes, safely, in order, and resumably.

Every test builds made-up files in its own temporary home; the people, employers and documents are
invented. The AI is the fake one, answering the `ingest_doc` prompt with canned JSON, or none at all.
"""
import contextlib
import io
import json
import tempfile
import time
import unittest
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
from jobhunt.ingest import discover, engine, frontmatter, items, notes, rank, sources, understand

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
                notes.add_mentions(vault, {"title": f"Note {n}", "entities": {"skills": [name]}}, f"Sources/x/{n}.md")
            self.assertEqual([p.name for p in (vault / "Skills").iterdir()], ["Oral History.md"])

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

    def test_someone_elses_document_gives_no_owner_facts(self):
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        found = []
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps({**REPLY, "about_owner": False})}), \
                mock.patch("jobhunt.profile.store.add_found", lambda c, k, f, e: found.append(k)):
            settings.set("you.name", OWNER)
            put(files, "Friend CV.md", other)
            run(con, [files])
            note = (vault / rows(con)["Files/Friend CV.md"]["note"]).read_text(encoding="utf-8")
        self.assertEqual(found, [])
        self.assertEqual(frontmatter.parse(note)[0]["about_owner"], "false")
        self.assertNotIn("## Key facts", note)

    def test_a_document_the_model_calls_theirs_but_never_names_them_is_unsure(self):
        other = CV_TEXT.replace(OWNER, "Morwenna Tregarth")
        self.assertEqual(understand.judge_owner(True, OWNER, other), "unsure")
        self.assertEqual(understand.judge_owner(True, OWNER, CV_TEXT), "true")
        self.assertEqual(understand.judge_owner("maybe", OWNER, CV_TEXT), "unsure")

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

    def test_only_the_upload_route_takes_a_body_past_the_usual_cap(self):
        from jobhunt.evidence import routes as evidence
        from jobhunt.web import router

        self.assertGreater(router.find("POST", "/api/v2/record/files")[0].max_body, router.MAX_BODY)
        self.assertEqual(router.find("POST", "/api/v2/record/ingest")[0].max_body, router.MAX_BODY)
        self.assertEqual(evidence.UPLOAD_MAX_BODY, 256 * router.MB)


if __name__ == "__main__":
    unittest.main()

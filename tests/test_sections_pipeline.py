"""The pipeline of a section's source: kept, read into a note under its section's folder, its facts filed with the
section's role and rank, read again in place, and removed with everything it gave.

Each test has its own temporary home and record. The two made-up graduates are Avery Quillon (software) and
Marnie Quillfeather (history); every name, place and file is invented. Readers that later phases write are
stood in for by `Scripted`, a made-up reader that returns the facts it is given.
"""
import io
import shutil
import threading
import unittest
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_ingest import home

from jobhunt import ai, record
from jobhunt.ai import hal_gateway
from jobhunt.core import events, runner
from jobhunt.ingest import engine, frontmatter
from jobhunt.ingest import tidy as ingest_tidy
from jobhunt.profile import store as profile_store
from jobhunt.sections import actions, pipeline, readers, store
from jobhunt.sections.pipeline import Incoming

AWARD = ("# Volunteer of the year\n\nAwarded to Marnie Quillfeather on 5 March 2025 for cataloguing letters at the "
         "Fenwick Lantern Museum Trust.\n")
ESSAY = ("# The parish registers of Harrowgate Vale\n\nAn essay by Marnie Quillfeather on archival research in "
         "parish registers, 1750 to 1850.\n")
CV = ("# Avery Quillon\n\nCurriculum vitae\n\n## Work experience\n\nJunior developer, Made-up Tides Ltd, 2024.\n\n"
      "## Education\n\nBSc Made-up Computing, University of Nowhere Fen, 2021 to 2024.\n")
PRIVATE = "# Bank form\n\nSort code: 12-34-56\nAccount number: 12345678\n"


@dataclass
class Scripted:
    """A made-up reader for a section whose real reader a later phase writes: it returns `facts` as given."""

    name: str
    facts: list = field(default_factory=list)

    def read(self, source: readers.Source) -> readers.Reading:
        """The source's text, with the scripted facts."""
        return readers.Reading(title="", text=readers.text_of(source.files), facts=list(self.facts))


@dataclass
class AiAway:
    """A made-up reader whose AI does not answer for its first `fails` reads, with `kind` as the reason."""

    name: str
    fails: int
    kind: str = "timeout"
    calls: int = 0

    def read(self, source: readers.Source) -> readers.Reading:
        """Raises `LLMUnavailable` until `fails` reads have failed, then reads the source's text."""
        self.calls += 1
        if self.calls <= self.fails:
            raise ai.LLMUnavailable("backend qwen was stopped", kind=self.kind)
        return readers.Reading(title="", text=readers.text_of(source.files))


def skill(name: str, quote: str, line: int = 1) -> readers.Fact:
    """A skill fact resting on `quote` at `line` of the essay."""
    return readers.Fact("skill", {"name": name}, readers.Quote("name", quote, "essay.md", line))


@contextmanager
def reader(name: str, facts=(), made=None):
    """Puts a scripted reader (or `made`) in place of `name` for the length of a block; yields it."""
    made = made or Scripted(name, list(facts))
    before = readers.register(made)
    try:
        yield made
    finally:
        readers.register(before)


def zip_of(members: dict[str, str]) -> bytes:
    """A zip holding `members`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


class Pipeline(unittest.TestCase):
    def education(self, con) -> int:
        """The history graduate's degree, as her CV gives it."""
        return profile_store.add_found(con, "education", {"qualification": "BA", "subject": "History",
                                                          "institution": "University of Nowhere Fen"},
                                       {"source_rel": "Sources/CVs/Marnie CV.md", "doc_type": "cv"})

    def coursework(self, con, target: int, text: str = ESSAY) -> int:
        """The essay added as coursework of `target`; its source's id."""
        return pipeline.add_uploads(con, "coursework", [Incoming("essay.md", text.encode())], doc_kind="essay",
                                    target_id=target)["added"][0]

    def test_a_document_is_kept_and_read_into_a_note_under_documents_with_its_title_and_date(self):
        with home() as (con, vault, _):
            added = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])
            source_id = added["added"][0]
            self.assertTrue((store.files_dir(source_id) / "award.md").is_file())
            self.assertEqual(store.get(con, source_id)["state"], "new")
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
            note = (vault / got["note_rel"]).read_text(encoding="utf-8")
        self.assertEqual((out["state"], out["facts"]), ("read", 0))
        self.assertEqual((got["title"], got["dated"], got["revision"], got["state"]),
                         ("Volunteer of the year", "2025-03", 1, "read"))
        self.assertEqual(got["note_rel"], "Sources/Documents/Volunteer of the year.md")
        fm, body = frontmatter.parse(note)
        self.assertEqual((fm["role"], fm["category"], fm["doc_type"]), ("own", "Documents", "other"))
        self.assertIn("cataloguing letters", body)

    def test_the_same_file_in_another_section_is_one_source_and_the_person_is_told_where(self):
        with home() as (con, _, _):
            first = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])
            again = pipeline.add_uploads(con, "letter", [Incoming("copy of award.md", AWARD.encode())])
            n = len(store.listing(con))
        self.assertEqual(again["added"], [])
        self.assertEqual(again["duplicates"], [{"name": "copy of award.md", "id": first["added"][0],
                                                "section_label": "Documents"}])
        self.assertEqual(n, 1)

    def test_a_zip_is_one_source_of_several_files_without_the_junk(self):
        data = zip_of({"Tide gauge/README.md": "# Tide gauge\n\nReads a made-up bay's tides.\n",
                       "Tide gauge/src/main.py": "print('tides')\n", "__MACOSX/._README.md": "junk",
                       "Tide gauge/.env": "SECRET=1"})
        with home() as (con, _, _):
            out = pipeline.add_uploads(con, "document", [Incoming("tide-gauge.zip", data)])
            got = store.get(con, out["added"][0])
            rels = store.file_rels(con, got["id"])
        self.assertEqual((got["held"], got["files"], got["name"]), ("files", 2, "tide-gauge.zip"))
        self.assertEqual(rels, ["Tide gauge/README.md", "Tide gauge/src/main.py"])

    def test_a_chosen_folder_is_one_source_named_by_the_folder(self):
        with home() as (con, _, _):
            out = pipeline.add_uploads(con, "document", [
                Incoming("a.md", b"# Award one\n", "Awards/a.md"),
                Incoming("b.md", b"# Award two\n", "Awards/2025/b.md"),
                Incoming("loose.md", AWARD.encode())])
            got = [store.get(con, i) for i in out["added"]]
        self.assertEqual(sorted((g["held"], g["name"], g["files"]) for g in got),
                         [("files", "Awards", 2), ("upload", "loose.md", 1)])
        with home() as (con, _, _), self.assertRaises(ValueError):
            pipeline.add_uploads(con, "document", [Incoming("x.md", b"x", "../outside/x.md")])

    def test_coursework_belongs_to_an_education_card_and_nothing_else(self):
        with home() as (con, _, _):
            job = profile_store.add_found(con, "job", {"title": "Collections volunteer", "employer": "Lantern Trust"},
                                          {"source_rel": "Sources/CVs/Marnie CV.md", "doc_type": "cv"})
            with self.assertRaisesRegex(ValueError, "Choose which education"):
                pipeline.add_uploads(con, "coursework", [Incoming("essay.md", ESSAY.encode())])
            with self.assertRaisesRegex(ValueError, "belong to a education, not a job"):
                self.coursework(con, job)
            with self.assertRaisesRegex(ValueError, "Coursework takes essay"):
                pipeline.add_uploads(con, "coursework", [Incoming("essay.md", ESSAY.encode())], doc_kind="cv",
                                     target_id=self.education(con))

    def test_facts_carry_the_sections_role_rank_and_place_and_the_note_sits_under_the_card(self):
        with home() as (con, vault, _), reader("coursework", [skill("Archival research", "archival research", 3)]):
            degree = self.education(con)
            source_id = self.coursework(con, degree)
            out = pipeline.read(con, source_id)
            item = next(i for i in profile_store.items(con, "skill"))
            note_rel = store.get(con, source_id)["note_rel"]
            on_disk = (vault / note_rel).is_file()
        self.assertEqual(out["facts"], 1)
        self.assertEqual(note_rel, "Sources/Education/BA History/Coursework/The parish registers of Harrowgate "
                                   "Vale.md")
        self.assertTrue(on_disk)
        ev = item["evidence"][0]
        self.assertEqual((ev["role"], ev["doc_type"], ev["source_id"], ev["place"], ev["line"], ev["verified"]),
                         ("own", "essay", source_id, "essay.md", 3, True))

    def test_a_true_quote_is_found_whatever_its_dashes_spaces_and_emphasis(self):
        """True CV quotes were marked to be checked: the file said "**September 2023 \u2013 Present**" and a
        PDF's text "8%n ationally" (2026-11-11)."""
        text = "# Week 3\n\nCatalogue work, **September 2023 \u2013\nPresent**, roughly the top 8%n ationally.\n"
        quotes = ["Catalogue work, September 2023 - Present", "roughly the top 8% nationally", "not in the notes"]
        facts = [skill(f"Made-up skill {n}", q) for n, q in enumerate(quotes)]
        with home() as (con, _, _), reader("lecture", facts):
            source_id = pipeline.add_uploads(con, "lecture", [Incoming("week 3.md", text.encode())],
                                             target_id=self.education(con))["added"][0]
            pipeline.read(con, source_id)
            found = {i["fields"]["name"]: i["evidence"][0]["verified"] for i in profile_store.items(con, "skill")}
        self.assertEqual(found, {"Made-up skill 0": True, "Made-up skill 1": True, "Made-up skill 2": False})

    def test_lecture_material_gives_skills_as_studied_and_never_a_job(self):
        job = readers.Fact("job", {"title": "Lecturer", "employer": "University of Nowhere Fen"},
                           readers.Quote("title", "Lecturer"))
        with home() as (con, _, _), reader("lecture", [skill("Palaeography", "palaeography"), job]):
            source_id = pipeline.add_uploads(con, "lecture", [Incoming("week 3.md", b"# Week 3\n\npalaeography\n")],
                                             target_id=self.education(con))["added"][0]
            out = pipeline.read(con, source_id)
            skills = profile_store.items(con, "skill")
            jobs = profile_store.items(con, "job")
        self.assertEqual((out["facts"], out["left_out"]), (1, 1))
        self.assertTrue(profile_store.is_studied(skills[0]))
        self.assertEqual(jobs, [])

    def test_reading_again_replaces_the_sources_facts_and_hides_a_card_it_alone_named(self):
        with home() as (con, _, _), reader("coursework", [skill("Archival research", "archival research")]) as r:
            source_id = self.coursework(con, self.education(con))
            pipeline.read(con, source_id)
            first = profile_store.items(con, "skill")[0]["id"]
            r.facts = [skill("Parish registers", "parish registers")]
            out = pipeline.read(con, source_id)
            skills = {i["fields"]["name"]: i["state"] for i in profile_store.items(con, "skill")}
            got = store.get(con, source_id)
        self.assertEqual(out["hidden"], [first])
        self.assertEqual(skills, {"Archival research": "hidden", "Parish registers": "hal"})
        self.assertEqual(got["revision"], 2)

    def test_removing_a_source_takes_its_facts_and_note_and_adding_it_again_brings_the_card_back(self):
        with home() as (con, vault, _), reader("coursework", [skill("Archival research", "archival research")]):
            degree = self.education(con)
            source_id = self.coursework(con, degree)
            pipeline.read(con, source_id)
            card = profile_store.items(con, "skill")[0]["id"]
            profile_store.confirm(con, card)
            note = vault / store.get(con, source_id)["note_rel"]
            out = pipeline.remove(con, source_id)
            after = profile_store.item(con, card)
            gone = (not note.exists(), not store.files_dir(source_id).exists(), store.listing(con))
            again = self.coursework(con, degree)
            pipeline.read(con, again)
            back = profile_store.item(con, card)
        self.assertEqual(out["hidden"], [card])
        self.assertEqual((after["state"], after["evidence"]), ("hidden", []))
        self.assertEqual(gone, (True, True, []))
        self.assertEqual(back["state"], "confirmed", "the card comes back as the person left it")
        self.assertEqual(back["evidence"][0]["source_id"], again)

    def test_a_card_another_file_names_stays_shown_when_one_source_goes(self):
        with home() as (con, _, _), reader("coursework", [skill("Archival research", "archival research")]):
            source_id = self.coursework(con, self.education(con))
            pipeline.read(con, source_id)
            profile_store.add_found(con, "skill", {"name": "Archival research"},
                                    {"source_rel": "Sources/CVs/Marnie CV.md", "doc_type": "cv"})
            pipeline.remove(con, source_id)
            item = profile_store.items(con, "skill")[0]
        self.assertEqual((item["state"], [e["doc_type"] for e in item["evidence"]]), ("hal", ["cv"]))

    def test_a_reader_not_written_yet_leaves_the_source_waiting_and_says_which_phase(self):
        before = readers.register(readers.Stub("cv", readers.CV_PHASE))
        self.addCleanup(readers.register, before)
        with home() as (con, _, _):
            source_id = pipeline.add_uploads(con, "cv", [Incoming("Avery CV.md", CV.encode())])["added"][0]
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
        self.assertEqual((out["state"], got["state"], got["note_rel"]), ("new", "new", None))
        self.assertIn("phase 2", got["error"])

    def test_a_private_looking_document_is_kept_out_until_the_person_lets_it_in(self):
        with home() as (con, vault, _):
            source_id = pipeline.add_uploads(con, "document", [Incoming("form.md", PRIVATE.encode())])["added"][0]
            out = pipeline.read(con, source_id)
            kept = store.get(con, source_id)
            notes_there = list((vault / "Sources").rglob("*.md")) if (vault / "Sources").exists() else []
            pipeline.read(con, source_id, allow_private=True)
            let_in = store.get(con, source_id)
        self.assertEqual((out["state"], kept["error"], notes_there), ("kept_out", "bank details", []))
        self.assertEqual(let_in["state"], "read")

    def test_a_cv_added_to_documents_is_suggested_for_cvs_and_stays_where_it_was_put(self):
        with home() as (con, _, _):
            cv = [Incoming("Avery Quillon CV.md", CV.encode())]
            source_id = pipeline.add_uploads(con, "document", cv)["added"][0]
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
        self.assertEqual((out["suggestion"], got["suggestion"], got["suggestion_label"], got["section"]),
                         ("cv", "cv", "CVs", "document"))

    def test_a_tidy_keeps_the_notes_of_sources_added_to_sections(self):
        with home() as (con, vault, _):
            source_id = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])["added"][0]
            pipeline.read(con, source_id)
            note = vault / store.get(con, source_id)["note_rel"]
            self.assertIn(store.get(con, source_id)["note_rel"], ingest_tidy._pointed_at(con))
            engine.run(con, "t", tidy=True, step=lambda text: None, progress=lambda done, total, text: None)
            kept = note.exists()
        self.assertTrue(kept)

    def test_adding_a_source_makes_a_missing_record_folder(self):
        # regression: on a fresh install where setup was skipped, the record folder was never made, and the read
        # after an accepted sort failed with "no employment record at ..." (2026-11-09)
        with home() as (con, vault, _files):
            shutil.rmtree(vault)
            pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])
            self.assertTrue((vault / "Start Here.md").is_file(), "the record folder is made as setup makes it")
            record.sync()
            shutil.rmtree(vault)
            pipeline.add_link(con, "project", "Made-up harbour clean-up day", doc_kind="manual")
            self.assertTrue(vault.is_dir())

    def test_the_action_reads_what_was_added_and_says_so(self):
        with home() as (con, _, _):
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode()),
                                                         Incoming("essay.md", ESSAY.encode())])["added"]
            done = runner.run(actions.KIND, params={"ids": [*ids, 999]})
            states = [store.get(con, i)["state"] for i in ids]
        self.assertEqual(done["state"], "done")
        self.assertEqual((done["result"]["read"], done["result"]["missing"]), (2, 1))
        self.assertEqual(states, ["read", "read"])

    def test_the_action_tells_open_pages_the_profile_changed_once_it_has_read(self):
        """A starred piece of coursework became a project card only after its read, and the Projects tab, which
        follows `profile` events, kept showing the cards from before it (2026-11-09)."""
        with home() as (con, _, _):
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])["added"]
            sub = events.subscribe()
            self.addCleanup(sub.close)
            runner.run(actions.KIND, params={"ids": ids})
            seen = [e for e in iter(lambda: sub.get(timeout=0.2), None) if e.type == "profile"]
        self.assertEqual([e.data for e in seen], [{"sources": ids}])

    def test_a_source_the_ai_cannot_read_now_waits_and_is_never_read_without_it(self):
        """Every CV was read by rules into junk cards while Hal's model was stopped (2026-11-11)."""
        with home() as (con, _, _), reader("document", made=AiAway("document", fails=1)):
            (source_id,) = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])["added"]
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
        self.assertEqual((out["state"], out["kind"]), ("waiting_ai", "timeout"))
        self.assertEqual((got["state"], got["note_rel"]), ("new", None))
        self.assertIn("Waiting for Hal's AI", got["error"])

    def test_the_action_waits_for_the_ai_to_come_back_and_reads_the_source_then(self):
        with home() as (con, _, _), reader("document", made=AiAway("document", fails=2)) as away:
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])["added"]
            with mock.patch.object(actions, "AI_RETRY_S", 0):
                done = runner.run(actions.KIND, params={"ids": ids})
            state = store.get(con, ids[0])["state"]
        self.assertEqual((done["result"]["read"], done["result"]["waiting_ai"], state), (1, 0, "read"))
        self.assertEqual(away.calls, 3)

    def test_an_ai_that_is_not_set_up_ends_the_read_with_the_rest_still_waiting(self):
        with home() as (con, _, _), reader("document", made=AiAway("document", 99, "not_installed")) as away:
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode()),
                                                         Incoming("essay.md", ESSAY.encode())])["added"]
            done = runner.run(actions.KIND, params={"ids": ids})
            states = [store.get(con, i)["state"] for i in ids]
        self.assertEqual((done["result"]["read"], done["result"]["waiting_ai"]), (0, 2))
        self.assertEqual(away.calls, 1, "the second source is not tried once the AI is known to be missing")
        self.assertEqual(states, ["new", "new"])

    def test_a_read_carried_on_after_a_restart_reads_only_what_still_waits(self):
        with home() as (con, _, _):
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode()),
                                                         Incoming("essay.md", ESSAY.encode())])["added"]
            pipeline.read(con, ids[0])
            with reader("document", made=AiAway("document", fails=0)) as again:
                done = runner.run(actions.KIND, params={"ids": ids, "_carry_on_from": "an-earlier-read"})
        self.assertEqual((done["result"]["read"], again.calls), (1, 1))

    def test_the_action_reads_as_batch_work_so_it_gives_the_card_up(self):
        """Regression: the read held Hal's gateway as a service, which kept gpu-light's batch jobs off the card."""
        seen = []

        class Watching(AiAway):
            def read(self, source):
                seen.append(hal_gateway._batch.get())
                return super().read(source)

        with home() as (con, _, _), reader("document", made=Watching("document", fails=0)):
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode())])["added"]
            runner.run(actions.KIND, params={"ids": ids})
        self.assertEqual(seen, [True])

    def test_the_action_keeps_as_many_reads_with_the_ai_as_it_serves(self):
        """Hal read the record one file at a time while Hal's writer served two at once (2026-11-11)."""
        both = threading.Barrier(2, timeout=5)    # each read waits for the other: one at a time never passes

        class Together(AiAway):
            def read(self, source):
                both.wait()
                return super().read(source)

        with home() as (con, _, _), reader("document", made=Together("document", fails=0)), \
                mock.patch.object(ai, "at_once", return_value=2):
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode()),
                                                         Incoming("essay.md", ESSAY.encode())])["added"]
            done = runner.run(actions.KIND, params={"ids": ids})
            states = [store.get(con, i)["state"] for i in ids]
        self.assertEqual((done["result"]["read"], done["result"]["failed"]), (2, 0))
        self.assertEqual(states, ["read", "read"])

    def test_reads_with_the_ai_together_wait_for_it_once_and_are_all_read_after(self):
        with home() as (con, _, _), reader("document", made=AiAway("document", fails=2)) as away, \
                mock.patch.object(ai, "at_once", return_value=2), mock.patch.object(actions, "AI_RETRY_S", 0):
            ids = pipeline.add_uploads(con, "document", [Incoming("award.md", AWARD.encode()),
                                                         Incoming("essay.md", ESSAY.encode())])["added"]
            done = runner.run(actions.KIND, params={"ids": ids})
            states = [store.get(con, i)["state"] for i in ids]
        self.assertEqual((done["result"]["read"], done["result"]["waiting_ai"]), (2, 0))
        self.assertEqual((states, away.calls), (["read", "read"], 4))


if __name__ == "__main__":
    unittest.main()

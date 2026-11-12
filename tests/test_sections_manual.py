"""Projects added by hand: the form becomes a project card whose facts carry the person's own authority, and the
files attached add their words to the note.

The person is Marnie Quillfeather, a made-up history graduate; the exhibition and its files are invented.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_ingest import home

from jobhunt.profile import merge
from jobhunt.profile import store as profile_store
from jobhunt.sections import manual, pipeline, store
from jobhunt.sections.pipeline import Incoming

FORM = {"name": "Lantern letters exhibition", "what": "A made-up exhibition of 40 letters from the Fenwick archive.",
        "when": "Spring 2025", "part": "Curator", "tools": "Archival research, Omeka; label writing",
        "links": "https://example.org/lantern-letters"}
CV_REL = "Sources/CVs/Marnie CV.md"


def added(con, form: dict = FORM) -> int:
    """The project added by hand with `form`; its source's id."""
    source_id = pipeline.add_link(con, "project", form["name"], doc_kind="manual")["added"][0]
    store.change(con, source_id, {"form": form})
    return source_id


class Manual(unittest.TestCase):
    def test_the_form_becomes_a_project_card_with_its_skills(self):
        with home() as (con, _, _):
            pipeline.read(con, added(con))
            card = profile_store.items(con, "project", hidden=False)[0]
            skills = sorted(s["fields"]["name"] for s in profile_store.items(con, "skill", hidden=False))
        f = card["fields"]
        self.assertEqual((f["name"], f["role"], f["period"], f["link"]),
                         ("Lantern letters exhibition", "Curator", "Spring 2025", "https://example.org/lantern-letters"))
        self.assertEqual(f["tools"], ["Archival research", "Omeka", "label writing"])
        self.assertEqual(skills, ["Archival research", "Label writing", "Omeka"])
        self.assertTrue(all(e["verified"] and e["place"] == "typed by you" for e in card["evidence"]))

    def test_what_the_person_typed_outranks_their_cv(self):
        with home() as (con, _, _):
            profile_store.add_found(con, "project", {"name": "Lantern letters exhibition", "role": "Volunteer"},
                                    {"source_rel": CV_REL, "doc_type": "cv"})
            pipeline.read(con, added(con))
            card = profile_store.items(con, "project", hidden=False)[0]
        self.assertEqual(card["fields"]["role"], "Curator")
        self.assertGreater(merge.rank("by_you"), merge.rank("transcript"))

    def test_attached_files_add_their_words_to_the_note_but_no_facts(self):
        with home() as (con, vault, _):
            source_id = added(con)
            out = pipeline.attach(con, source_id, [Incoming("labels.md", b"# Labels\n\nLetter 12: a made-up note.\n"),
                                                   Incoming(".hidden", b"x")])
            pipeline.read(con, source_id)
            note = (vault / store.get(con, source_id)["note_rel"]).read_text()
            facts = con.execute("SELECT count(*) FROM profile_evidence WHERE source_id = ?", (source_id,)).fetchone()[0]
        self.assertEqual(out["added"], ["labels.md"])
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("Letter 12: a made-up note.", note)
        self.assertEqual(facts, 9)       # name, what, when, part, tools, link and three skills

    def test_a_form_with_no_name_or_a_link_that_is_not_a_web_address_is_refused(self):
        with self.assertRaises(ValueError):
            manual.clean_form({"name": " "})
        with self.assertRaises(ValueError):
            manual.clean_form({"name": "X", "links": "ftp://example.org/x"})
        with self.assertRaises(ValueError):
            manual.clean_form({"name": "X", "colour": "blue"})

    def test_only_a_project_added_by_hand_takes_a_form_or_attachments(self):
        with home() as (con, _, _):
            page = pipeline.add_link(con, "project", "https://example.org/a", doc_kind="web_page")["added"][0]
            with self.assertRaises(ValueError):
                store.change(con, page, {"form": FORM})
            with self.assertRaises(ValueError):
                pipeline.attach(con, page, [Incoming("a.md", b"a")])


if __name__ == "__main__":
    unittest.main()

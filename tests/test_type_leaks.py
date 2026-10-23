"""No recipe leaks: each made-up person's documents hold only their own kind of career.

The three made-up people of `support.people`, each with their CV type, have every document Hal writes made
through the fake AI: the advert read, the fit map, the letter (in a pack and at form time), the message, the
answers, the interview notes and the contact search. Every prompt sent, every reply and every file written
for one person must hold none of that person's LEAKS words (the other careers' words), no other person's
name and no pronoun for the applicant.
"""
from __future__ import annotations

import json
import re
import unittest
from unittest import mock

from support import people
from test_pack import FakeWriter, add_job, as_applicant

from jobhunt import contacts, documents, gpu, pack, prep
from jobhunt.ai import prompts
from jobhunt.cvstudio import types as cvtypes

PRONOUN = re.compile(r"\b(he|him|his)\b", re.I)
DOCUMENT_PROMPTS = ("analyse", "brief", "fit", "letter", "message", "answers", "verify", "prep", "contacts_pick")


def everything_written(person: people.Person) -> list[tuple[str, str]]:
    """(where, text) for every prompt, reply and file Hal makes for `person`'s documents."""
    writer = FakeWriter()
    with as_applicant(person, writer) as a, mock.patch.object(documents, "render", lambda job, docs: {}), \
            mock.patch.object(gpu, "busy", lambda: None):
        job = add_job(a.con, person.key, status="interview")
        pack_md = pack.build(a.con, job["id"], use_web=False)
        notes = prep.build(a.con, a.con.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone())
        found = [{"name": "Ada Penhallow", "role": "Head of the team", "source": "web search", "evidence": ""}]
        picked, _ = contacts.rank(job, found)
        queries = contacts.queries(job["company"], contacts.hiring_titles())
        form_letter = documents.draft_letter(a.con, job)
        pack_text = pack_md.read_text()
    out = [(f"{e['id']} prompt", e["system"]) for e in writer.log]
    out += [(f"{e['id']} request", e["user"]) for e in writer.log]
    out += [(f"{e['id']} reply", e["reply"]) for e in writer.log]
    out += [("pack.md", pack_text), ("prep notes", notes), ("contact search", "\n".join(queries)),
            ("contacts", json.dumps(picked)), ("form-time letter", form_letter)]
    return out


class NoLeaks(unittest.TestCase):
    def test_three_made_up_people_get_documents_with_no_recipe_leaks(self):
        for person in people.PEOPLE:
            others = [n for p in people.PEOPLE if p is not person for n in p.names]
            written = everything_written(person)
            self.assertTrue({"letter prompt", "message prompt", "prep prompt", "contacts_pick prompt",
                             "answers prompt", "fit prompt", "analyse prompt"} <= {w for w, _ in written})
            for where, text in written:
                with self.subTest(person=person.key, where=where):
                    lowered = text.lower()
                    for word in (*person.leaks, *others):
                        self.assertNotIn(word.lower(), lowered)
                    self.assertIsNone(PRONOUN.search(text))

    def test_every_document_prompt_reads_the_persons_own_cv_type(self):
        for person in people.PEOPLE:
            kind = cvtypes.load(person.key)
            with as_applicant(person):
                values = pack.person()
                texts = {pid: prompts.text(pid, **values) for pid in DOCUMENT_PROMPTS}
            with self.subTest(person=person.key):
                self.assertEqual(values["cv_type"], kind.name)
                self.assertIn(kind.documents["letter_mentions"], " ".join(texts["letter"].split()))
                self.assertIn(kind.documents["hiring_titles"][1], texts["message"])
                self.assertIn(kind.documents["prep_topics"][0], texts["prep"])
                self.assertIn(kind.documents["evidence"][0], texts["fit"])
                for pid, text in texts.items():
                    self.assertIsNone(prompts.PLACEHOLDER.search(text), f"{pid} left a placeholder unfilled")
                    for word in person.leaks:
                        self.assertNotIn(word.lower(), text.lower(), pid)

    def test_only_the_technical_type_reads_ktp_adverts_as_the_host_company(self):
        for person in people.PEOPLE:
            with as_applicant(person):
                special = prompts.text("analyse", **pack.person())
            with self.subTest(person=person.key):
                self.assertEqual("KTP" in special, person is people.TECHNICAL)

    def test_with_no_cv_type_the_documents_name_no_career(self):
        with as_applicant(people.HUMANITIES), mock.patch("jobhunt.cvstudio.context.cv_type", lambda: None):
            values = pack.person()
        self.assertEqual(values["cv_type"], "General")
        from_type = json.dumps([values[k] for k in ("evidence", "letter_mentions", "hiring_titles", "prep_topics",
                                                    "special_cases")]).lower()
        for person in people.PEOPLE:
            for word in person.leaks:
                self.assertNotIn(word.lower(), from_type)


if __name__ == "__main__":
    unittest.main()

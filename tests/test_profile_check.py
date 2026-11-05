"""The Check your record queue: what is asked first, how skills are grouped and ranked, and the progress count.

Each test opens a fresh database in a temporary folder. The person, her files, employers and modules are made
up (see support/people.py).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import db
from jobhunt.profile import check, store

NOW = "2026-11-05T09:00:00Z"
CV = "Sources/CVs and letters/Alex Doe CV.md"
LETTER = "Sources/CVs and letters/Museum letter.md"
MODULE = "Sources/Study notes/Archive methods week 3.md"
PROJECT = "Sources/Projects/Mill town letters.md"


def ev(source_rel: str, doc_type: str, quote: str = "", role: str = "") -> dict:
    """Evidence as the ingest gives it; `role` is added to the stored row the way the ingest marks it."""
    return {"source_rel": source_rel, "quote": quote, "field": "", "doc_type": doc_type, "_role": role}


class Queue(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect(folder / "jobs.db")
        self.addCleanup(self.con.close)
        self.roles: dict[tuple[int, str], str] = {}
        real = store.evidence_of

        def with_roles(con, item_ids):
            """The store's evidence with the made-up roles, as the ingest's role marks will give them."""
            out = real(con, item_ids)
            for item_id, found in out.items():
                for e in found:
                    e["role"] = self.roles.get((item_id, e["source_rel"]), "")
            return out
        self.enterContext(mock.patch.object(store, "evidence_of", with_roles))

    def found(self, kind: str, fields: dict, evidence: dict) -> int:
        role = evidence.pop("_role")
        item_id = store.add_found(self.con, kind, fields, evidence)
        if role:
            self.roles[(item_id, evidence["source_rel"])] = role
        return item_id

    def skill(self, name: str, *sources: tuple[str, str, str]) -> int:
        """A skill named in each of `sources`, given as (file, doc type, role)."""
        ids = {self.found("skill", {"name": name}, ev(rel, doc_type, name.lower(), role))
               for rel, doc_type, role in sources}
        (item_id,) = ids
        return item_id

    def test_basics_come_first_in_their_order_then_jobs_education_and_the_rest(self):
        link = self.found("link", {"url": "https://example.org/alex"}, ev(CV, "cv"))
        job = self.found("job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"},
                         ev(CV, "cv"))
        place = self.found("basic", {"key": "location", "value": "Harrowgate"}, ev(CV, "cv"))
        name = self.found("basic", {"key": "name", "value": "Alex Doe"}, ev(CV, "cv"))
        project = self.found("project", {"name": "Oral history of the mills"}, ev(PROJECT, "notes"))
        cert = self.found("certificate", {"name": "Emergency first aid at work"}, ev(CV, "cv"))
        degree = self.found("education", {"qualification": "BA History", "institution": "Wexley University",
                                          "start": "2021"}, ev(CV, "cv"))
        prize = self.found("achievement", {"title": "Dean's list"}, ev(CV, "cv"))
        order = [c["items"][0]["id"] for c in check.queue(self.con)["cards"]]
        self.assertEqual(order, [name, place, job, degree, cert, prize, project, link])

    def test_open_conflicts_are_asked_before_any_fact(self):
        job = {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"}
        self.found("job", {**job, "start": "Sept 2023"}, ev(CV, "cv"))
        self.found("job", {**job, "start": "Oct 2023"}, ev(LETTER, "letter"))
        found = check.queue(self.con)
        self.assertEqual([c["field"] for c in found["conflicts"]], ["start"])

    def test_skills_are_grouped_by_the_file_they_rest_on_most(self):
        a = self.skill("Archival research", (MODULE, "notes", ""), (CV, "cv", ""))
        b = self.skill("Oral history interviewing", (PROJECT, "notes", "own"))
        c = self.skill("Transcription", (PROJECT, "notes", "own"), (MODULE, "notes", ""))
        groups = [(g["source_rel"], [i["id"] for i in g["items"]]) for g in check.queue(self.con)["cards"]]
        self.assertEqual(groups, [(PROJECT, [c, b]), (CV, [a])])

    def test_weak_skills_and_those_past_the_first_wait_under_show_more(self):
        weak = self.skill("Palaeography", (MODULE, "notes", "taught"))
        strong = [self.skill(f"Made-up method {n:02d}", (PROJECT, "notes", "own"))
                  for n in range(check.SKILLS_FIRST + 2)]
        found = check.queue(self.con)
        asked = [i["id"] for c in found["cards"] for i in c["items"]]
        later = [i["id"] for c in found["more"] for i in c["items"]]
        self.assertEqual(len(asked), check.SKILLS_FIRST)
        self.assertEqual(sorted(asked + later), sorted([weak, *strong]))
        self.assertEqual(later[-1], weak, "the weak skill comes last")
        self.assertEqual(len({c["key"] for c in found["cards"] + found["more"]}), len(found["cards"] + found["more"]))
        self.assertEqual(found["total"], check.SKILLS_FIRST, "what waits under Show more is not counted")

    def test_skills_named_by_more_files_and_the_persons_own_come_first(self):
        one = self.skill("Cataloguing", (CV, "cv", ""), (LETTER, "letter", ""))
        own = self.skill("Exhibition design", (PROJECT, "notes", "own"))
        three = self.skill("Archival research", (CV, "cv", ""), (LETTER, "letter", ""), (MODULE, "notes", ""))
        ranked = sorted([store.item(self.con, i) for i in (one, own, three)], key=check.strength, reverse=True)
        self.assertEqual([i["id"] for i in ranked], [own, three, one])

    def test_stopping_half_way_and_coming_back_starts_where_the_person_stopped(self):
        name = self.found("basic", {"key": "name", "value": "Alex Doe"}, ev(CV, "cv"))
        job = self.found("job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"},
                         ev(CV, "cv"))
        degree = self.found("education", {"qualification": "BA History", "institution": "Wexley University",
                                          "start": "2021"}, ev(CV, "cv"))
        store.confirm(self.con, name)
        store.hide(self.con, job)
        found = check.queue(self.con)
        self.assertEqual([c["items"][0]["id"] for c in found["cards"]], [degree])
        self.assertEqual((found["done"], found["total"]), (2, 3))
        store.edit(self.con, degree, {"grade": "2:1"})
        found = check.queue(self.con)
        self.assertEqual((found["cards"], found["done"], found["total"]), ([], 3, 3))

    def test_each_card_names_the_source_whose_quote_is_shown(self):
        job = {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"}
        self.found("job", job, ev(LETTER, "letter", "volunteering at the museum"))
        item_id = self.found("job", job, ev(CV, "cv", "Collections volunteer"))
        (card,) = check.queue(self.con)["cards"]
        best = [e for e in store.item(self.con, item_id)["evidence"] if e["id"] == card["best"][0]]
        self.assertEqual(best[0]["source_rel"], CV, "a CV is trusted over a letter")


if __name__ == "__main__":
    unittest.main()

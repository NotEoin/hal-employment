"""Who to message about an application: the titles come from the CV type, never one career's.

The search engine and the AI are fakes: the search returns made-up profile results, and the fake writer of
`test_pack` picks from the list. The route test serves the made-up world from a real server.
"""
from __future__ import annotations

import contextlib
import time
import unittest
from unittest import mock
from urllib.parse import quote

from support import people
from test_pack import POLL_S, WAIT_S, add_job, as_applicant, section

from jobhunt import browser, contacts, documents, pack
from jobhunt.core import runner
from jobhunt.cvstudio import types as cvtypes


def profile(name: str, role: str, company: str) -> dict:
    """One made-up search result for a profile at `company`."""
    slug = name.lower().replace(" ", "-")
    return {"url": f"https://www.linkedin.com/in/{slug}", "title": f"{name} - {role} - LinkedIn",
            "snippet": f"{role} at {company}. A made-up profile."}


class HiringTitles(unittest.TestCase):
    def test_the_hiring_manager_message_goes_to_a_title_the_type_names(self):
        for person in people.PEOPLE:
            titles = cvtypes.load(person.key).documents["hiring_titles"]
            with self.subTest(person=person.key), as_applicant(person) as a:
                job = add_job(a.con, person.key)
                md = pack.build(a.con, job["id"], use_web=False).read_text()
                queries = contacts.queries(job["company"], contacts.hiring_titles())
            to = section(md, "Message to the hiring manager").strip().splitlines()[0]
            self.assertTrue(to.startswith("To: find the person hiring"), to)
            self.assertIn(f"{job['company']} {titles[0]}", to)
            self.assertIn(", ".join(titles[:-1]), a.writer.asked("message")[0]["system"])
            self.assertIn(f'"{titles[1]}"', queries[0])
            for word in person.leaks:
                self.assertNotIn(word.lower(), " ".join(queries).lower())

    def test_a_person_the_advert_names_is_messaged_first(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            analysis = {"contacts": [{"name": "Ada Penhallow", "role": "Head of Collections"}]}
            self.assertEqual(pack.message_to(job, analysis, pack.person(job)), "Ada Penhallow, Head of Collections")

    def test_the_rules_rank_the_types_managers_above_other_careers_managers(self):
        company = "Riverside Heritage Trust"
        found = [contacts._profile(profile(n, r, company), company) for n, r in (
            ("Bram Oakhill", "Engineering Manager"), ("Ada Penhallow", "Head of Research"),
            ("Cleo Marsh", "Talent Partner"))]
        with as_applicant(people.HUMANITIES):
            ranked = contacts._by_rules(found)
        self.assertEqual([p["name"] for p in ranked], ["Ada Penhallow", "Cleo Marsh"])
        with as_applicant(people.TECHNICAL):
            ranked = contacts._by_rules(found)
        self.assertEqual([p["name"] for p in ranked], ["Bram Oakhill", "Cleo Marsh"])

    def test_the_old_find_line_is_never_taken_for_a_person(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad")
            for line in ('find the person hiring on LinkedIn (search "Riverside Heritage Trust hiring manager")',
                         "find the engineering lead on LinkedIn"):
                with mock.patch.object(documents, "drafts", lambda job, line=line: {"message_to": line}):
                    self.assertEqual(contacts.from_advert(job), [])


class Route(unittest.TestCase):
    def test_find_who_to_message_searches_for_the_types_titles_and_stores_the_people(self):
        from support.api import HalServer

        searched = []

        def search(queries, pause):
            searched.extend(queries)
            return [[profile("Ada Penhallow", "Head of Research", "Riverside Heritage Trust")]], False

        with contextlib.ExitStack() as stack:
            hal = stack.enter_context(HalServer())
            a = stack.enter_context(as_applicant(people.HUMANITIES))
            stack.enter_context(mock.patch.object(browser, "search", search))
            stack.enter_context(mock.patch.object(contacts, "SEARCH_PAUSE", 0))
            job = add_job(a.con, "humanities_grad", status="applied")
            c = hal.signed_in()
            r = c.post(f"/api/v2/applications/{quote(job['id'], safe='')}/contacts")
            self.assertEqual(r.status, 200, r.text)
            end = time.monotonic() + WAIT_S
            while time.monotonic() < end:
                runner.flush()
                row = a.con.execute("SELECT state FROM actions WHERE id = ?", (r.json()["action_id"],)).fetchone()
                if row is not None and row["state"] not in ("queued", "running"):
                    break
                time.sleep(POLL_S)
            self.assertEqual(row["state"], "done")
            stored = a.con.execute("SELECT contacts FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
        self.assertIn('"head of research"', searched[0])
        self.assertIn("Ada Penhallow", stored["contacts"])


if __name__ == "__main__":
    unittest.main()

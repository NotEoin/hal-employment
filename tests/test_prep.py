"""Notes for the next stage: the CV type's topics and the tests the advert names, no one career's.

The fake writer of `test_pack` answers the prep prompt; the route test serves the made-up world from a
real server.
"""
from __future__ import annotations

import unittest

from support import people
from test_pack import add_job, as_applicant

from jobhunt import pack, prep


class Notes(unittest.TestCase):
    def test_prep_asks_about_the_types_topics_and_the_tests_the_advert_names(self):
        with as_applicant(people.HUMANITIES) as a:
            job = add_job(a.con, "humanities_grad", status="interview")
            pack.build(a.con, job["id"], use_web=False)
            notes = prep.build(a.con, job)
            stored = a.con.execute("SELECT prep FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
        asked = a.writer.asked("prep")[0]
        self.assertIn("why this sector, a written exercise and competency questions", asked["system"])
        self.assertIn("Prepare Marnie, a new graduate", asked["system"])
        self.assertIn("Tests the advert names: written exercise and assessment centre", asked["user"])
        self.assertIn("Stage: interview", asked["user"])
        self.assertNotIn("coding practice", asked["system"] + asked["user"])
        self.assertEqual(stored["prep"], notes)

    def test_an_advert_that_names_no_test_says_so(self):
        with as_applicant(people.CHANGER) as a:
            job = add_job(a.con, "career_changer")
            a.con.execute("UPDATE jobs SET description = 'Keep projects on time.' WHERE id = ?", (job["id"],))
            a.con.commit()
            row = a.con.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            text = prep.request(row, pack.person(row))
        self.assertIn("Tests the advert names: none named in the advert", text)


if __name__ == "__main__":
    unittest.main()

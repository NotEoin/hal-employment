"""Tests of salary expectations: figures read from adverts, market medians weighed, a band chosen.

Nothing here reads the web or the real job database: ITJobsWatch is replaced by fixed
figures and the adverts come from a temporary database.

Run with: .venv/bin/python -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import db, salary

PAGE = """<table><tr><td>Number of salaries quoted</td><td>{quotes}</td><td>9</td></tr>
<tr><td>Median annual salary (50<sup>th</sup> Percentile)</td><td>&pound;{median}</td><td>&pound;42,750</td></tr>
<tr><td>UK excluding London median annual salary</td><td>&pound;29,000</td><td>&pound;32,000</td></tr></table>"""


def job(title="Junior Data Engineer", location="London", pay=None, source="greenhouse", n=0, description=""):
    return {"id": f"{source}:{n}", "source": source, "company": f"Firm {n}", "title": title, "location": location,
            "url": f"https://example.org/{n}", "description": description, "salary": pay, "posted_at": None,
            "board_size": None}


class Salary(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(self.con.close)
        saved = salary.itjobswatch, salary.adverts
        self.addCleanup(setattr, salary, "itjobswatch", saved[0])
        self.addCleanup(setattr, salary, "adverts", saved[1])
        self.market = {}
        salary.itjobswatch = lambda phrase, london: self.market.get(phrase)
        salary.adverts = saved[1]

    def add(self, *jobs):
        for j in jobs:
            db.upsert(self.con, j)
        self.con.commit()

    def test_annual_figures_are_read_and_day_rates_and_other_currencies_are_not(self):
        self.assertEqual(salary.figures("GBP 40,000-60,000"), [40000, 60000])
        self.assertEqual(salary.figures("£30-35k plus bonus"), [30000, 35000])
        self.assertEqual(salary.figures("£28,000 to £32,000 per annum"), [28000, 32000])
        self.assertEqual(salary.figures("GBP 385-395") + salary.figures("$80K – $120K") + salary.figures("£450 per day"), [])

    def test_an_adzuna_estimate_is_not_an_advertised_salary(self):
        """Adzuna writes its prediction as one exact figure; no employer advertises £53,622."""
        self.assertIsNone(salary.advertised(job(pay="GBP 53,622-53,622", source="adzuna")))
        self.assertEqual(salary.advertised(job(pay="GBP 35,000-35,000", source="adzuna")), (35000, 35000))
        self.assertEqual(salary.advertised(job(description="Salary: £32k - £36k depending on experience")), (32000, 36000))

    def test_the_six_month_median_is_read_from_an_itjobswatch_page(self):
        page = PAGE.format(quotes=12, median="50,000")
        self.assertEqual(salary.parse_itjobswatch(page, london=True), (50000, 12))
        self.assertEqual(salary.parse_itjobswatch(page, london=False), (29000, 12))
        self.assertIsNone(salary.parse_itjobswatch("<p>No matching jobs</p>", london=True))

    def test_the_answer_stays_inside_an_advertised_range(self):
        self.market["junior data engineer"] = (47500, 30)
        out = salary.expect(job(pay="£30,000 - £40,000"), self.con)
        self.assertEqual((out["figure"], out["text"]), (40000, "£38,000 to £40,000"))
        self.assertTrue(out["basis"].startswith("the advert states £30,000 to £40,000"))

    def test_a_thin_market_for_the_role_falls_back_to_junior_software_roles(self):
        """Four salaries for junior data engineers are not enough; graduate software roles stand in."""
        self.market.update({"junior data engineer": (61000, 4), "graduate software engineer": (46000, 40)})
        out = salary.expect(job(), self.con)
        self.assertEqual((out["figure"], out["text"]), (46000, "£44,000 to £48,000"))
        self.assertIn("too few salaries for junior data engineer roles", out["basis"])
        self.assertNotIn("61,000", out["basis"])

    def test_comparable_adverts_count_only_junior_roles_in_the_same_region(self):
        self.add(*[job(n=i, pay=f"£{34 + i % 3},000") for i in range(1, 11)],
                 job("Senior Data Engineer", n=20, pay="£80,000"), job(n=21, location="Leeds", pay="£25,000"),
                 job(n=22, pay="GBP 53,622-53,622", source="adzuna"), job(n=23, pay="£25,000 - £78,000"))
        mids = salary.adverts(self.con, job(n=99), "data engineer")
        self.assertEqual(sorted(mids), sorted(34000 + 1000 * (i % 3) for i in range(1, 11)))
        out = salary.expect(job(n=99), self.con)
        self.assertEqual(out["figure"], 35000)
        self.assertIn("10 similar junior data engineer adverts", out["basis"])

    def test_with_no_data_the_floor_is_given_and_flagged(self):
        out = salary.expect(job(title="Graduate Developer", location="City"), self.con)
        self.assertEqual(out["figure"], salary.SALARY_FLOOR)
        self.assertIn("check this", out["basis"])

    def test_a_salary_list_gets_the_band_that_holds_the_figure(self):
        options = ["Up to £25,000", "£25,000 - £34,999", "£35,000 - £44,999", "£45,000+"]
        self.assertEqual(salary.band(options, 38000), "£35,000 - £44,999")
        self.assertEqual(salary.band(options, 52000), "£45,000+")
        self.assertEqual(salary.band(options, 20000), "Up to £25,000")
        self.assertIsNone(salary.band(["Yes", "No"], 38000))


if __name__ == "__main__":
    unittest.main()

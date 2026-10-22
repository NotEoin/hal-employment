"""Pay in adverts, compared per year. Every advert here is made up."""

import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.jobs import advert

from jobhunt.find import goal as goal_mod
from jobhunt.find import pay, rules


class Pay(unittest.TestCase):
    """Annual, monthly, weekly, daily and hourly pay all become pounds a year."""

    def test_hourly_internship_pay_is_compared_per_year(self):
        found = pay.parse("\u00a312.50 per hour", "")
        self.assertEqual((found.min, found.max, found.per, found.paid), (24375, 24375, "hour", 1))
        values = {"goal.kinds": ["internship", "graduate_job"], "goal.min_pay": 24000}
        job = advert("Summer Internship", "Paid \u00a312.50 an hour for ten weeks. Recent graduates welcome.")
        scored = rules.score(job, goal_mod.from_values(values), rules.weights_from("humanities_grad"))
        self.assertNotIn("pay_under", [r.rule for r in scored.reasons])
        lower = rules.score(job, goal_mod.from_values({**values, "goal.min_pay": 26000}),
                            rules.weights_from("humanities_grad"))
        self.assertIn("pay_under", [r.rule for r in lower.reasons])

    def test_each_period_is_turned_into_a_year(self):
        self.assertEqual(pay.parse("\u00a32,000 per month", "").max, 24000)
        self.assertEqual(pay.parse("\u00a3450 a week", "").max, 23400)
        self.assertEqual(pay.parse("\u00a3120 per day", "").max, 31200)
        self.assertEqual(pay.parse("GBP 26,000 - 28,000 per annum", ""), pay.Pay(26000, 28000, "year", 1,
                                                                                  "GBP 26,000 - 28,000"))
        self.assertEqual(pay.parse("\u00a330-35k", "").max, 35000)

    def test_unpaid_and_unstated_pay_are_told_apart(self):
        self.assertEqual(pay.parse(None, "This is a voluntary role with expenses only.").paid, 0)
        self.assertIsNone(pay.parse(None, "Competitive salary.").paid)


if __name__ == "__main__":
    unittest.main()

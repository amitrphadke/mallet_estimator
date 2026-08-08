# Pure unit tests for repair estimation — no database, no frappe. Run with
#   python -m unittest mallet_estimator.tests.test_repair
import types
import unittest

from mallet_estimator import estimator as E
from mallet_estimator import repair_csv as R


def _settings(**over):
    """Wage rates and the two repair policy numbers. The values here are test
    fixtures, not the shop's — every real rate lives only in the site DB."""
    base = dict(
        carpenter_salary=0, helper_salary=0, designer_salary=0, bonus_months=0,
        working_days_per_month=26, paid_holidays_per_month=0,
        national_holidays_per_year=0, working_hours_per_day=8, lunch_hours_per_day=0,
        carpenter_rate=120, helper_rate=60, design_rate=0,
        markup_repair=50, repair_visit_charge=2000,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def _act(**over):
    base = dict(qty=1, carpenters=1, carp_min=60, helpers=0, helper_min=0, status="Quoted")
    base.update(over)
    return base


class TestRepairRowMinutes(unittest.TestCase):
    def test_quantity_multiplies_the_crew_minutes(self):
        # six chairs at 30 min each with one carpenter and one helper
        c, h = E.repair_row_minutes(_act(qty=6, carp_min=30, helpers=1, helper_min=30))
        self.assertEqual((c, h), (180, 180))

    def test_missing_quantity_counts_as_one(self):
        c, _h = E.repair_row_minutes(_act(qty=0, carp_min=45))
        self.assertEqual(c, 45)

    def test_minutes_without_a_crew_cost_nothing(self):
        # the sheet's quiet failure: minutes typed against a head-count of 0
        _c, h = E.repair_row_minutes(_act(qty=6, helpers=0, helper_min=30))
        self.assertEqual(h, 0)


class TestRepairCosting(unittest.TestCase):
    def test_visit_charge_is_a_floor_not_an_addition(self):
        # half a day of work: labour is small, so the day rate binds
        r = E.calc_repair([_act(qty=6, carp_min=30, helpers=1, helper_min=30)], _settings())
        self.assertEqual(r["carp_min"], 180)
        self.assertAlmostEqual(r["est_days"], 0.5)
        self.assertEqual(r["visits"], 1)
        self.assertAlmostEqual(r["labor_cost"], 180 / 60 * 120 + 180 / 60 * 60)
        self.assertAlmostEqual(r["client_labor"], 540 * 1.5)
        self.assertAlmostEqual(r["visit_amount"], 2000)
        self.assertAlmostEqual(r["client_repair"], 2000)
        self.assertAlmostEqual(r["visit_topup"], 2000 - 810)

    def test_a_full_day_stops_the_floor_binding(self):
        # once labour alone clears the day rate, the floor is irrelevant
        r = E.calc_repair([_act(qty=1, carp_min=1800, helpers=1, helper_min=1800)],
                          _settings(carpenter_rate=400, helper_rate=200))
        self.assertAlmostEqual(r["est_days"], 5.0)
        self.assertEqual(r["visits"], 5)
        self.assertGreater(r["client_labor"], r["visit_amount"])
        self.assertAlmostEqual(r["client_repair"], r["client_labor"])
        self.assertEqual(r["visit_topup"], 0)

    def test_a_low_wage_rate_makes_the_floor_bind_all_day(self):
        """Worth knowing rather than discovering: the floor only 'stops
        applying after a full day' when a day of wages plus margin is worth
        MORE than the day rate. Keep the visit charge below that and it does
        what it is meant to — protect short visits only."""
        cheap = _settings(carpenter_rate=120, helper_rate=60)   # a day = 1080 cost
        r = E.calc_repair([_act(qty=1, carp_min=360, helpers=1, helper_min=360)], cheap)
        self.assertAlmostEqual(r["est_days"], 1.0)
        self.assertAlmostEqual(r["client_labor"], 1080 * 1.5)   # 1620
        self.assertAlmostEqual(r["client_repair"], 2000)        # the floor still wins
        self.assertGreater(r["visit_topup"], 0)

    def test_to_inspect_rows_are_held_out_of_the_firm_total(self):
        rows = [_act(carp_min=60), _act(carp_min=600, status="To Inspect")]
        r = E.calc_repair(rows, _settings())
        self.assertEqual(r["carp_min"], 60)          # only the quoted row
        self.assertEqual(r["to_inspect"], 1)
        self.assertEqual(r["to_inspect_carp_min"], 600)  # still visible as scope

    def test_visits_can_be_overridden_for_a_split_job(self):
        # two half-days a week apart is two visits, not one
        rows = [_act(qty=6, carp_min=30, helpers=1, helper_min=30)]
        r = E.calc_repair(rows, _settings(), visits=2)
        self.assertEqual(r["visits"], 2)
        self.assertEqual(r["derived_visits"], 1)
        self.assertAlmostEqual(r["visit_amount"], 4000)

    def test_margin_defaults_to_policy_and_can_be_overridden(self):
        rows = [_act(carp_min=600)]
        policy = E.calc_repair(rows, _settings())
        custom = E.calc_repair(rows, _settings(), markup_pct=0)
        self.assertAlmostEqual(policy["client_labor"], custom["client_labor"] * 1.5)

    def test_no_activities_means_no_visit_charge(self):
        r = E.calc_repair([], _settings())
        self.assertEqual(r["visits"], 0)
        self.assertEqual(r["client_repair"], 0)


SHEET = """Customer Name,Someone,,Quote Number,MCFT-QT-FY-1234,,,,,,,,,,,,
Designed By,A P,,SKU Name,Home Repair,,,,,,,,,,,,
,,,,,,,,,,,,,,,,
S.No.,Room Name,SKU,Activity,Description,Material Description,Item,UOM,Quantity,\
Carpenters (No.),Carpenters (Mins),Helpers (No.),Helpers (Mins),\
Row Total (Carp. Mins),Row Total (Helper Mins),Workstation,Remarks
1,Kitchen,Dining Chair,Dining chairs to repair,"Fix joints with Araldite",Araldite - 1 tube,,Nos,6,1,30,1,30,180,180,On-Site,
2,Kids Bedroom,,Wardrobe Repair,Need to inspect it first to come up with estimate.,TBD,,,0,1,30,0,0,0,0,,
3,Kitchen,Cabinet Door,Hydraulic Lift replace,Swap the lifts,Hydraulic lift,,Nos,6,1,20,0,30,120,0,On-Site,
,,,,,,,,TOTALS,,,,,300,180,,
,,,,,,,,,,,,,,,,
Column 1,Column 2,Column 11,Column 3,Column 4,Column 5,Column 10,Column 9,Column 6,Column 7,Column 8,,,,,,
1,Plywood,,18 mm 8 x 4,3,Sheet,,,0,0,,,,,,,
"""


class TestRepairSheet(unittest.TestCase):
    def test_reads_the_activity_block(self):
        acts, _w = R.parse_repair_csv(SHEET)
        self.assertEqual([a["activity"] for a in acts],
                         ["Dining chairs to repair", "Wardrobe Repair", "Hydraulic Lift replace"])
        self.assertEqual(acts[0]["room"], "Kitchen")
        self.assertEqual(acts[0]["target"], "Dining Chair")
        self.assertEqual(acts[0]["qty"], 6)
        self.assertEqual(acts[0]["carp_min"], 30)

    def test_header_is_found_not_assumed(self):
        # the title block above the header changes height from job to job
        self.assertEqual(R.find_header(list(__import__("csv").reader(SHEET.splitlines()))), 3)

    def test_stops_at_totals_and_says_what_it_left(self):
        acts, warnings = R.parse_repair_csv(SHEET)
        self.assertNotIn("Plywood", [a["activity"] for a in acts])
        self.assertTrue(any("new-work material" in w for w in warnings))

    def test_unpriceable_rows_are_flagged_not_dropped(self):
        acts, warnings = R.parse_repair_csv(SHEET)
        held = [a for a in acts if a["status"] == "To Inspect"]
        self.assertEqual([a["activity"] for a in held], ["Wardrobe Repair"])
        self.assertTrue(any("site inspection" in w for w in warnings))

    def test_minutes_with_no_crew_are_reported(self):
        _acts, warnings = R.parse_repair_csv(SHEET)
        self.assertTrue(any("no helper is on the row" in w for w in warnings),
                        f"expected a zero-crew warning, got {warnings}")

    def test_sheet_and_engine_agree_on_the_quoted_effort(self):
        acts, _w = R.parse_repair_csv(SHEET)
        r = E.calc_repair(acts, _settings())
        # 6x30 chairs + 6x20 lifts = 300 carpenter min; the wardrobe row is held
        self.assertEqual(r["carp_min"], 300)
        # 6x30 helper on the chairs only — the lift row has no helper on it,
        # which is why the sheet's own 180 helper total is the honest number
        self.assertEqual(r["helper_min"], 180)
        self.assertEqual(r["to_inspect"], 1)

    def test_a_sheet_that_is_not_the_sheet(self):
        acts, warnings = R.parse_repair_csv("a,b,c\n1,2,3\n")
        self.assertEqual(acts, [])
        self.assertTrue(warnings)



class TestRepairPrintLeakSafety(unittest.TestCase):
    """The client copy sells an outcome; the shop copy plans a day. Effort per
    activity belongs only on the second one.

    The payload already withholds those keys unless kind == 'execution', so
    this is the belt to that braces: if someone later adds the fields to the
    client payload, the template must still not be rendering them."""

    def _template(self, name):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "templates", "print", name)) as fh:
            return fh.read()

    def test_client_copy_renders_no_effort_or_rates(self):
        html = self._template("mallet_client_estimate.html")
        repair = html[html.index("Repair Work"):]
        for token in ("carp_min", "helper_min", "carpenters", "a.qty", "a.remarks"):
            self.assertNotIn(token, repair,
                             f"client repair section must not render {token}")

    def test_client_copy_prints_scope_and_a_lump_sum(self):
        html = self._template("mallet_client_estimate.html")
        for token in ("job.scope", "job.materials", "job.price", "p.repair.to_inspect"):
            self.assertIn(token, html, f"client repair section is missing {token}")

    def test_shop_copy_carries_the_effort(self):
        html = self._template("mallet_execution_estimate.html")
        for token in ("a.carp_min", "a.helper_min", "a.carpenters", "job.visits"):
            self.assertIn(token, html, f"execution repair section is missing {token}")

    def test_both_copies_survive_an_estimate_with_no_new_work(self):
        # a pure-repair estimate must not print an empty room table
        for name in ("mallet_client_estimate.html", "mallet_execution_estimate.html"):
            self.assertIn("{% if p.rooms %}", self._template(name),
                          f"{name} renders the article table unconditionally")


if __name__ == "__main__":
    unittest.main()

# Pure unit tests for the cost engine — no database, no frappe. Run locally with
#   python -m unittest mallet_estimator.tests.test_estimator
# and in CI under `bench run-tests --app mallet_estimator`.
import types
import unittest

from mallet_estimator import estimator as E


def _settings(**over):
    base = dict(
        monthly_rent=60000, working_days_per_month=26, working_hours_per_day=8,
        carpenter_rate=157, helper_rate=107, design_rate=500, design_flat=0,
        markup_material=15, markup_labor=20, markup_overhead=20, markup_design=20,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


class TestWorkstationRates(unittest.TestCase):
    def test_modular_components(self):
        # OPS3: Rent = pure space rent, Depreciation its own component, one Wage
        # component per crew member; zero-value components skipped per station.
        rates = {w["name"]: w for w in E.workstation_rates(_settings())}
        comps = [c for c, _ in rates["Panel Saw"]["components"]]
        self.assertEqual(comps, ["Rent", "Depreciation", "Carpenter Wage", "Helper Wage",
                                 "Electricity", "Consumables"])
        # D1: the Design Desk is crewed by the designer only.
        dcomps = [c for c, _ in rates["Design Desk"]["components"]]
        self.assertIn("Designer Wage", dcomps)
        self.assertNotIn("Carpenter Wage", dcomps)

    def test_salary_calendar_rates(self):
        # L1: ###/mo carpenter + 1-month Diwali bonus over ~162 productive hrs
        # (26 − 2 paid − 10/12 natl days x 7 hrs) ≈ ₹174.5/hr.
        s = _settings(carpenter_salary=###, helper_salary=###, bonus_months=1,
                      paid_holidays_per_month=2, national_holidays_per_year=10,
                      lunch_hours_per_day=1)
        self.assertAlmostEqual(E.working_days_per_month(s), 26 - 2 - 10 / 12.0, places=4)
        self.assertAlmostEqual(E.working_hours_per_month(s), (26 - 2 - 10 / 12.0) * 7, places=4)
        r = E.staff_rates(s)
        self.assertAlmostEqual(r["carpenter"], ### * 13 / 12.0 / ((26 - 2 - 10 / 12.0) * 7), places=4)
        self.assertAlmostEqual(r["helper"], ### * 13 / 12.0 / ((26 - 2 - 10 / 12.0) * 7), places=4)

    def test_legacy_hourly_fallback(self):
        # No salaries keyed -> the old hourly fields still price (back-compat).
        r = E.staff_rates(_settings())
        self.assertEqual(r["carpenter"], 157)
        self.assertEqual(r["helper"], 107)

    def test_wages_is_two_person_crew(self):
        r = {w["name"]: w for w in E.workstation_rates(_settings())}["Assembly Station"]
        self.assertAlmostEqual(r["wages_hr"], 157 + 107, places=2)

    def test_net_is_sum_of_components(self):
        for r in E.workstation_rates(_settings()):
            self.assertAlmostEqual(r["net_hr"], sum(v for _, v in r["components"]), places=6)

    def test_onsite_has_no_rent(self):
        r = {w["name"]: w for w in E.workstation_rates(_settings())}["On-Site"]
        self.assertEqual(r["rent_hr"], 0)


class TestCalcSku(unittest.TestCase):
    def _row(self, ws, qty, mins):
        return types.SimpleNamespace(
            phase="Sheet Cutting", workstation=ws, qty=qty, carp_min=mins,
            is_misc=0, carp_total=0, helper_total=0, op_cost=0,
        )

    def test_total_min_and_phase_cost(self):
        s = _settings()
        rate = {"rent_hr": 24.48, "wages_hr": 250, "machine_hr": 0, "elec_hr": 10,
                "consumable_hr": 40, "net_hr": 324.48, "labour_hr": 250, "dep_hr": 0, "total_hr": 324.48}
        row = self._row("Pasting Station", 9, 15)
        sku = types.SimpleNamespace(labor=[row], materials=[], design_hours=0, design_flat=0, include_misc=0)
        E.calc_sku(sku, s, ws_rates={"Pasting Station": rate})
        self.assertEqual(row.carp_total, 135)                      # qty x min
        self.assertAlmostEqual(row.op_cost, 324.48 * 135 / 60, 2)  # net rate x hours

    def test_breakdown_balances_to_op_cost(self):
        s = _settings()
        rate = {"rent_hr": 111, "wages_hr": 264, "machine_hr": 0, "elec_hr": 50,
                "consumable_hr": 60, "net_hr": 485, "labour_hr": 264, "dep_hr": 0, "total_hr": 485}
        row = self._row("Panel Saw", 3, 20)
        sku = types.SimpleNamespace(labor=[row], materials=[], design_hours=0, design_flat=0, include_misc=0)
        out = E.calc_sku(sku, s, ws_rates={"Panel Saw": rate})
        self.assertAlmostEqual(out["labor_cost"] + out["overhead_cost"], row.op_cost, 2)

    def test_material_cost_and_markup(self):
        s = _settings()
        mat = types.SimpleNamespace(line_cost=1000)
        sku = types.SimpleNamespace(labor=[], materials=[mat], design_hours=0, design_flat=0, include_misc=0)
        out = E.calc_sku(sku, s, ws_rates={})
        self.assertEqual(out["material_cost"], 1000)
        self.assertAlmostEqual(out["client_material"], 1000 * 1.15, 2)  # 15% markup


class TestOpPhase(unittest.TestCase):
    def test_prefers_operation_link_over_legacy_phase(self):
        row = types.SimpleNamespace(operation="Drilling", phase="Sheet Cutting", is_misc=0)
        self.assertEqual(E.op_phase(row), "Drilling")

    def test_falls_back_to_legacy_phase(self):
        row = types.SimpleNamespace(operation=None, phase="Grooving", is_misc=0)
        self.assertEqual(E.op_phase(row), "Grooving")

    def test_misc_row_uses_sanitized_operation_name(self):
        row = types.SimpleNamespace(operation=None, phase=None, is_misc=1)
        self.assertEqual(E.op_phase(row), "Miscellaneous - extra")
        self.assertEqual(E.op_phase(row), E.MISC_OPERATION)


class TestCodes(unittest.TestCase):
    def test_customer_initials(self):
        self.assertEqual(E.customer_initials("Yogesh Sahasrabudhe"), "YS")

    def test_sku_code(self):
        self.assertEqual(E.sku_code("Yogesh Sahasrabudhe", "Master Bedroom", "Wardrobe"), "YS_MB_WAR")


if __name__ == "__main__":
    unittest.main()

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
    def test_four_components_no_machinery(self):
        rates = {w["name"]: w for w in E.workstation_rates(_settings())}
        comps = [c for c, _ in rates["Panel Saw"]["components"]]
        self.assertEqual(comps, ["Rent", "Wages", "Electricity", "Consumables"])

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


class TestCodes(unittest.TestCase):
    def test_customer_initials(self):
        self.assertEqual(E.customer_initials("Yogesh Sahasrabudhe"), "YS")

    def test_sku_code(self):
        self.assertEqual(E.sku_code("Yogesh Sahasrabudhe", "Master Bedroom", "Wardrobe"), "YS_MB_WAR")


if __name__ == "__main__":
    unittest.main()

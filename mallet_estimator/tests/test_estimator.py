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
        # OPS3: modular per-role components in canonical order; zero-value
        # components are skipped (cost seed values are ### — never in the repo,
        # so code-side seeds are zeros and only rent + wages appear here).
        rates = {w["name"]: w for w in E.workstation_rates(_settings())}
        comps = [c for c, _ in rates["Panel Saw"]["components"]]
        self.assertIn("Rent", comps)
        self.assertIn("Carpenter Wage", comps)
        self.assertIn("Helper Wage", comps)
        # order always respects WS_COMPONENTS
        idx = [E.WS_COMPONENTS.index(c) for c in comps]
        self.assertEqual(idx, sorted(idx))
        # a synthetic capital produces a Depreciation component of its own
        orig = E.WORKSTATIONS[0]["capital"]
        try:
            E.WORKSTATIONS[0]["capital"] = 120000
            r2 = {w["name"]: w for w in E.workstation_rates(_settings())}
            self.assertIn("Depreciation", [c for c, _ in r2["Panel Saw"]["components"]])
        finally:
            E.WORKSTATIONS[0]["capital"] = orig
        # D1: the Design Desk is crewed by the designer only.
        dcomps = [c for c, _ in rates["Design Desk"]["components"]]
        self.assertIn("Designer Wage", dcomps)
        self.assertNotIn("Carpenter Wage", dcomps)

    def test_salary_calendar_rates(self):
        # L1: SYNTHETIC salaries (real figures are sensitive, never in the repo).
        # salary + bonus month over (26 − 2 paid − 10/12 natl days) x 7 hrs.
        s = _settings(carpenter_salary=13000, helper_salary=6500, bonus_months=1,
                      paid_holidays_per_month=2, national_holidays_per_year=10,
                      lunch_hours_per_day=1)
        self.assertAlmostEqual(E.working_days_per_month(s), 26 - 2 - 10 / 12.0, places=4)
        self.assertAlmostEqual(E.working_hours_per_month(s), (26 - 2 - 10 / 12.0) * 7, places=4)
        r = E.staff_rates(s)
        self.assertAlmostEqual(r["carpenter"], 13000 * 13 / 12.0 / ((26 - 2 - 10 / 12.0) * 7), places=4)
        self.assertAlmostEqual(r["helper"], 6500 * 13 / 12.0 / ((26 - 2 - 10 / 12.0) * 7), places=4)

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



class TestDecor(unittest.TestCase):
    def test_bcn_standard(self):
        from mallet_estimator import decor
        v = decor.parse_slot_value("Merino 1834 Moonlit Gray")
        self.assertEqual((v["brand"], v["catalogue"], v["name"]), ("Merino", "1834", "Moonlit Gray"))
        v = decor.parse_slot_value("RT 6575")  # alias + name optional
        self.assertEqual((v["brand"], v["catalogue"], v["name"]), ("Royal Touch", "6575", ""))
        v = decor.parse_slot_value("Royal Touch 6575 Black Marmor")
        self.assertEqual(v["brand"], "Royal Touch")
        # multi-word maker + initials alias, straight from the maker list
        v = decor.parse_slot_value("Virgo Mica 1834 Grey")
        self.assertEqual((v["brand"], v["catalogue"], v["name"]), ("Virgo Mica", "1834", "Grey"))
        v = decor.parse_slot_value("VM 1834")
        self.assertEqual(v["brand"], "Virgo Mica")
        # a NEW maker supplied via the live list is recognised without code changes
        v = decor.parse_slot_value("Greenlam 204 Teak", brands=["Greenlam", "Merino"])
        self.assertEqual(v["brand"], "Greenlam")

    def test_legacy_freeform(self):
        from mallet_estimator import decor
        v = decor.parse_slot_value("YS_6534_MOONLIT_BED_Laminate")
        self.assertIsNone(v["brand"])
        self.assertEqual(v["raw"], "YS_6534_MOONLIT_BED_Laminate")

    def test_material_slots(self):
        from mallet_estimator import decor
        self.assertEqual(decor.material_slots("SG_PLY_V2_b_c"), ["b", "c"])
        self.assertEqual(decor.material_slots("SG_LAM_V1_16mm_b_a"), ["b"])
        self.assertEqual(decor.material_slots("EB_PVC_EX_c"), ["c"])
        self.assertEqual(decor.material_slots("SG_PLY_V0_a_a"), [])

    def test_multi_slot_description(self):
        from mallet_estimator import decor
        out = decor.parse_description("b=Merino 6534; c=RT 6575 Black Marmor", "SG_PLY_V2_b_c")
        self.assertEqual(out["b"]["brand"], "Merino")
        self.assertEqual(out["c"]["catalogue"], "6575")

    def test_suffixed_slots(self):
        # paste-rename décor instances: b1 is a DIFFERENT décor than b, in the
        # description parser and the slot extractors alike
        from mallet_estimator import decor
        out = decor.parse_description("b1=Merino 6534; c1=RT 6575", "SG_PLY_V2_b1_c1")
        self.assertEqual(out["b1"]["brand"], "Merino")
        self.assertEqual(out["c1"]["catalogue"], "6575")
        self.assertEqual(decor.material_slots("SG_PLY_V2_b1_c1"), ["b1", "c1"])
        self.assertEqual(decor.material_slots("EB_PVC_EX_c1"), ["c1"])
        # single letters untouched (backward compatible)
        self.assertEqual(decor.material_slots("SG_PLY_V2_b_c"), ["b", "c"])
        v = decor.parse_slot_value("b1 = Merino 1834 Moonlit Gray")
        self.assertEqual((v["brand"], v["catalogue"]), ("Merino", "1834"))

    def test_real_code_substitution(self):
        # S9v2 — the user's exact convention: trailing letters replaced by the
        # FIRST letter's décor short code; one PO code per laminate.
        from mallet_estimator import decor
        ss = {"b": "VM6534", "a": "GE1834"}
        self.assertEqual(decor.substitute_real_code("SG_LAM_V0_a_a", ss)[0], "SG_LAM_V0_GE1834")
        self.assertEqual(decor.substitute_real_code("SG_LAM_V1_16mm_a_b", ss)[0], "SG_LAM_V1_16mm_GE1834")
        self.assertEqual(decor.substitute_real_code("SG_LAM_V1_16mm_b_a", ss)[0], "SG_LAM_V1_16mm_VM6534")
        self.assertEqual(decor.substitute_real_code("EB_PVC_EX_b", ss)[0], "EB_PVC_EX_VM6534")
        # no description -> the placeholder itself stays the item
        self.assertEqual(decor.substitute_real_code("SG_LAM_V0_12mm_a_a", {}), ("SG_LAM_V0_12mm_a_a", None))
        # ply codes are untouched by design (no substitution is ever called on them)

    def test_short_codes(self):
        from mallet_estimator import decor
        self.assertEqual(decor.short_code({"brand": "Virgo Mica", "catalogue": "6534"}), "VM6534")
        self.assertEqual(decor.short_code({"brand": "Merino", "catalogue": "1834"}), "ME1834")
        self.assertEqual(decor.short_code({"short": "GE", "brand": "Greenlam", "catalogue": "1834"}), "GE")
        self.assertTrue(decor.short_code({"raw": "YS_6534_MOONLIT"}))

    def test_extract_from_pdf_text(self):
        from mallet_estimator import decor
        text = ("SG_LAM_V1_16mm_b_a / 1 mm\n"
                "b=Merino 1834 Moonlit Gray\n"
                "3 4.16 m²8.92 m² - -12 Rs34 Rs\n"
                "EB_PVC_EX_b / 1 mm x 22 mm\n"
                "b=RT 6575\n")
        out = decor.extract_slot_map(text)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["placeholder"], "SG_LAM_V1_16mm_b_a")
        self.assertEqual(out[0]["brand"], "Merino")
        self.assertEqual(out[1]["brand"], "Royal Touch")


class TestDecorBlocks(unittest.TestCase):
    BLOCK = ("b = External Laminate\n"
             "Brand = Virgo Mica\n"
             "Code = 1834\n"
             "Name = Moonlight\n"
             "Year = 2025-26\n"
             "a = Internal Laminate\n"
             "Brand = Serplex\n"
             "Code = 1834\n"
             "Name = Fabric\n")

    def test_labelled_block(self):
        from mallet_estimator import decor
        out = decor.parse_description(self.BLOCK, "SG_LAM_V1_16mm_b_a",
                                      brands=["Virgo Mica", "Serplex"])
        self.assertEqual(out["b"]["brand"], "Virgo Mica")
        self.assertEqual(out["b"]["year"], "2025-26")
        self.assertEqual(out["b"]["title"], "External Laminate")
        self.assertEqual(out["a"]["brand"], "Serplex")
        self.assertEqual(out["a"]["name"], "Fabric")

    def test_unknown_maker_accepted_when_labelled(self):
        from mallet_estimator import decor
        out = decor.parse_description("b = Ext\nBrand = Greenlam\nCode = 204\n",
                                      "SG_LAM_V1_16mm_b_a")
        self.assertEqual(out["b"]["brand"], "Greenlam")  # explicit label = intent

    def test_initials_canonicalize_in_block(self):
        from mallet_estimator import decor
        out = decor.parse_description("b = Ext\nBrand = VM\nCode = 1834\n",
                                      "SG_LAM_V1_16mm_b_a", brands=["Virgo Mica"])
        self.assertEqual(out["b"]["brand"], "Virgo Mica")

    def test_extract_block_from_pdf_text(self):
        from mallet_estimator import decor
        text = ("SG_LAM_V1_16mm_b_a / 1 mm\n" + self.BLOCK +
                "3 4.16 m²8.92 m² - -12 Rs34 Rs\n")
        out = decor.extract_slot_map(text, brands=["Virgo Mica", "Serplex"])
        slots = {(e["placeholder"], e["slot"]) for e in out}
        self.assertIn(("SG_LAM_V1_16mm_b_a", "b"), slots)
        self.assertIn(("SG_LAM_V1_16mm_b_a", "a"), slots)


class TestLineDiscountTax(unittest.TestCase):
    """Per-line discount + tax arithmetic (the pure part of price_material_lines):
    stock prices are PRE-tax, discount applies to the line only, tax follows the
    line's applied rate or the policy rate."""

    @staticmethod
    def _price(qty, rate, disc_pct, policy_pct, applied_pct=None, client_supplied=False):
        net_rate = rate * (1 - disc_pct / 100.0)
        discount = qty * rate * disc_pct / 100.0
        line_cost = qty * net_rate
        pct = policy_pct if applied_pct is None else applied_pct
        tax = line_cost * pct / 100.0
        if client_supplied:
            line_cost = discount = tax = 0
        return {"net_rate": net_rate, "discount": discount, "line_cost": line_cost, "tax": tax}

    def test_discount_applies_to_line_only(self):
        r = self._price(10, 100, 10, 18)
        self.assertAlmostEqual(r["net_rate"], 90)
        self.assertAlmostEqual(r["discount"], 100)
        self.assertAlmostEqual(r["line_cost"], 900)

    def test_tax_follows_policy_then_override(self):
        self.assertAlmostEqual(self._price(10, 100, 0, 18)["tax"], 180)
        self.assertAlmostEqual(self._price(10, 100, 0, 18, applied_pct=12)["tax"], 120)
        # tax is charged on the DISCOUNTED value, not the list value
        self.assertAlmostEqual(self._price(10, 100, 10, 18)["tax"], 162)

    def test_client_supplied_costs_us_nothing(self):
        r = self._price(10, 100, 10, 18, client_supplied=True)
        self.assertEqual((r["line_cost"], r["discount"], r["tax"]), (0, 0, 0))


class TestTaxDiscount(unittest.TestCase):
    """The full line model: MRP -> discount -> taxable -> std tax vs applied
    tax -> tax discount -> landed."""

    @staticmethod
    def _line(qty, mrp, disc_pct, std_pct, applied_pct=None):
        net_rate = mrp * (1 - disc_pct / 100.0)
        taxable = qty * net_rate
        applied = std_pct if applied_pct is None else applied_pct
        tax_disc_pct = std_pct - applied
        return {
            "net_rate": net_rate,
            "discount": qty * mrp * disc_pct / 100.0,
            "taxable": taxable,
            "tax_discount_pct": tax_disc_pct,
            "tax_saved": taxable * tax_disc_pct / 100.0,
            "tax": taxable * applied / 100.0,
            "landed": taxable + taxable * applied / 100.0,
        }

    def test_full_line_chain(self):
        r = self._line(10, 100, 10, 18, 12)
        self.assertAlmostEqual(r["net_rate"], 90)
        self.assertAlmostEqual(r["discount"], 100)
        self.assertAlmostEqual(r["taxable"], 900)
        self.assertAlmostEqual(r["tax_discount_pct"], 6)
        self.assertAlmostEqual(r["tax_saved"], 54)     # 900 x 6%
        self.assertAlmostEqual(r["tax"], 108)          # 900 x 12%
        self.assertAlmostEqual(r["landed"], 1008)
        # policy tax would have been 162 — saved is exactly the difference
        self.assertAlmostEqual(900 * 0.18 - r["tax"], r["tax_saved"])

    def test_no_override_means_no_tax_discount(self):
        r = self._line(10, 100, 0, 18)
        self.assertAlmostEqual(r["tax_discount_pct"], 0)
        self.assertAlmostEqual(r["tax_saved"], 0)
        self.assertAlmostEqual(r["landed"], 1180)

    def test_above_policy_rate_is_negative_saving(self):
        r = self._line(1, 100, 0, 12, 18)
        self.assertAlmostEqual(r["tax_discount_pct"], -6)
        self.assertAlmostEqual(r["tax_saved"], -6)


class TestSingleSkuGrid(unittest.TestCase):
    """The estimate screen collapsed three tables (intake grid, SKUs grid,
    files panel) into ONE grid, so the SKUs child table now has to carry the
    intake columns as well. These live in JSON, which nothing else type-checks
    — a stray edit would quietly take the intake away and leave no way to
    attach a Part List CSV without opening the SKU."""

    def _child(self):
        import json
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "mallet_estimator", "doctype",
                            "execution_estimate_sku", "execution_estimate_sku.json")
        with open(path) as fh:
            return json.load(fh)

    def test_grid_carries_the_intake_columns(self):
        fields = {f["fieldname"]: f for f in self._child()["fields"]}
        for fn in ("parts_csv", "estimate_pdf", "views_pdf"):
            self.assertEqual(fields[fn]["fieldtype"], "Attach", fn)
            self.assertTrue(fields[fn].get("in_list_view"), f"{fn} must be a grid column")

    def test_derived_columns_are_read_only(self):
        # mode / sheets are a VIEW of the SKU; typing over them would invent a
        # second truth the next save silently overwrites
        fields = {f["fieldname"]: f for f in self._child()["fields"]}
        for fn in ("estimation_mode", "sheets", "client_total", "est_days"):
            self.assertTrue(fields[fn].get("in_list_view"), f"{fn} must be a grid column")
        for fn in ("estimation_mode", "sheets"):
            self.assertTrue(fields[fn].get("read_only"), f"{fn} must be read-only")


if __name__ == "__main__":
    unittest.main()

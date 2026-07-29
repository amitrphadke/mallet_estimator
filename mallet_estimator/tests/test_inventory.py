# Integration tests for the material inventory — run under `bench run-tests`.
import frappe

from mallet_estimator import inventory

try:
    from frappe.tests import IntegrationTestCase as MalletTestCase
except Exception:  # Frappe v15 fallback
    from frappe.tests.utils import FrappeTestCase as MalletTestCase


class TestClassification(MalletTestCase):
    def test_kind_for_code(self):
        self.assertEqual(inventory.kind_for_code("SG_PLY_V0_a_a"), "sheet")
        self.assertEqual(inventory.kind_for_code("SG_LAM_V0_12mm_a_a"), "laminate")  # LAM before SG
        self.assertEqual(inventory.kind_for_code("DL_Oak"), "laminate")
        self.assertEqual(inventory.kind_for_code("EB_PVC_IN_a"), "edge")
        self.assertEqual(inventory.kind_for_code("HWD_Hinge"), "hardware")
        self.assertEqual(inventory.kind_for_code("SW_Teak"), "solidwood")

    def test_item_code_carries_thickness_for_sheets(self):
        self.assertEqual(inventory.item_code_for("SG_PLY_V0_a_a", 16, "sheet"), "SG_PLY_V0_a_a_16mm")
        self.assertEqual(inventory.item_code_for("HWD_Hinge", 0, "hardware"), "HWD_Hinge")


class TestMaterialItem(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        inventory.ensure_inventory_masters()

    def test_plywood_item(self):
        code, rate, source = inventory.ensure_material_item("SG_PLY_TEST", kind="sheet", thickness=16)
        self.assertTrue(frappe.db.exists("Item", code))
        it = frappe.get_doc("Item", code)
        self.assertEqual(it.item_group, "Sheet Goods")
        self.assertEqual(it.stock_uom, "Sheet")
        self.assertEqual(it.is_stock_item, 1)
        uoms = {r.uom: r.conversion_factor for r in it.uoms}
        self.assertIn("Square Meter", uoms)               # 1 Sheet = ~2.98 m²
        self.assertAlmostEqual(uoms["Square Meter"], inventory.SHEET_AREA_SQM, 3)

    def test_edge_banding_roll_conversion(self):
        code, _, _ = inventory.ensure_material_item("EB_TEST", kind="edge")
        it = frappe.get_doc("Item", code)
        self.assertEqual(it.item_group, "Edge Banding")
        self.assertEqual(it.stock_uom, "Meter")
        self.assertEqual(it.purchase_uom, "Roll")
        uoms = {r.uom: r.conversion_factor for r in it.uoms}
        self.assertEqual(uoms["Roll"], 50)                # buy rolls, stock metres

    def test_idempotent_no_duplicate(self):
        inventory.ensure_material_item("SG_DUP_TEST", kind="sheet", thickness=18)
        n1 = frappe.db.count("Item", {"item_code": "SG_DUP_TEST_18mm"})
        inventory.ensure_material_item("SG_DUP_TEST", kind="sheet", thickness=18)
        n2 = frappe.db.count("Item", {"item_code": "SG_DUP_TEST_18mm"})
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 1)

    def test_unset_price_flagged(self):
        code, rate, source = inventory.ensure_material_item("HWD_TEST_UNPRICED", kind="hardware")
        self.assertEqual(rate, 0)
        self.assertEqual(source, "unset")

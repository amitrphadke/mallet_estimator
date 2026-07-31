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

    def test_is_material_code(self):
        # material families are recognised; a finished article / real Product is not
        for c in ("SG_PLY_V0_a_a", "SG_LAM_V1_16mm_a_b", "EB_PVC_IN_a", "HWD_Hinge", "SW_Teak"):
            self.assertTrue(inventory.is_material_code(c), c)
        for c in ("YS_MB_WAR", "Products", "Some Random Product"):
            self.assertFalse(inventory.is_material_code(c), c)


class TestFixMaterialItems(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        inventory.ensure_inventory_masters()

    def test_rehomes_and_stocks_a_misfiled_material(self):
        from mallet_estimator.patches import fix_material_items
        # simulate an old-build item: a plywood sheet stuck in the default group,
        # non-stock, measured in Nos with no conversions.
        code = "SG_PLY_FIXME_16mm"
        if frappe.db.exists("Item", code):
            frappe.delete_doc("Item", code, force=True, ignore_permissions=True)
        it = frappe.new_doc("Item")
        it.item_code = code
        it.item_group = "Products" if frappe.db.exists("Item Group", "Products") else inventory._fallback_group()
        it.stock_uom = "Nos"
        it.is_stock_item = 0
        it.insert(ignore_permissions=True)

        fix_material_items.execute()

        it.reload()
        self.assertEqual(it.item_group, "Sheet Goods")
        self.assertEqual(it.stock_uom, "Sheet")
        self.assertEqual(it.is_stock_item, 1)
        self.assertEqual(it.is_purchase_item, 1)
        self.assertIn("Square Meter", {r.uom for r in it.uoms})
        # manufacturers seeded
        self.assertTrue(frappe.db.exists("Manufacturer", "Hafele"))


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

    def test_hardware_item_by_designation_with_dims(self):
        # F7: the hardware Item is the designation, carrying the part's physical
        # size in the generic Length/Width fields (no "sheet" size, no thickness
        # suffix on the code).
        code, _, _ = inventory.ensure_material_item(
            "HWD_AH_SC_0_TEST", kind="hardware", thickness=42,
            dims={"category": "HWD_Hinge", "length": 80, "width": 65, "thickness": 42},
        )
        it = frappe.get_doc("Item", code)
        self.assertEqual(it.item_code, "HWD_AH_SC_0_TEST")
        self.assertEqual(it.item_group, "Hardware")
        self.assertEqual(it.stock_uom, "Nos")
        self.assertEqual(it.is_stock_item, 1)
        self.assertEqual(it.get("mallet_sheet_length_mm"), 80)
        self.assertEqual(it.get("mallet_sheet_width_mm"), 65)
        self.assertEqual(it.get("mallet_thickness_mm"), 42)
        self.assertIn("HWD_Hinge", it.description or "")

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

# Wave B — execution design + variance; part-list hardware; material rate card.
import frappe

try:
    from frappe.tests import IntegrationTestCase as MalletTestCase
except Exception:
    from frappe.tests.utils import FrappeTestCase as MalletTestCase


class TestExecutionVariance(MalletTestCase):
    def test_compute_execution_variance(self):
        # V1/V2: actual_amount = qty x rate; variance = actual - estimated; the SKU
        # execution cost and variance roll up.
        sku = frappe.new_doc("Estimate SKU")
        sku.material_cost = 100
        sku.append("execution_materials", {
            "est_material": "HWD_Hinge", "est_qty": 10, "est_rate": 10, "est_amount": 100,
            "chosen_item": None, "actual_qty": 10, "actual_rate": 23, "actual_amount": 0,
        })
        sku.compute_execution()
        row = sku.execution_materials[0]
        self.assertEqual(row.actual_amount, 230)          # 10 x 23
        self.assertEqual(row.variance, 130)               # 230 - 100 (actual over estimate)
        self.assertEqual(sku.execution_material_cost, 230)
        self.assertEqual(sku.execution_variance, 130)     # 230 - material_cost 100

    def test_no_execution_design_zero_variance(self):
        sku = frappe.new_doc("Estimate SKU")
        sku.material_cost = 100
        sku.compute_execution()
        self.assertEqual(sku.execution_variance, 0)


class TestPartlistHardware(MalletTestCase):
    def test_parse_partlist_text(self):
        # The Parts List PDF identifies hardware correctly: group heading
        # (HWD_Hinge) -> real designations (HWD_AH_SC_0). #N instance suffixes are
        # summed; a qty wrapped onto a later line (after noise) is captured.
        from mallet_estimator import views_pdf
        text = (
            "\xa0 HWD_Handle\n"
            "No. Designation Qty.\n"
            "99HWD_HandleDrawer_150mm 2\n"
            "100HWD_Handle_150mm#3 2\n"
            "101HWD_Handle_150mm#1 1\n"
            "\xa0 HWD_Hinge\n"
            "No. Designation Qty.\n"
            "102HWD_AH_SC_0 11\n"
            "\xa0 HWD_TowerBolt\n"
            "No. Designation Qty.\n"
            "107HWD_Lock_20mm#1\n"
            "lock noise line\n"
            "2\n"
        )
        rows = {r["code"]: r for r in views_pdf.parse_partlist_text(text)}
        self.assertEqual(rows["HWD_Handle_150mm"]["qty"], 3)          # 2 + 1 across #N
        self.assertEqual(rows["HWD_Handle_150mm"]["category"], "HWD_Handle")
        self.assertEqual(rows["HWD_AH_SC_0"]["qty"], 11)
        self.assertEqual(rows["HWD_AH_SC_0"]["category"], "HWD_Hinge")
        self.assertEqual(rows["HWD_Lock_20mm"]["qty"], 2)             # wrapped qty
        self.assertEqual(rows["HWD_Lock_20mm"]["category"], "HWD_TowerBolt")


class TestMaterialRateCard(MalletTestCase):
    def test_seed_material_rates(self):
        from mallet_estimator.patches import seed_material_rates
        from mallet_estimator import inventory
        inventory.ensure_inventory_masters()
        seed_material_rates.execute()
        self.assertTrue(frappe.db.exists("Item", "HWD_AH_SC_0"))
        # C1: client-selectable hardware lands in its own group (falls back to
        # Hardware only when the group doesn't exist yet).
        self.assertIn(frappe.db.get_value("Item", "HWD_AH_SC_0", "item_group"),
                      ("Client Hardware", "Hardware"))
        rate, source = inventory.material_rate("HWD_AH_SC_0")
        self.assertEqual(rate, 300)
        self.assertEqual(source, "assumed")
        # sheet code already carries its thickness — no double suffix
        self.assertTrue(frappe.db.exists("Item", "SG_PLY_V0_a_a_16mm"))
        self.assertFalse(frappe.db.exists("Item", "SG_PLY_V0_a_a_16mm_16mm"))

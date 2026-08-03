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
        # Rates are sensitive (###) and no longer seeded from the repo — keyed on
        # the site's price list instead, so no rate assertion here.
        # sheet code already carries its thickness — no double suffix
        self.assertTrue(frappe.db.exists("Item", "SG_PLY_V0_a_a_16mm"))
        self.assertFalse(frappe.db.exists("Item", "SG_PLY_V0_a_a_16mm_16mm"))


class TestPartlistEdges(MalletTestCase):
    def test_parse_edges_with_wrapped_spec(self):
        from mallet_estimator import views_pdf
        text = (
            " EB_PVC_EX_b / 1 mm x 22 mm 21 20.41 m0.45 m²0.00 m³\n"
            " EB_PVC_IN_a / 1 mm x 22 mm\n"
            "b=YS_6534_MOONLIT_BED_Laminate\n"
            "27 24.13 m0.53 m²0.00 m³\n"
            " SG_LAM_V0_12mm_a_a / 1 mm 24 17.38 m9.56 m²0.01 m³\n"
        )
        edges = views_pdf.parse_partlist_edges_text(text)
        self.assertEqual(len(edges), 2)
        self.assertEqual(edges[0], {"code": "EB_PVC_EX_b", "parts": 21, "meters": 20.41})
        self.assertEqual(edges[1], {"code": "EB_PVC_IN_a", "parts": 27, "meters": 24.13})

    def test_parse_edges_qty_only_layout(self):
        # BED-style export: no length columns — the row ends in the parts count;
        # a following part-table number ("1 1944 m…") must NOT leak in as meters.
        from mallet_estimator import views_pdf
        text = (
            " EB_PVC_EX_b / 1 mm x 22 mm 36\n"
            " EB_PVC_IN_a / 1 mm x 22 mm\n"
            "b=YS_6534_MOONLIT_BED_Laminate\n"
            "1 1944 m0.47 m²0.00 m³\n"
            "2 1944 m0.47 m²0.00 m³\n"
            "3 1944 m0.47 m²0.00 m³\n"
        )
        edges = views_pdf.parse_partlist_edges_text(text)
        self.assertEqual(edges[0], {"code": "EB_PVC_EX_b", "parts": 36, "meters": None})
        self.assertEqual(edges[1]["code"], "EB_PVC_IN_a")
        self.assertIsNone(edges[1]["meters"])  # garbage 1944 m rejected

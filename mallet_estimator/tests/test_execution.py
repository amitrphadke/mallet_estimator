# Wave B — execution design + variance + PDF rate import.
import frappe

from mallet_estimator import rate_import

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


class TestPdfRateParse(MalletTestCase):
    def test_parse_rate_pdf_lines(self):
        # V4: the Sun Tradelink layout is parsed even with wrapped descriptions.
        text = (
            "1 H-311.01.357 Clip-On Hinges with 3D Eccentric Mounting Plate\n"
            "and Integrated Soft Close ( Nickel Plated) - Full Overlay\n"
            "1 PR 230 PR 0.0% 230\n"
            "6 H-422.87.030 Full Ext BBR Steel Zinc 250mm 35kg (10\") 1 PR 490 PR 50.0% 245\n"
            "9,768.6\nCGST 879.21\n"
        )
        orig = rate_import._pdf_text
        rate_import._pdf_text = lambda content: text
        try:
            rows = rate_import.parse_rate_pdf(b"ignored")
        finally:
            rate_import._pdf_text = orig
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["part_no"], "H-311.01.357")
        self.assertEqual(rows[0]["rate"], 230)
        self.assertIn("Full Overlay", rows[0]["description"])
        self.assertEqual(rows[1]["part_no"], "H-422.87.030")
        self.assertEqual(rows[1]["discount"], 50)

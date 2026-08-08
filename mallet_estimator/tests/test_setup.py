# Config health-check tests — assert the app's masters get created and that
# verify_setup() reflects reality. Run under `bench run-tests`.
import frappe

from mallet_estimator import install, inventory

try:
    from frappe.tests import IntegrationTestCase as MalletTestCase
except Exception:
    from frappe.tests.utils import FrappeTestCase as MalletTestCase


def _ensure_company():
    name = frappe.db.get_value("Company", {}, "name")
    if name:
        return name
    co = frappe.get_doc({
        "doctype": "Company", "company_name": "Mallet Test Co",
        "abbr": "MTC", "default_currency": "INR", "country": "India",
    }).insert(ignore_permissions=True)
    return co.name


class TestMasters(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        install.seed_settings()
        inventory.ensure_inventory_masters()
        install.ensure_project_customization()
        install.ensure_gst_masters()
        install.ensure_manufacturing_masters()
        inventory.ensure_warehouses(_ensure_company())

    def test_item_groups(self):
        for g in [inventory.PARENT_GROUP, inventory.CLIENT_SKU_GROUP] + inventory.ITEM_GROUPS:
            self.assertTrue(frappe.db.exists("Item Group", g), f"missing Item Group {g}")

    def test_uoms(self):
        for u in ["Sheet", "Meter", "Roll", "Square Meter"]:
            self.assertTrue(frappe.db.exists("UOM", u), f"missing UOM {u}")

    def test_item_custom_fields(self):
        meta = frappe.get_meta("Item")
        for f in install.ITEM_CUSTOM_FIELDS:
            self.assertTrue(meta.has_field(f), f"missing Item field {f}")

    def test_workstations(self):
        from mallet_estimator.estimator import WORKSTATIONS
        for w in WORKSTATIONS:
            self.assertTrue(frappe.db.exists("Workstation", w["name"]), f"missing {w['name']}")

    def test_warehouses(self):
        for w in install.WAREHOUSE_LEAVES:
            self.assertTrue(frappe.db.exists("Warehouse", {"warehouse_name": w}), f"missing Warehouse {w}")

    def test_verify_setup_all_ok(self):
        report = install.verify_setup()
        self.assertTrue(report["all_ok"], f"verify_setup failed: {report['failed']}")

    def test_batch_tier_masters(self):
        self.assertTrue(frappe.db.exists("DocType", "Mallet Operation Batch Tier"))
        self.assertTrue(frappe.get_meta("Operation").has_field("mallet_batch_tiers"),
                        "missing Operation.mallet_batch_tiers")

    def test_ensure_warehouses_idempotent(self):
        before = frappe.db.count("Warehouse")
        inventory.ensure_warehouses(_ensure_company())
        self.assertEqual(frappe.db.count("Warehouse"), before)

    def test_strip_invalid_workstation_costs(self):
        # B1: a stale 'Machinery' cost row (component removed) is dropped so the
        # workstation re-save no longer fails link validation.
        ws = frappe.new_doc("Workstation")
        ws.workstation_name = "ZZ Strip Test"
        ws.append("workstation_costs", {"operating_component": "Rent", "operating_cost": 10})
        ws.append("workstation_costs", {"operating_component": "Machinery", "operating_cost": 5})
        self.assertTrue(install._strip_invalid_costs(ws))
        comps = {r.operating_component for r in ws.workstation_costs}
        self.assertNotIn("Machinery", comps)
        self.assertIn("Rent", comps)
        self.assertFalse(install._strip_invalid_costs(ws))  # idempotent — nothing left to strip

    def test_single_sku_grid_and_decor_masters(self):
        self.assertTrue(frappe.db.exists("DocType", "Mallet Decor"))
        # The SKUs grid is the ONE table on the estimate: select-or-create in
        # its link column, this SKU's input files in the same row, its numbers
        # beside them. If any of these columns goes missing the estimate screen
        # silently loses the intake it replaced.
        row = frappe.get_meta("Execution Estimate SKU")
        for f in ("estimate_sku", "parts_csv", "estimate_pdf", "views_pdf",
                  "estimation_mode", "sheets", "client_total", "est_days"):
            self.assertTrue(row.has_field(f), f"missing Execution Estimate SKU.{f}")
        est = frappe.get_meta("Estimate")
        for f in ("sku_materials_html", "sku_summary_html"):
            self.assertTrue(est.has_field(f), f"missing Estimate.{f}")
        # Creating a SKU straight from the grid's link column needs quick entry.
        self.assertTrue(frappe.get_meta("Estimate SKU").quick_entry,
                        "Estimate SKU must allow quick entry (create from the grid)")
        # Repair work (R1): its own activity table and the two policy numbers.
        self.assertTrue(frappe.db.exists("DocType", "Estimate Repair Activity"))
        for f in ("work_type", "repair_activities", "repair_csv", "repair_visits",
                  "client_repair"):
            self.assertTrue(frappe.get_meta("Estimate SKU").has_field(f),
                            f"missing Estimate SKU.{f}")
        for f in ("work_scope", "total_new_work", "total_repair"):
            self.assertTrue(est.has_field(f), f"missing Estimate.{f}")
        for f in ("repair_visit_charge", "markup_repair"):
            self.assertTrue(frappe.get_meta("Estimate Settings").has_field(f),
                            f"missing Estimate Settings.{f}")
        for t in ("Estimate SKU Decor", "Estimate SKU Decor Edge"):
            self.assertTrue(frappe.get_meta(t).has_field("decor"),
                            f"missing {t}.decor link")

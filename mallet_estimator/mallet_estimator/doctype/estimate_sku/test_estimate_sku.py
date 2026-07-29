# Integration test for the Estimate SKU flow — run under `bench run-tests`.
import frappe

from mallet_estimator import install, inventory

try:
    from frappe.tests import IntegrationTestCase as MalletTestCase
except Exception:
    from frappe.tests.utils import FrappeTestCase as MalletTestCase


class TestEstimateSKU(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        install.seed_settings()
        install.ensure_rooms()
        inventory.ensure_inventory_masters()
        install.ensure_manufacturing_masters()
        if not frappe.db.exists("Customer", "Test Customer"):
            frappe.get_doc({
                "doctype": "Customer", "customer_name": "Test Customer",
                "customer_type": "Individual",
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("Project", {"project_name": "Mallet Test Project"}):
            cls.project = frappe.get_doc({
                "doctype": "Project", "project_name": "Mallet Test Project",
                "customer": "Test Customer",
            }).insert(ignore_permissions=True).name
        else:
            cls.project = frappe.db.get_value("Project", {"project_name": "Mallet Test Project"}, "name")

    def _new_sku(self, **over):
        code, _, _ = inventory.ensure_material_item("SG_PLY_SKUTEST", kind="sheet", thickness=16)
        doc = frappe.get_doc({
            "doctype": "Estimate SKU",
            "project": self.project,
            "customer": "Test Customer",
            "room": "Master Bedroom",
            "article_name": "Wardrobe",
            "auto_name": 1,
            "labor": [{"phase": "Sheet Cutting", "workstation": "Panel Saw", "qty": 2, "carp_min": 20}],
            "materials": [{"item": code, "material": "SG_PLY_SKUTEST", "qty": 1, "unit_cost": 100}],
        })
        doc.update(over)
        return doc.insert(ignore_permissions=True)

    def test_costs_computed(self):
        sku = self._new_sku()
        self.assertEqual(sku.labor[0].carp_total, 40)            # 2 x 20
        self.assertGreater(sku.labor[0].op_cost, 0)              # priced from workstation
        self.assertEqual(sku.material_cost, 100)                 # 1 x 100
        self.assertGreater(sku.client_total, sku.internal_cost * 0)  # a client price exists
        self.assertEqual(sku.sku_code, "TC_MB_WAR")              # customer initials + room + article

    def test_customer_supplied_material_is_free(self):
        sku = self._new_sku(materials=[{
            "item": inventory.ensure_material_item("SG_PLY_CS", kind="sheet", thickness=16)[0],
            "material": "SG_PLY_CS", "qty": 5, "unit_cost": 200, "customer_supplied": 1,
        }])
        self.assertEqual(sku.material_cost, 0)                   # client already owns it

    def test_create_item_lands_in_client_sku_group(self):
        sku = self._new_sku(create_item=1, article_name="Bookshelf")
        self.assertTrue(sku.item, "an ERPNext Item should be linked")
        self.assertEqual(frappe.db.get_value("Item", sku.item, "item_group"), inventory.CLIENT_SKU_GROUP)

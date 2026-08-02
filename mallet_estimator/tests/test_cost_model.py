# Wave T1/L1/OPS3/D1/J1/C1 — GST landed rates, salary calendar, modular
# components, design labor, joinery consumables, cost breakup + transport.
import frappe

from mallet_estimator import inventory, install

try:
    from frappe.tests import IntegrationTestCase as MalletTestCase
except Exception:
    from frappe.tests.utils import FrappeTestCase as MalletTestCase


class TestGstLanded(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        inventory.ensure_inventory_masters()

    def test_landed_rate_grosses_up_gst(self):
        code, _, _ = inventory.ensure_material_item("HWD_GST_TEST", kind="hardware")
        inventory.set_assumed_rate(code, 100)
        landed, base, gst, source = inventory.landed_rate(code)
        self.assertEqual(base, 100)
        self.assertEqual(gst, 18)              # default when unset
        self.assertAlmostEqual(landed, 118.0, 2)
        self.assertEqual(source, "assumed")

    def test_material_bucket_classifier(self):
        self.assertEqual(inventory.material_bucket("SG_PLY_V0_a_a_16mm"), "Ply V0 (structure grade)")
        self.assertEqual(inventory.material_bucket("SG_PLY_V1_a_b_16mm"), "Ply V1 (visible grade)")
        self.assertEqual(inventory.material_bucket("SG_LAM_V1_16mm_b_a"), "Laminate External")
        self.assertEqual(inventory.material_bucket("SG_LAM_V0_12mm_a_a"), "Laminate Internal")
        self.assertEqual(inventory.material_bucket("EB_PVC_EX_b"), "Edge Banding External")
        self.assertEqual(inventory.material_bucket("EB_PVC_IN_a"), "Edge Banding Internal")
        self.assertEqual(inventory.material_bucket("HWD_AH_SC_0"), "Client Hardware")
        self.assertEqual(inventory.material_bucket("HWD_Screw_8x32"), "Joinery Hardware")
        self.assertEqual(inventory.material_bucket("JH_Fevicol"), "Joinery Hardware")

    def test_hardware_group_split(self):
        self.assertEqual(inventory.hardware_group("HWD_AH_SC_0"), inventory.CLIENT_HW_GROUP)
        self.assertEqual(inventory.hardware_group("HWD_MiniFix"), inventory.JOINERY_GROUP)
        self.assertEqual(inventory.hardware_group("JH_Abrotape"), inventory.JOINERY_GROUP)


class TestCostModelPatch(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        inventory.ensure_inventory_masters()

    def test_patch_seeds_joinery_and_groups(self):
        from mallet_estimator.patches import cost_model_rework
        cost_model_rework.execute()
        self.assertTrue(frappe.db.exists("Item Group", inventory.JOINERY_GROUP))
        self.assertTrue(frappe.db.exists("Item Group", inventory.CLIENT_HW_GROUP))
        self.assertTrue(frappe.db.exists("Item", "JH_Fevicol"))
        it = frappe.get_doc("Item", "JH_Abrotape")
        self.assertEqual(it.stock_uom, "Meter")
        self.assertIn("Roll", {r.uom for r in it.uoms})
        # MRPs are sensitive (###) and not seeded from the repo — rates are keyed
        # on the site, so only the item/UOM structure is asserted here.

    def test_design_desk_and_operations_seeded(self):
        install.ensure_manufacturing_masters()
        self.assertTrue(frappe.db.exists("Workstation", "Design Desk"))
        self.assertTrue(frappe.db.exists("Operation", "Live 3D Floor Plan"))
        self.assertTrue(frappe.db.exists("Operation", "Site Measurement (ImageMeter + Laser)"))

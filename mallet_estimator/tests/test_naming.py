# Naming, verified against a real database rather than as pure functions.
#
# abbr() returning BAS_CAB does not prove that saving a SKU produces an Item
# called BAS_CAB. The code is computed on validate, made unique against rows
# that already exist, and carried onto an ERPNext Item that has to FOLLOW a
# rename. Each of those steps needs a database to be wrong in, so each is
# exercised here against one.
import frappe

from mallet_estimator import estimator

try:
    from frappe.tests import IntegrationTestCase as MalletTestCase
except Exception:
    from frappe.tests.utils import FrappeTestCase as MalletTestCase


# Distinctive enough that no other test's SKUs share the prefix — _unique_code
# looks at every Estimate SKU whose code starts the same way, so a shared
# prefix would make these assertions depend on test ordering.
CUSTOMER_NAME = "Naming Verification Customer"


def _room(name):
    if not frappe.db.exists("Estimate Room", name):
        frappe.get_doc({"doctype": "Estimate Room", "room_name": name}).insert(
            ignore_permissions=True)
    return name


def _customer():
    existing = frappe.db.get_value("Customer", {"customer_name": CUSTOMER_NAME}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Customer", "customer_name": CUSTOMER_NAME,
        "customer_type": "Individual",
    }).insert(ignore_permissions=True).name


def _company():
    name = frappe.db.get_value("Company", {}, "name")
    if name:
        return name
    return frappe.get_doc({
        "doctype": "Company", "company_name": "Mallet Test Co", "abbr": "MTC",
        "default_currency": "INR", "country": "India",
    }).insert(ignore_permissions=True).name


def _project(customer):
    existing = frappe.db.get_value("Project", {"project_name": "Naming Verification"}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Project", "project_name": "Naming Verification", "customer": customer,
        "company": _company(),
    }).insert(ignore_permissions=True).name


def _why_the_rename_did_not_happen(old_code, new_code):
    """sync_item swallows a refused rename into the Error Log and carries on,
    which is right for a user mid-save and useless for a failing test. Pull the
    reason back out so the failure says WHY instead of only that it happened."""
    facts = [
        f"old Item {old_code} exists: {bool(frappe.db.exists('Item', old_code))}",
        f"new Item {new_code} exists: {bool(frappe.db.exists('Item', new_code))}",
    ]
    log = frappe.get_all("Error Log", filters={"method": ["like", "%rename item%"]},
                         fields=["error"], order_by="creation desc", limit=1)
    if log:
        facts.append("Error Log: " + (log[0].error or "")[-1200:])
    return "\n".join(facts)


class TestSkuNamingInTheDatabase(MalletTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = _customer()
        cls.project = _project(cls.customer)
        _room("Kitchen")
        _room("Master Bedroom")
        # The customer prefix is whatever the customer is called; only the room
        # and article halves are asserted literally, since those are the rule.
        cls.ci = estimator.customer_initials(
            frappe.db.get_value("Customer", cls.customer, "customer_name"))

    def _sku(self, article, room):
        doc = frappe.new_doc("Estimate SKU")
        doc.project = self.project
        doc.customer = self.customer
        doc.room = room
        doc.article_name = article
        return doc.insert(ignore_permissions=True)

    def test_a_saved_sku_carries_the_code_and_an_item_named_for_it(self):
        sku = self._sku("Base Cabinet", "Kitchen")
        self.assertEqual(sku.sku_code, f"{self.ci}_KIT_BAS_CAB")
        # The Item is the point of the exercise — it is what the stock ledger,
        # the BOM and the person searching actually see.
        self.assertEqual(sku.item, sku.sku_code)
        self.assertTrue(frappe.db.exists("Item", sku.sku_code),
                        f"no ERPNext Item called {sku.sku_code}")

    def test_a_multi_word_room_keeps_its_initials(self):
        sku = self._sku("Wardrobe", "Master Bedroom")
        self.assertEqual(sku.sku_code, f"{self.ci}_MB_WAR")
        self.assertTrue(frappe.db.exists("Item", sku.sku_code))

    def test_a_second_identical_article_gets_its_own_code_and_its_own_item(self):
        # Two wardrobes for one customer in one room used to compute the SAME
        # code, and the second silently attached itself to the FIRST one's Item
        # — both wrote to one Item and its price became whichever saved last.
        first = self._sku("Study Table", "Kitchen")
        second = self._sku("Study Table", "Kitchen")
        self.assertEqual(first.sku_code, f"{self.ci}_KIT_STU_TAB")
        self.assertEqual(second.sku_code, f"{self.ci}_KIT_STU_TAB_2")
        self.assertNotEqual(first.item, second.item)
        self.assertTrue(frappe.db.exists("Item", second.sku_code))

    def test_renaming_the_article_renames_the_erpnext_item(self):
        sku = self._sku("Tall Unit", "Kitchen")
        old_code = sku.sku_code
        self.assertEqual(old_code, f"{self.ci}_KIT_TAL_UNI")
        self.assertTrue(frappe.db.exists("Item", old_code))

        sku.article_name = "Crockery Unit"
        sku.save(ignore_permissions=True)

        new_code = f"{self.ci}_KIT_CRO_UNI"
        self.assertEqual(sku.sku_code, new_code)
        self.assertEqual(sku.item, new_code, _why_the_rename_did_not_happen(old_code, new_code))
        self.assertTrue(frappe.db.exists("Item", new_code),
                        "the Item did not follow the rename")
        # Renamed, not duplicated: the old code must be gone, or a search for
        # the article turns up two Items and neither is obviously the live one.
        self.assertFalse(frappe.db.exists("Item", old_code),
                         f"{old_code} still exists — the Item was copied, not renamed")

    def test_a_one_word_room_reads_as_a_room_not_a_letter(self):
        # "K" in the middle of a code is a letter, not a room. This is the whole
        # reason single-word rooms take three characters.
        sku = self._sku("Chimney Hood", "Kitchen")
        self.assertIn("_KIT_", sku.sku_code)
        self.assertNotIn("_K_", sku.sku_code)

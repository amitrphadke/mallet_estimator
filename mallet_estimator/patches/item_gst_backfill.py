import frappe

from mallet_estimator import inventory


def execute():
    """T2b — every material Item carries the GST Item Tax Template explicitly
    (older items relied on group-level inheritance only, which shows an empty
    tax table on the Item page)."""
    groups = [g for g in inventory.ITEM_GROUPS if frappe.db.exists("Item Group", g)]
    if not groups:
        return
    stamped = 0
    for code in frappe.get_all("Item", filters={"item_group": ["in", groups], "disabled": 0}, pluck="name"):
        try:
            if inventory.apply_item_gst(code):
                stamped += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"item gst backfill {code}")
    frappe.db.commit()

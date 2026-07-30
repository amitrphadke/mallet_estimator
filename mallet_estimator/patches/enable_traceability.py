import frappe

from mallet_estimator import inventory


def execute():
    """Turn on native traceability for existing Items:
      • Laminate → Batch (dye-lot matching),
      • finished Client SKU articles → Serial No (per-unit warranty/repair trace).
    ERPNext forbids toggling has_batch_no / has_serial_no once an Item has stock
    movements, so skip any Item that already has a Stock Ledger Entry."""
    meta = frappe.get_meta("Item")

    def has_stock(code):
        return bool(frappe.db.exists("Stock Ledger Entry", {"item_code": code}))

    # Batch on laminate
    if frappe.db.exists("Item Group", "Laminate"):
        for code in frappe.get_all("Item", filters={"item_group": "Laminate"}, pluck="name"):
            if has_stock(code):
                continue
            vals = {}
            if meta.has_field("has_batch_no"):
                vals["has_batch_no"] = 1
            if meta.has_field("create_new_batch"):
                vals["create_new_batch"] = 1
            if meta.has_field("batch_number_series"):
                vals["batch_number_series"] = f"{code}-.####"
            if vals:
                frappe.db.set_value("Item", code, vals, update_modified=False)

    # Serial on finished client articles
    if frappe.db.exists("Item Group", inventory.CLIENT_SKU_GROUP):
        for code in frappe.get_all("Item", filters={"item_group": inventory.CLIENT_SKU_GROUP}, pluck="name"):
            if has_stock(code):
                continue
            vals = {}
            if meta.has_field("has_serial_no"):
                vals["has_serial_no"] = 1
            if meta.has_field("serial_no_series"):
                vals["serial_no_series"] = f"{code}-.###"
            if vals:
                frappe.db.set_value("Item", code, vals, update_modified=False)

    frappe.db.commit()

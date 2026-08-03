import frappe


def execute():
    """S9v2 — real laminates ON the material lines replace the slot-map concept:
    drop the Estimate Slot Map doctype and the orphan LAMINATE_*/EBD_* décor
    items from the abandoned approach (nothing references them; skip any with
    stock or prices keyed)."""
    if frappe.db.exists("DocType", "Estimate Slot Map"):
        try:
            frappe.delete_doc("DocType", "Estimate Slot Map", force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "drop Estimate Slot Map")
    for code in frappe.get_all("Item", filters={"name": ["like", "LAMINATE\\_%"]}, pluck="name") \
            + frappe.get_all("Item", filters={"name": ["like", "EBD\\_%"]}, pluck="name"):
        try:
            if frappe.db.exists("Stock Ledger Entry", {"item_code": code}) \
                    or frappe.db.exists("Item Price", {"item_code": code}):
                continue
            frappe.delete_doc("Item", code, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"drop decor item {code}")
    frappe.db.commit()

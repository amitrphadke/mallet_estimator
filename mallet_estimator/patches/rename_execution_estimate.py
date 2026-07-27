import frappe


def execute():
    """Rename the 'Execution Estimate' DocType to 'Estimate' (preserving data),
    before the new doctype JSON is synced from disk."""
    if frappe.db.exists("DocType", "Execution Estimate") and not frappe.db.exists("DocType", "Estimate"):
        frappe.rename_doc("DocType", "Execution Estimate", "Estimate", force=True, ignore_permissions=True)
        frappe.clear_cache()

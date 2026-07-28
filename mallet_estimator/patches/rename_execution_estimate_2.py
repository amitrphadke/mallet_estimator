import frappe


def execute():
    """Ensure 'Execution Estimate' becomes 'Estimate'. Robust to doctype-sync
    order: if the new doctype was already synced from disk, drop the orphaned
    old one; otherwise rename it (preserving any data)."""
    old, new = "Execution Estimate", "Estimate"
    if not frappe.db.exists("DocType", old):
        return
    if not frappe.db.exists("DocType", new):
        frappe.rename_doc("DocType", old, new, force=True)
    else:
        # New already exists (freshly synced, empty). Move any old records over,
        # then drop the old doctype + its table.
        if frappe.db.table_exists("Execution Estimate"):
            for name in frappe.get_all(old, pluck="name"):
                frappe.db.sql(
                    "INSERT IGNORE INTO `tabEstimate` SELECT * FROM `tabExecution Estimate` WHERE name=%s", name
                )
        frappe.delete_doc("DocType", old, force=True, ignore_permissions=True)
        frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabExecution Estimate`")
    frappe.clear_cache()

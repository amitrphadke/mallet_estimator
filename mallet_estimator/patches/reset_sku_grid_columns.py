import frappe


def execute():
    """The Estimate SKU 'Process Steps' grid columns were reworked to be
    workstation-based (Phase | Workstation | Qty | Min/Unit | Total Min | Phase
    Cost). Users who had opened the old grid have a saved per-user column layout
    in __UserSettings that overrides the doctype defaults, so they still see the
    old carpenter/helper columns and NOT Workstation / Phase Cost.

    Clear the saved GridView/list settings for our doctypes so everyone falls
    back to the new defaults. Runs once (tracked in patches.txt); future
    Configure-Columns customisations are preserved."""
    for dt in ("Estimate SKU", "Estimate"):
        try:
            frappe.db.sql("delete from `__UserSettings` where doctype = %s", dt)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"reset grid columns: {dt}")
    frappe.clear_cache()

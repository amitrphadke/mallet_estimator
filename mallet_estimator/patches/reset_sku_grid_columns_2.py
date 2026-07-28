import frappe


def execute():
    """The Process Steps grid dropped the carpenter/helper columns (workstation
    rate now carries the wage). Clear any saved per-user grid layout again so the
    new column set (Phase | Workstation | Qty | Min/Unit | Total Min | Phase Cost)
    shows for everyone. Runs once."""
    for dt in ("Estimate SKU", "Estimate"):
        try:
            frappe.db.sql("delete from `__UserSettings` where doctype = %s", dt)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"reset grid columns 2: {dt}")
    frappe.clear_cache()

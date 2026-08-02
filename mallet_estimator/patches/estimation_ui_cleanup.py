import frappe


def execute():
    """Fix-batch migrate (2026-08-02):
      • clear saved per-user grid layouts + caches so the removed CSV field and
        the old column sets stop appearing anywhere in the UI ("CSV removal is
        not gone fully — remove it from cache");
      • the door/num_doors/num_drawers columns were dropped from Estimate SKU
        (wardrobe-specific, not common attributes) — nothing to migrate, data
        columns simply become unused."""
    for dt in ("Estimate SKU", "Estimate"):
        try:
            frappe.db.sql("delete from `__UserSettings` where doctype = %s", dt)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"estimation_ui_cleanup {dt}")
    frappe.clear_cache()

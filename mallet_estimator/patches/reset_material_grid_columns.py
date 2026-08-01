import frappe


def execute():
    """I2 — the Estimate SKU 'Material Lines' grid shows the non-clickable
    'OpenCutList Code' (Data) instead of the clickable 'Material Item' (Link ->
    Item, which routes to the stock module) because a stale per-user grid layout in
    __UserSettings (saved when the grid was first opened) overrides the doctype
    defaults. Clear it so 'Material Item' shows by default for everyone. Mirrors
    reset_sku_grid_columns; runs once (future Configure-Columns choices persist)."""
    try:
        frappe.db.sql("delete from `__UserSettings` where doctype = %s", "Estimate SKU")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "reset material grid columns")
    frappe.clear_cache()

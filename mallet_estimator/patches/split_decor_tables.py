import frappe


def execute():
    """Décor map split into two tables (laminates / edge bands): move legacy
    'Edge Band' rows out of the laminate table into the new edge table, with
    the ply-driven physical defaults where blank (22 x 0.8 mm standard)."""
    if not frappe.db.table_exists("Estimate SKU Decor Edge"):
        return
    rows = frappe.get_all(
        "Estimate SKU Decor",
        filters={"domain": "Edge Band", "parenttype": "Estimate SKU"},
        fields=["name", "parent", "slot", "brand", "code", "decor_name",
                "year", "short", "thickness", "width"],
    )
    for r in rows:
        try:
            d = frappe.new_doc("Estimate SKU Decor Edge")
            d.parenttype = "Estimate SKU"
            d.parent = r.parent
            d.parentfield = "sku_decor_edges"
            d.slot = r.slot
            d.brand = r.brand
            d.code = r.code
            d.decor_name = r.decor_name
            d.year = r.year
            d.short = r.short
            d.thickness = r.thickness or 0.8
            d.width = r.width or 22
            d.idx = 99
            d.db_insert()
            frappe.delete_doc("Estimate SKU Decor", r.name, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"split decor {r.name}")
    frappe.db.commit()

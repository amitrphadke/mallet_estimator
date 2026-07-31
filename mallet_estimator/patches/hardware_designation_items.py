import frappe

from mallet_estimator import inventory


def execute():
    """F7 + B4.
      • Relabel the Item dimension custom fields (Sheet Length/Width → generic
        Length/Width; section "Dimensions") now that hardware uses them too —
        a label-only update on existing fields, no schema rebuild.
      • B4: backfill the Square Meter (m²) conversion on existing sheet/laminate
        Items that missed it (an earlier fix_material_items version ran before the
        two-save fix). stock_uom is unchanged here, so the conversion sticks.
    Idempotent. NOTE: designation-level hardware Items (HWD_AH_SC_0, …) are created
    when a SKU's Parts CSV is re-imported; this patch does not rewrite existing
    material grids."""
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(inventory.CUSTOM_FIELDS, ignore_validate=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "hardware_designation_items: custom fields")

    if not frappe.db.exists("UOM", "Square Meter"):
        frappe.db.commit()
        return
    for grp in ("Sheet Goods", "Laminate"):
        if not frappe.db.exists("Item Group", grp):
            continue
        for code in frappe.get_all("Item", filters={"item_group": grp}, pluck="name"):
            try:
                item = frappe.get_doc("Item", code)
                if any(r.uom == "Square Meter" for r in item.uoms):
                    continue
                item.append("uoms", {"uom": "Square Meter", "conversion_factor": inventory.SHEET_AREA_SQM})
                item.save(ignore_permissions=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"hardware_designation_items: m2 backfill {code}")
    frappe.db.commit()

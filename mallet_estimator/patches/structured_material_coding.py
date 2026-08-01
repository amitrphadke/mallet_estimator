import frappe

from mallet_estimator import inventory


def execute():
    """F3 — add the structured SG/laminate coding Item fields (visible sides +
    internal/external laminate) and backfill them on existing sheet/laminate Items
    by decoding their OpenCutList code. F2 — seed the Manufacturer / Brand /
    Supplier option pools. Idempotent; safe to re-run."""
    # F3 coding fields (+ any other mallet Item fields not yet present). One ALTER
    # on the small material Item set — light, and runs post-model-sync so the
    # Item DocType schema is already in place.
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(inventory.CUSTOM_FIELDS, ignore_validate=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "structured_material_coding custom fields")

    # F2 vendor option pools.
    try:
        inventory.ensure_vendor_masters()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "structured_material_coding vendor masters")

    meta = frappe.get_meta("Item")
    if not meta.has_field("mallet_visible_sides"):
        return  # field creation failed — nothing to backfill onto

    groups = [g for g in ("Sheet Goods", "Laminate") if frappe.db.exists("Item Group", g)]
    if not groups:
        frappe.db.commit()
        return

    for code in frappe.get_all("Item", filters={"item_group": ["in", groups]}, pluck="name"):
        try:
            src = frappe.db.get_value("Item", code, "mallet_oc_code") or code
            fields = inventory._coding_fields(src)
            item = frappe.get_doc("Item", code)
            changed = False
            for fld, val in fields.items():
                if val is not None and not (item.get(fld) or None):
                    item.set(fld, val)
                    changed = True
            if changed:
                item.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"structured_material_coding backfill {code}")

    frappe.db.commit()

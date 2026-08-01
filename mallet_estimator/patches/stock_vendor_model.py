import frappe

from mallet_estimator import inventory


OEM_SUPPLIER_NAMES = ("Hafele", "Ebco", "Merino", "Royal Touch")


def execute():
    """Wave A (S1/S2/S3): correct the vendor model on staging.
      • add the Manufacturer Part No Item field + Paint group + Litre UOM;
      • seed the 7 real Suppliers (and keep the 4 OEMs as Manufacturer/Brand only);
      • DEMOTE the OEMs wrongly seeded as Suppliers by F2 (delete if unused);
      • attach scope-valid Item Supplier rows to existing material Items;
      • refresh the estimation ceiling from any existing supplier prices.
    Idempotent; each step guarded."""
    # 1. Item custom fields (adds mallet_mfr_part_no + sourcing section).
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(inventory.CUSTOM_FIELDS, ignore_validate=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "stock_vendor_model custom fields")

    # 2. Litre UOM + Paint item group (light; the full masters run on install/setup).
    try:
        if not frappe.db.exists("UOM", "Litre"):
            frappe.get_doc({"doctype": "UOM", "uom_name": "Litre"}).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "stock_vendor_model Litre")
    try:
        parent = inventory.PARENT_GROUP if frappe.db.exists("Item Group", inventory.PARENT_GROUP) else \
            frappe.db.get_value("Item Group", {"is_group": 1}, "name")
        if parent and not frappe.db.exists("Item Group", "Paint"):
            inventory._ensure_group("Paint", parent, is_group=0, result={"item_groups": 0, "errors": []})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "stock_vendor_model Paint group")

    # 3. Correct masters: makers/brands (4 OEMs) + real Suppliers (7 vendors).
    try:
        inventory.ensure_vendor_masters()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "stock_vendor_model vendor masters")

    # 4. Demote the OEMs that F2 wrongly created as Suppliers (only if unused).
    for name in OEM_SUPPLIER_NAMES:
        _delete_supplier_if_unused(name)

    # 5. Attach scope-valid suppliers to existing material Items.
    groups = [g for g in inventory.ITEM_GROUPS if frappe.db.exists("Item Group", g)]
    if groups:
        for code in frappe.get_all("Item", filters={"item_group": ["in", groups]}, pluck="name"):
            try:
                oc = frappe.db.get_value("Item", code, "mallet_oc_code") or code
                inventory.attach_scope_suppliers(code, inventory.kind_for_code(oc))
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"stock_vendor_model suppliers {code}")

    # 6. Refresh the estimation ceiling from any supplier prices already present.
    try:
        inventory.recompute_all_ceilings()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "stock_vendor_model ceilings")

    frappe.db.commit()


def _delete_supplier_if_unused(name):
    doc = inventory.supplier_docname(name)
    # Only demote an OEM that is NOT also a real vendor in our scope.
    if not doc or name in inventory.SUPPLIER_SCOPE:
        return
    for dt in ("Purchase Order", "Purchase Invoice", "Purchase Receipt", "Supplier Quotation"):
        try:
            if frappe.db.exists("DocType", dt) and frappe.db.count(dt, {"supplier": doc}):
                return  # has transactions — leave it
        except Exception:
            return
    try:
        frappe.db.delete("Item Supplier", {"supplier": doc})
    except Exception:
        pass
    try:
        frappe.delete_doc("Supplier", doc, force=True, ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"stock_vendor_model demote {doc}")

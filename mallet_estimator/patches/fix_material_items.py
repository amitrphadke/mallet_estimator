import frappe

from mallet_estimator import inventory

# Manufacturers the shop buys from — seeded so the native Item manufacturer/brand
# fields are ready to use (per-item assignment stays manual / a later mapping).
MANUFACTURERS = ("Hafele", "Ebco", "Merino", "Royal Touch")


def _has_sle(code):
    return bool(frappe.db.exists("Stock Ledger Entry", {"item_code": code}))


def _ensure_uoms():
    """The stock/purchase/area UOMs the re-homed materials need. Cheap + idempotent
    (no schema rebuild) — unlike ensure_inventory_masters which also touches custom
    fields, kept out of migrate to avoid deploy stalls."""
    for uom in ("Sheet", "Meter", "Roll", "Square Meter"):
        if not frappe.db.exists("UOM", uom):
            try:
                d = frappe.new_doc("UOM")
                d.uom_name = uom
                d.insert(ignore_permissions=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"fix_material_items UOM {uom}")


def execute():
    """Repair Items that an older build created in the default group ('Products')
    as non-stock:
      • materials  -> correct Mallet group, stockable + purchasable, right stock/
        purchase UOM conversions, laminate dye-lot batch;
      • finished client articles -> 'Client SKU' group, stockable + sellable, serial.
    Also seed the manufacturer masters. ERPNext forbids changing is_stock_item /
    UOM / batch / serial once stock has moved, so anything with a Stock Ledger
    Entry keeps those untouched (only its group is corrected). Idempotent."""
    meta = frappe.get_meta("Item")
    _ensure_uoms()

    if frappe.db.exists("DocType", "Manufacturer"):
        for mfr in MANUFACTURERS:
            if not frappe.db.exists("Manufacturer", mfr):
                try:
                    d = frappe.new_doc("Manufacturer")
                    d.short_name = mfr
                    d.insert(ignore_permissions=True)
                except Exception:
                    frappe.log_error(frappe.get_traceback(), f"seed manufacturer {mfr}")

    valid_groups = set(inventory.ITEM_GROUPS) | {inventory.CLIENT_SKU_GROUP}

    # finished articles = every Item an Estimate SKU points at (link or its code)
    articles = set()
    for s in frappe.get_all("Estimate SKU", fields=["item", "sku_code"]):
        for c in (s.item, s.sku_code):
            if c and frappe.db.exists("Item", c):
                articles.add(c)

    for code in frappe.get_all(
        "Item", filters={"item_group": ["not in", list(valid_groups)]}, pluck="name"
    ):
        try:
            if inventory.is_material_code(code):
                _fix_material(code, inventory.kind_for_code(code), meta)
            elif code in articles:
                _fix_article(code, meta)
            # else: a non-material, non-article Item (a real Product) — left alone
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"fix_material_items: {code}")

    frappe.db.commit()


def _fix_material(code, kind, meta):
    spec = inventory.KIND_SPEC.get(kind, inventory.KIND_SPEC["hardware"])
    item = frappe.get_doc("Item", code)
    sle = _has_sle(code)

    # --- pass 1: group, flags, stock UOM, batch. Changing stock_uom makes ERPNext
    # reset the uoms table to just the new stock UOM, so the extra conversions are
    # added in pass 2 (below), once stock_uom is settled. ---
    if frappe.db.exists("Item Group", spec["group"]):
        item.item_group = spec["group"]
    item.is_purchase_item = 1
    if not sle:
        item.is_stock_item = 1
        want = spec["stock_uom"]
        if want and frappe.db.exists("UOM", want) and item.stock_uom != want:
            item.stock_uom = want
    if kind == "laminate" and not sle:
        inventory._set(item, meta, "has_batch_no", 1)
        inventory._set(item, meta, "create_new_batch", 1)
        inventory._set(item, meta, "batch_number_series", f"{code}-.####")
    item.save(ignore_permissions=True)

    # --- pass 2: purchase-unit + area conversions (stock_uom is now stable, so
    # ERPNext keeps them) ---
    item.reload()
    changed = False
    pu = spec.get("purchase_uom")
    if pu and pu != item.stock_uom and frappe.db.exists("UOM", pu):
        if not any(r.uom == pu for r in item.uoms):
            item.append("uoms", {"uom": pu, "conversion_factor": spec["conv"]})
            changed = True
        if meta.has_field("purchase_uom") and not item.purchase_uom:
            item.purchase_uom = pu
            changed = True
    if kind in ("sheet", "laminate") and frappe.db.exists("UOM", "Square Meter"):
        if not any(r.uom == "Square Meter" for r in item.uoms):
            item.append("uoms", {"uom": "Square Meter", "conversion_factor": inventory.SHEET_AREA_SQM})
            changed = True
    if changed:
        item.save(ignore_permissions=True)


def _fix_article(code, meta):
    item = frappe.get_doc("Item", code)
    sle = _has_sle(code)
    if frappe.db.exists("Item Group", inventory.CLIENT_SKU_GROUP):
        item.item_group = inventory.CLIENT_SKU_GROUP
    item.is_sales_item = 1
    if not sle:
        item.is_stock_item = 1
        inventory._set(item, meta, "has_serial_no", 1)
        inventory._set(item, meta, "serial_no_series", f"{code}-.###")
    item.save(ignore_permissions=True)

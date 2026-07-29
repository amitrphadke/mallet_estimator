# ---------------------------------------------------------------------------
# Material inventory: turn an OpenCutList material code into a proper, native
# ERPNext stock Item, and pull its unit cost from ERPNext (never from the PDF).
#
# The OpenCutList Estimate PDF / Parts CSV classify WHAT material a design needs
# (code + thickness + quantity). This module makes sure each such material EXISTS
# as an ERPNext Item exactly once (idempotent on item_code), grouped and UOM'd
# correctly, stock-tracked — and returns its cost from valuation / last purchase
# / buying price list / standard rate (in that order).
# ---------------------------------------------------------------------------

import frappe

# Standard sheet size for sheet goods & laminate (mm) — your 1220 x 2440 stock.
SHEET_LENGTH_MM = 2440.0
SHEET_WIDTH_MM = 1220.0

PARENT_GROUP = "Mallet Materials"

# PDF section / code prefix -> (Item Group, stock UOM)
KIND_SPEC = {
    "sheet":       ("Sheet Goods", "Sheet"),
    "laminate":    ("Laminate", "Sheet"),
    "edge":        ("Edge Banding", "Meter"),
    "hardware":    ("Hardware", "Nos"),
    "solidwood":   ("Solid Wood", "Nos"),
    "dimensional": ("Dimensional Lumber", "Nos"),
}
ITEM_GROUPS = [spec[0] for spec in KIND_SPEC.values()]

# OpenCutList code prefixes -> kind (fallback when the PDF section is unknown).
PREFIX_KIND = {
    "SG_": "sheet", "DL_": "laminate", "EB_": "edge",
    "HWD_": "hardware", "SW_": "solidwood",
}


def kind_for_code(code):
    up = str(code or "").upper()
    for prefix, kind in PREFIX_KIND.items():
        if up.startswith(prefix):
            return kind
    return "hardware"


def item_code_for(name, thickness, kind=None):
    """Stable ERPNext item_code for a material — thickness is part of the identity
    for sheet goods/laminate (a 16mm and an 18mm ply are different Items)."""
    kind = kind or kind_for_code(name)
    if kind in ("sheet", "laminate") and thickness:
        return f"{name}_{thickness:g}mm"
    return name


def _fallback_group():
    return (
        frappe.db.get_value("Item Group", {"is_group": 0, "name": PARENT_GROUP}, "name")
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )


def _describe(name, kind, thickness):
    if kind == "sheet":
        return f"{name} — {thickness:g}mm sheet good ({SHEET_LENGTH_MM:g}x{SHEET_WIDTH_MM:g}mm)"
    if kind == "laminate":
        return f"{name} — decorative laminate sheet"
    if kind == "edge":
        return f"{name} — edge banding (per metre)"
    if kind == "solidwood":
        return f"{name} — solid wood"
    return f"{name}"


def material_rate(item_code):
    """(rate, source) for a material Item: valuation -> last purchase -> buying
    Item Price -> standard rate. rate 0 with source 'unset' means not priced yet."""
    v = frappe.db.get_value(
        "Item", item_code, ["valuation_rate", "last_purchase_rate", "standard_rate"], as_dict=True
    ) or {}
    if v.get("valuation_rate"):
        return v["valuation_rate"], "valuation"
    if v.get("last_purchase_rate"):
        return v["last_purchase_rate"], "last purchase"
    price = frappe.db.get_value("Item Price", {"item_code": item_code, "buying": 1}, "price_list_rate")
    if price:
        return price, "price list"
    if v.get("standard_rate"):
        return v["standard_rate"], "standard rate"
    return 0.0, "unset"


def ensure_material_item(name, kind=None, thickness=0, dims=None):
    """Ensure the material exists as one ERPNext stock Item (idempotent on
    item_code), then return (item_code, rate, source). `name` is the OpenCutList
    material code (e.g. SG_PLY_V0_a_a); `dims` optionally {length,width}."""
    kind = kind or kind_for_code(name)
    item_group, uom = KIND_SPEC.get(kind, ("Hardware", "Nos"))
    code = item_code_for(name, thickness, kind)

    if not frappe.db.exists("Item", code):
        meta = frappe.get_meta("Item")
        item = frappe.new_doc("Item")
        item.item_code = code
        item.item_name = (name or code)[:140]
        item.item_group = item_group if frappe.db.exists("Item Group", item_group) else _fallback_group()
        item.stock_uom = uom if frappe.db.exists("UOM", uom) else "Nos"
        item.is_stock_item = 1
        if meta.has_field("include_item_in_manufacturing"):
            item.include_item_in_manufacturing = 1
        item.description = _describe(name, kind, thickness)
        # dimensions
        if kind in ("sheet", "laminate"):
            _set(item, meta, "mallet_sheet_length_mm", (dims or {}).get("length") or SHEET_LENGTH_MM)
            _set(item, meta, "mallet_sheet_width_mm", (dims or {}).get("width") or SHEET_WIDTH_MM)
        if thickness:
            _set(item, meta, "mallet_thickness_mm", thickness)
        _set(item, meta, "mallet_oc_code", name)
        item.insert(ignore_permissions=True)

    rate, source = material_rate(code)
    return code, rate, source


def _set(doc, meta, field, value):
    if meta.has_field(field):
        doc.set(field, value)


# --- masters --------------------------------------------------------------
CUSTOM_FIELDS = {
    "Item": [
        {"fieldname": "mallet_material_sb", "fieldtype": "Section Break",
         "label": "Mallet Material", "insert_after": "stock_uom", "collapsible": 1},
        {"fieldname": "mallet_oc_code", "fieldtype": "Data", "label": "OpenCutList Code",
         "insert_after": "mallet_material_sb", "read_only": 1},
        {"fieldname": "mallet_thickness_mm", "fieldtype": "Float", "label": "Thickness (mm)",
         "insert_after": "mallet_oc_code"},
        {"fieldname": "mallet_material_cb", "fieldtype": "Column Break",
         "insert_after": "mallet_thickness_mm"},
        {"fieldname": "mallet_sheet_length_mm", "fieldtype": "Float", "label": "Sheet Length (mm)",
         "insert_after": "mallet_material_cb"},
        {"fieldname": "mallet_sheet_width_mm", "fieldtype": "Float", "label": "Sheet Width (mm)",
         "insert_after": "mallet_sheet_length_mm"},
    ]
}


def ensure_inventory_masters():
    """Create the material Item Group tree, the Sheet/Meter UOMs and the Item
    custom fields (dimensions + OpenCutList code). Idempotent."""
    result = {"item_groups": 0, "uoms": 0, "custom_fields": 0, "errors": []}

    # UOMs
    for uom in ("Sheet", "Meter"):
        if not frappe.db.exists("UOM", uom):
            try:
                d = frappe.new_doc("UOM")
                d.uom_name = uom
                d.insert(ignore_permissions=True)
                result["uoms"] += 1
            except Exception as exc:
                result["errors"].append(f"UOM {uom}: {exc}")

    # Item Group tree: Mallet Materials -> the 6 material groups
    root = frappe.db.get_value("Item Group", {"is_group": 1}, "name") or "All Item Groups"
    if not frappe.db.exists("Item Group", PARENT_GROUP):
        try:
            g = frappe.new_doc("Item Group")
            g.item_group_name = PARENT_GROUP
            g.is_group = 1
            g.parent_item_group = root
            g.insert(ignore_permissions=True)
            result["item_groups"] += 1
        except Exception as exc:
            result["errors"].append(f"Item Group {PARENT_GROUP}: {exc}")
    parent = PARENT_GROUP if frappe.db.exists("Item Group", PARENT_GROUP) else root
    for grp in ITEM_GROUPS:
        if not frappe.db.exists("Item Group", grp):
            try:
                g = frappe.new_doc("Item Group")
                g.item_group_name = grp
                g.is_group = 0
                g.parent_item_group = parent
                g.insert(ignore_permissions=True)
                result["item_groups"] += 1
            except Exception as exc:
                result["errors"].append(f"Item Group {grp}: {exc}")

    # Custom fields on Item
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
        result["custom_fields"] = len(CUSTOM_FIELDS["Item"])
    except Exception as exc:
        result["errors"].append(f"custom fields: {exc}")

    frappe.db.commit()
    return result

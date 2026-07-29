# ---------------------------------------------------------------------------
# Material inventory: turn an OpenCutList material code into a proper, native
# ERPNext stock Item, and pull its unit cost from ERPNext (never from the PDF).
#
# The OpenCutList Estimate PDF / Parts CSV classify WHAT material a design needs
# (code + thickness + quantity). This module makes sure each such material EXISTS
# as an ERPNext stock Item exactly once (idempotent on item_code), grouped and
# UOM'd the way the trade actually buys/stocks it — and returns its cost from
# valuation / last purchase / buying price list / standard rate (in that order).
# ---------------------------------------------------------------------------

import frappe

# Standard sheet size for sheet goods & laminate (mm) — your 1220 x 2440 stock.
SHEET_LENGTH_MM = 2440.0
SHEET_WIDTH_MM = 1220.0
SHEET_AREA_SQM = (SHEET_LENGTH_MM * SHEET_WIDTH_MM) / 1_000_000.0  # ~2.9768 m²/sheet
EDGE_ROLL_METERS = 50.0  # edge banding is bought in 50 m rolls

PARENT_GROUP = "Mallet Materials"
CLIENT_SKU_GROUP = "Client SKU"  # finished articles per client project (archivable)

# kind -> how ERPNext should hold it.
#   group        : Item Group (under Mallet Materials)
#   stock_uom    : the unit stock/consumption is measured in
#   purchase_uom : the unit it is bought in (with a conversion into stock_uom)
#   conv         : how many stock_uom in one purchase_uom
KIND_SPEC = {
    "sheet":       {"group": "Sheet Goods",       "stock_uom": "Sheet", "purchase_uom": "Sheet", "conv": 1},
    "laminate":    {"group": "Laminate",          "stock_uom": "Sheet", "purchase_uom": "Sheet", "conv": 1},
    "edge":        {"group": "Edge Banding",      "stock_uom": "Meter", "purchase_uom": "Roll",  "conv": EDGE_ROLL_METERS},
    "hardware":    {"group": "Hardware",          "stock_uom": "Nos",   "purchase_uom": "Nos",   "conv": 1},
    "solidwood":   {"group": "Solid Wood",        "stock_uom": "Nos",   "purchase_uom": "Nos",   "conv": 1},
    "dimensional": {"group": "Dimensional Lumber", "stock_uom": "Nos",  "purchase_uom": "Nos",   "conv": 1},
}
ITEM_GROUPS = [spec["group"] for spec in KIND_SPEC.values()]

# OpenCutList code prefixes -> kind. NOTE: laminate ships as SG_LAM_* (a sub-type
# of the SG_ sheet family), so LAM is checked before the plain SG_ = sheet.
def kind_for_code(code):
    up = str(code or "").upper()
    if up.startswith("SG_LAM") or up.startswith("LAM_") or up.startswith("DL_"):
        return "laminate"
    if up.startswith("SG_"):
        return "sheet"
    if up.startswith("EB_"):
        return "edge"
    if up.startswith("HWD_"):
        return "hardware"
    if up.startswith("SW_"):
        return "solidwood"
    if up.startswith("DIM_") or up.startswith("DM_"):
        return "dimensional"
    return "hardware"


def stock_uom_for(kind):
    return KIND_SPEC.get(kind or "hardware", KIND_SPEC["hardware"])["stock_uom"]


def sheet_dims(kind, thickness):
    """(length, width, thickness) in mm for a sheet/laminate line; else (0,0,thickness)."""
    if kind in ("sheet", "laminate"):
        return SHEET_LENGTH_MM, SHEET_WIDTH_MM, (thickness or 0)
    return 0, 0, (thickness or 0)


def item_code_for(name, thickness, kind=None):
    """Stable ERPNext item_code. Thickness is part of the identity for sheet goods
    (a 16mm and 18mm ply are different Items) UNLESS the code already carries it."""
    kind = kind or kind_for_code(name)
    if kind == "sheet" and thickness and "mm" not in str(name).lower():
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
        return f"{name} — {thickness:g}mm sheet good ({SHEET_LENGTH_MM:g}x{SHEET_WIDTH_MM:g}mm sheet)"
    if kind == "laminate":
        return f"{name} — decorative laminate sheet ({SHEET_LENGTH_MM:g}x{SHEET_WIDTH_MM:g}mm)"
    if kind == "edge":
        return f"{name} — edge banding (stocked per metre; bought in {EDGE_ROLL_METERS:g} m rolls)"
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
    item_code), with the right group, stock UOM, purchase UOM + conversion, and
    dimensions. Returns (item_code, rate, source). `name` is the OpenCutList code."""
    kind = kind or kind_for_code(name)
    spec = KIND_SPEC.get(kind, KIND_SPEC["hardware"])
    code = item_code_for(name, thickness, kind)

    if not frappe.db.exists("Item", code):
        meta = frappe.get_meta("Item")
        item = frappe.new_doc("Item")
        item.item_code = code
        item.item_name = (name or code)[:140]
        item.item_group = spec["group"] if frappe.db.exists("Item Group", spec["group"]) else _fallback_group()
        item.stock_uom = spec["stock_uom"] if frappe.db.exists("UOM", spec["stock_uom"]) else "Nos"
        item.is_stock_item = 1
        item.is_purchase_item = 1
        if meta.has_field("include_item_in_manufacturing"):
            item.include_item_in_manufacturing = 1
        # buy in a different unit (e.g. Roll of 50 m) with a conversion
        pu = spec.get("purchase_uom")
        if pu and pu != spec["stock_uom"] and frappe.db.exists("UOM", pu) and meta.has_field("purchase_uom"):
            item.purchase_uom = pu
        # UOM conversion table: 1 stock_uom = 1 stock_uom, plus purchase + area
        _add_uom(item, spec["stock_uom"], 1)
        if pu and pu != spec["stock_uom"] and frappe.db.exists("UOM", pu):
            _add_uom(item, pu, spec["conv"])
        if kind in ("sheet", "laminate") and frappe.db.exists("UOM", "Square Meter"):
            _add_uom(item, "Square Meter", SHEET_AREA_SQM)  # 1 Sheet = ~2.98 m²
        item.description = _describe(name, kind, thickness)
        if kind in ("sheet", "laminate"):
            _set(item, meta, "mallet_sheet_length_mm", (dims or {}).get("length") or SHEET_LENGTH_MM)
            _set(item, meta, "mallet_sheet_width_mm", (dims or {}).get("width") or SHEET_WIDTH_MM)
        if thickness:
            _set(item, meta, "mallet_thickness_mm", thickness)
        _set(item, meta, "mallet_oc_code", name)
        item.insert(ignore_permissions=True)

    rate, source = material_rate(code)
    return code, rate, source


def _add_uom(item, uom, factor):
    if not any((r.uom == uom) for r in (item.get("uoms") or [])):
        item.append("uoms", {"uom": uom, "conversion_factor": factor})


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
    """UOMs, the material Item Group tree, the Client SKU group and the Item
    custom fields. Idempotent."""
    result = {"item_groups": 0, "uoms": 0, "custom_fields": 0, "errors": []}

    for uom in ("Sheet", "Meter", "Roll", "Square Meter"):
        if not frappe.db.exists("UOM", uom):
            try:
                d = frappe.new_doc("UOM")
                d.uom_name = uom
                d.insert(ignore_permissions=True)
                result["uoms"] += 1
            except Exception as exc:
                result["errors"].append(f"UOM {uom}: {exc}")

    # Parent the tree under the site's root Item Group; create the root if the
    # site has none (bare ERPNext installs lack the wizard-seeded Item Group tree).
    root = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]}, "name") \
        or frappe.db.get_value("Item Group", {"is_group": 1}, "name")
    if not root:
        try:
            r = frappe.new_doc("Item Group")
            r.item_group_name = "All Item Groups"
            r.is_group = 1
            r.insert(ignore_permissions=True)
            root = r.name
            result["item_groups"] += 1
        except Exception as exc:
            result["errors"].append(f"root Item Group: {exc}")
            root = "All Item Groups"

    # Raw-material tree: Mallet Materials -> the 6 material groups
    _ensure_group(PARENT_GROUP, root, is_group=1, result=result)
    parent = PARENT_GROUP if frappe.db.exists("Item Group", PARENT_GROUP) else root
    for grp in ITEM_GROUPS:
        _ensure_group(grp, parent, is_group=0, result=result)

    # Finished client articles live in their own group so they never mix with
    # regular products and can be archived when a project closes.
    _ensure_group(CLIENT_SKU_GROUP, root, is_group=0, result=result)

    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
        result["custom_fields"] = len(CUSTOM_FIELDS["Item"])
    except Exception as exc:
        result["errors"].append(f"custom fields: {exc}")

    frappe.db.commit()
    return result


def _ensure_group(name, parent, is_group, result):
    if frappe.db.exists("Item Group", name):
        return
    try:
        g = frappe.new_doc("Item Group")
        g.item_group_name = name
        g.is_group = 1 if is_group else 0
        g.parent_item_group = parent
        g.insert(ignore_permissions=True)
        result["item_groups"] += 1
    except Exception as exc:
        result["errors"].append(f"Item Group {name}: {exc}")


# --- warehouses -----------------------------------------------------------
# Mirrors the physical factory: raw-material store (sheet/board racks + hardware
# racks), work-in-progress (cut-part tables, assembly area, project room),
# finished goods (packed/dispatch racks) and a customer-provided store for the
# occasional client-shipped plywood/laminate.
WAREHOUSE_TREE = {
    "Raw Materials": {
        "is_group": 1,
        "children": ["Board & Sheet Store", "Hardware Store"],
    },
    "Work In Progress": {
        "is_group": 1,
        "children": ["Cut Parts - Table 1", "Cut Parts - Table 2", "Assembly Area", "Project Room"],
    },
    "Finished Goods": {
        "is_group": 1,
        "children": ["Packed / Dispatch"],
    },
    "Customer Provided": {"is_group": 0, "children": []},
}


def _default_company():
    return (
        frappe.defaults.get_user_default("Company")
        or frappe.db.get_default("company")
        or frappe.db.get_value("Company", {}, "name")
    )


def ensure_warehouses(company=None):
    """Create the factory warehouse tree (native Warehouse doctype). Idempotent."""
    result = {"warehouses": 0, "errors": []}
    company = company or _default_company()
    if not company:
        result["errors"].append("no Company found")
        return result
    abbr = frappe.db.get_value("Company", company, "abbr")
    root = (
        frappe.db.get_value("Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["is", "not set"]}, "name")
        or frappe.db.get_value("Warehouse", {"company": company, "is_group": 1}, "name")
        or f"All Warehouses - {abbr}"
    )

    def wh(name, is_group, parent):
        full = f"{name} - {abbr}"
        if frappe.db.exists("Warehouse", full):
            return full
        try:
            w = frappe.new_doc("Warehouse")
            w.warehouse_name = name
            w.company = company
            w.is_group = 1 if is_group else 0
            w.parent_warehouse = parent
            w.insert(ignore_permissions=True)
            result["warehouses"] += 1
            return w.name
        except Exception as exc:
            result["errors"].append(f"Warehouse {name}: {exc}")
            return None

    for group, spec in WAREHOUSE_TREE.items():
        gname = wh(group, spec["is_group"], root)
        for child in spec["children"]:
            wh(child, False, gname or root)

    frappe.db.commit()
    return result

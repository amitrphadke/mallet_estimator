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

import re

import frappe

# Standard sheet size for sheet goods & laminate (mm) — your 1220 x 2440 stock.
SHEET_LENGTH_MM = 2440.0
SHEET_WIDTH_MM = 1220.0
SHEET_AREA_SQM = (SHEET_LENGTH_MM * SHEET_WIDTH_MM) / 1_000_000.0  # ~2.9768 m²/sheet
EDGE_ROLL_METERS = 50.0  # edge banding is bought in 50 m rolls

PARENT_GROUP = "Mallet Materials"
CLIENT_SKU_GROUP = "Client SKU"  # finished articles per client project (archivable)

# S1 — makers vs vendors are DIFFERENT (corrects F2, which wrongly seeded the OEMs
# as Suppliers). Manufacturers (OEM) make the goods; Suppliers are the vendors the
# shop actually buys from. One technical Item carries its Manufacturer + many
# Supplier prices — all native.
#   MANUFACTURERS: maker -> the material kind they make (used to default the Item's
#   manufacturer). BRAND_NAMES mirror the maker names.
MANUFACTURERS = {"Hafele": "hardware", "Ebco": "hardware", "Merino": "laminate", "Royal Touch": "laminate", "Virgo Mica": "laminate"}
BRAND_NAMES = tuple(MANUFACTURERS)
# Back-compat: the 4 maker/brand names verify_setup still asserts.
VENDOR_NAMES = tuple(MANUFACTURERS)

# The real vendors, and which material kinds each is allowed to supply. Drives the
# Item Supplier rows so a PO only offers a supplier who actually sells that item.
SUPPLIER_SCOPE = {
    # J1 — "all hardware or ply suppliers supply Fevicol and abrotape" → joinery.
    "SAI Ply":        {"sheet", "laminate", "edge", "hardware", "joinery"},
    "Shanti":         {"sheet", "laminate", "edge", "hardware", "joinery"},
    "EdgeIndia":      {"edge"},
    "Vibrant Ply":    {"sheet", "laminate"},
    "Sun Tradelink":  {"hardware", "joinery"},
    "Lotus Hardware": {"hardware", "joinery"},
    "Lotus Paint":    {"paint"},
}

# F5 — assumed-vs-actual pricing. The estimate values material at a deliberate
# *planning* rate held on this Buying price list, kept separate from live
# procurement prices (valuation / last purchase / vendor buying lists). An
# Item Price on this list wins for estimation; real purchase prices are what
# procurement negotiates and pays (visible as Project margin variance).
ESTIMATION_PRICE_LIST = "Estimation (Assumed)"

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
    "joinery":     {"group": "Joinery Hardware",  "stock_uom": "Nos",   "purchase_uom": "Nos",   "conv": 1},
    "paint":       {"group": "Paint",             "stock_uom": "Litre", "purchase_uom": "Litre", "conv": 1},
    "solidwood":   {"group": "Solid Wood",        "stock_uom": "Nos",   "purchase_uom": "Nos",   "conv": 1},
    "dimensional": {"group": "Dimensional Lumber", "stock_uom": "Nos",  "purchase_uom": "Nos",   "conv": 1},
}
# C1 — hardware splits into two shopper-facing groups: what the CLIENT selects
# (hinges/rails/handles/locks/lifts) vs the JOINERY the shop consumes
# (screws/minifix/shelf supports/fevicol/abrotape).
CLIENT_HW_GROUP = "Client Hardware"
JOINERY_GROUP = "Joinery Hardware"
_JOINERY_TOKENS = ("SCREW", "MINIFIX", "SHELFSUPPORT", "FEVICOL", "ABROTAPE")
ITEM_GROUPS = [spec["group"] for spec in KIND_SPEC.values()] + [CLIENT_HW_GROUP]


def hardware_group(code):
    """C1 — which group a HWD_/JH_ item belongs to: joinery consumables vs
    client-selectable hardware."""
    up = str(code or "").upper()
    if up.startswith("JH_") or any(t in up for t in _JOINERY_TOKENS):
        return JOINERY_GROUP
    return CLIENT_HW_GROUP

# Raw-material code families. Used to tell a genuine material apart from a
# finished article or a real Product, so re-homing never misfiles a non-material.
MATERIAL_PREFIXES = ("SG_LAM", "LAM_", "DL_", "SG_", "EB_", "HWD_", "JH_", "SW_", "DIM_", "DM_", "PT_", "PAINT")


def is_material_code(code):
    up = str(code or "").upper()
    return any(up.startswith(p) for p in MATERIAL_PREFIXES)


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
    if up.startswith("JH_"):
        return "joinery"
    if up.startswith("PT_") or up.startswith("PAINT"):
        return "paint"
    if up.startswith("SW_"):
        return "solidwood"
    if up.startswith("DIM_") or up.startswith("DM_"):
        return "dimensional"
    return "hardware"


def stock_uom_for(kind):
    return KIND_SPEC.get(kind or "hardware", KIND_SPEC["hardware"])["stock_uom"]


def parse_material_code(name):
    """F3 — decode a sheet/laminate code into its structural attributes:
      SG_PLY_V{v}_{int}_{ext}[_{th}mm]   (plywood core)
      SG_LAM_V{v}_{th}mm_{int}_{ext}     (laminate sheet)
    Returns {visible_sides:int|None, lam_internal:str|None, lam_external:str|None}.
    Tolerant: finds the V{n} token, then the first two non-thickness tokens after
    it are the internal/external laminate codes. Empty dict for non-SG codes."""
    out = {"visible_sides": None, "lam_internal": None, "lam_external": None}
    tokens = str(name or "").split("_")
    for i, t in enumerate(tokens):
        m = re.fullmatch(r"[Vv](\d+)", t)
        if not m:
            continue
        out["visible_sides"] = int(m.group(1))
        rest = [x for x in tokens[i + 1:] if not re.fullmatch(r"\d+mm", x, re.I)]
        if len(rest) >= 1:
            out["lam_internal"] = rest[0]
        if len(rest) >= 2:
            out["lam_external"] = rest[1]
        break
    return out


def _coding_fields(name):
    """parse_material_code() mapped onto the Item custom-field names."""
    p = parse_material_code(name)
    return {
        "mallet_visible_sides": p["visible_sides"],
        "mallet_lam_internal": p["lam_internal"],
        "mallet_lam_external": p["lam_external"],
    }


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


def _describe(name, kind, thickness, category=None):
    if kind == "sheet":
        return f"{name} — {thickness:g}mm sheet good ({SHEET_LENGTH_MM:g}x{SHEET_WIDTH_MM:g}mm sheet)"
    if kind == "laminate":
        return f"{name} — decorative laminate sheet ({SHEET_LENGTH_MM:g}x{SHEET_WIDTH_MM:g}mm)"
    if kind == "edge":
        return f"{name} — edge banding (stocked per metre; bought in {EDGE_ROLL_METERS:g} m rolls)"
    if kind == "solidwood":
        return f"{name} — solid wood"
    if kind == "hardware" and category and category != name:
        return f"{name} ({category})"
    return f"{name}"


def material_rate(item_code):
    """(rate, source) for a material Item, in estimation priority order:
    Estimation (Assumed) price list -> valuation -> last purchase -> any buying
    Item Price -> standard rate. The assumed planning rate wins so estimates use a
    deliberate number, not whatever valuation happens to be. rate 0 with source
    'unset' means not priced yet."""
    # F5: the deliberate assumed planning rate takes precedence for estimation.
    assumed = frappe.db.get_value(
        "Item Price", {"item_code": item_code, "price_list": ESTIMATION_PRICE_LIST}, "price_list_rate"
    )
    if assumed:
        return assumed, "assumed"
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


def ensure_pricing_masters():
    """F5 — create the 'Estimation (Assumed)' Buying price list that holds the
    planning rates the estimate reads from. Idempotent; never clobbers an existing
    one. Returns a small summary."""
    result = {"price_list": 0, "errors": []}
    if not frappe.db.exists("DocType", "Price List"):
        return result
    if not frappe.db.exists("Price List", ESTIMATION_PRICE_LIST):
        try:
            currency = frappe.db.get_default("currency") or "INR"
            pl = frappe.new_doc("Price List")
            pl.price_list_name = ESTIMATION_PRICE_LIST
            pl.buying = 1
            pl.selling = 0
            pl.enabled = 1
            pl.currency = currency
            pl.insert(ignore_permissions=True)
            result["price_list"] = 1
        except Exception as exc:
            result["errors"].append(f"Price List {ESTIMATION_PRICE_LIST}: {exc}")
    frappe.db.commit()
    return result


def set_assumed_rate(item_code, rate):
    """Upsert the assumed planning rate (Item Price on ESTIMATION_PRICE_LIST) for
    an Item. Used by F4 'apply choices' and by manual price maintenance."""
    ensure_pricing_masters()
    name = frappe.db.get_value(
        "Item Price", {"item_code": item_code, "price_list": ESTIMATION_PRICE_LIST}, "name"
    )
    if name:
        frappe.db.set_value("Item Price", name, "price_list_rate", rate)
        return name
    doc = frappe.new_doc("Item Price")
    doc.item_code = item_code
    doc.price_list = ESTIMATION_PRICE_LIST
    doc.buying = 1
    doc.price_list_rate = rate
    doc.insert(ignore_permissions=True)
    return doc.name


def _default_buying_price_list():
    """The site's real buying price list for procurement actuals — Buying Settings
    default, else 'Standard Buying', else any enabled buying list."""
    pl = frappe.db.get_single_value("Buying Settings", "buying_price_list") \
        if frappe.db.exists("DocType", "Buying Settings") else None
    if pl and frappe.db.exists("Price List", pl):
        return pl
    if frappe.db.exists("Price List", "Standard Buying"):
        return "Standard Buying"
    return frappe.db.get_value("Price List", {"buying": 1, "enabled": 1}, "name")


def set_actual_buying_rate(item_code, rate):
    """Upsert the ACTUAL negotiated rate on the real buying price list (kept
    separate from the assumed planning rate, so Project margin shows the variance)."""
    pl = _default_buying_price_list()
    if not pl:
        return None
    name = frappe.db.get_value(
        "Item Price", {"item_code": item_code, "price_list": pl}, "name"
    )
    if name:
        frappe.db.set_value("Item Price", name, "price_list_rate", rate)
        return name
    doc = frappe.new_doc("Item Price")
    doc.item_code = item_code
    doc.price_list = pl
    doc.buying = 1
    doc.price_list_rate = rate
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_item_supplier(item_code, supplier, part_no=None):
    """Record a vendor on the Item's supplier list (native Item Supplier),
    idempotent — the dedupe compares case-insensitively so a casing difference
    can never append duplicates again."""
    supplier = supplier_docname(supplier) or supplier
    item = frappe.get_doc("Item", item_code)
    if not item.meta.has_field("supplier_items"):
        return
    for s in item.get("supplier_items") or []:
        if (s.supplier or "").lower() == (supplier or "").lower():
            if part_no and not s.supplier_part_no:
                s.supplier_part_no = part_no
                item.save(ignore_permissions=True)
            return
    row = {"supplier": supplier}
    if part_no:
        row["supplier_part_no"] = part_no
    item.append("supplier_items", row)
    item.save(ignore_permissions=True)


# --- F2: makers, brands, vendors + many prices per item -------------------
def _default_supplier_group():
    """A leaf Supplier Group for seeded vendors — an existing non-group leaf, else
    create 'Local' under the root. Creates the root Supplier Group too if the site
    has none (a bare erpnext install without the wizard-seeded tree)."""
    if not frappe.db.exists("DocType", "Supplier Group"):
        return None
    leaf = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if leaf:
        return leaf
    root = frappe.db.get_value("Supplier Group", {"is_group": 1, "parent_supplier_group": ["in", ["", None]]}, "name") \
        or frappe.db.get_value("Supplier Group", {"is_group": 1}, "name")
    if not root:
        try:
            r = frappe.new_doc("Supplier Group")
            r.supplier_group_name = "All Supplier Groups"
            r.is_group = 1
            r.insert(ignore_permissions=True)
            root = r.name
        except Exception:
            frappe.log_error(frappe.get_traceback(), "mallet_estimator seed root Supplier Group")
            return None
    if not frappe.db.exists("Supplier Group", "Local"):
        try:
            g = frappe.new_doc("Supplier Group")
            g.supplier_group_name = "Local"
            g.parent_supplier_group = root
            g.insert(ignore_permissions=True)
            return "Local"
        except Exception:
            frappe.log_error(frappe.get_traceback(), "mallet_estimator seed Supplier Group")
    return root


def ensure_vendor_masters():
    """S1 — seed the native masters, with makers and vendors kept SEPARATE:
      • Manufacturer + Brand  = the 4 OEMs (Hafele/Ebco/Merino/Royal Touch)
      • Supplier              = the 7 real vendors the shop buys from
    Idempotent; never clobbers an existing record."""
    result = {"manufacturers": 0, "brands": 0, "suppliers": 0, "errors": []}
    for name in MANUFACTURERS:
        try:
            if frappe.db.exists("DocType", "Manufacturer") and not frappe.db.exists("Manufacturer", name):
                d = frappe.new_doc("Manufacturer")
                d.short_name = name
                d.insert(ignore_permissions=True)
                result["manufacturers"] += 1
        except Exception as exc:
            result["errors"].append(f"Manufacturer {name}: {exc}")
        try:
            if frappe.db.exists("DocType", "Brand") and not frappe.db.exists("Brand", name):
                d = frappe.new_doc("Brand")
                d.brand = name
                d.insert(ignore_permissions=True)
                result["brands"] += 1
        except Exception as exc:
            result["errors"].append(f"Brand {name}: {exc}")
    sg = _default_supplier_group() if frappe.db.exists("DocType", "Supplier") else None
    for name in SUPPLIER_SCOPE:
        try:
            # existence by supplier_name — the docname may be a series, not the name.
            if sg and not supplier_docname(name):
                d = frappe.new_doc("Supplier")
                d.supplier_name = name
                d.supplier_group = sg
                d.insert(ignore_permissions=True)
                result["suppliers"] += 1
        except Exception as exc:
            result["errors"].append(f"Supplier {name}: {exc}")
    frappe.db.commit()
    return result


def suppliers_for_kind(kind):
    """S2 — the seeded vendors allowed to supply this material kind."""
    return [s for s, scope in SUPPLIER_SCOPE.items() if kind in scope]


def supplier_docname(name):
    """Resolve a vendor's human name to its STORED Supplier docname. Case matters:
    frappe.db.exists returns the QUERIED string, and MySQL matches
    case-insensitively — so 'SAI Ply' 'exists' even when the doc is 'Sai Ply'.
    Using the queried casing broke Item Supplier dedupe (rows piled up on every
    import). Always return the docname as stored in the DB."""
    if not name:
        return None
    return frappe.db.get_value("Supplier", {"name": name}, "name") \
        or frappe.db.get_value("Supplier", {"supplier_name": name}, "name")


def attach_scope_suppliers(item_code, kind):
    """S2 — attach an Item Supplier row for every vendor whose scope covers this
    kind, so a PO only offers valid suppliers for the item. Idempotent."""
    for s in suppliers_for_kind(kind):
        doc = supplier_docname(s)
        if doc:
            _ensure_item_supplier(item_code, doc)


def recompute_estimation_ceiling(item_code, price_list=None):
    """S4 — set the item's Estimation (Assumed) rate to the MAX buying rate across
    all its supplier prices, so an estimate never quotes below any actual supplier
    price (purchase = MRP − discount ≤ MRP ≤ estimate). Returns the ceiling or None."""
    pl = price_list or _default_buying_price_list()
    if not pl:
        return None
    rates = [r for r in frappe.get_all(
        "Item Price", filters={"item_code": item_code, "price_list": pl}, pluck="price_list_rate"
    ) if r]
    if not rates:
        return None
    ceiling = max(rates)
    set_assumed_rate(item_code, ceiling)
    return ceiling


@frappe.whitelist()
def recompute_all_ceilings():
    """S4 — refresh the estimation ceiling for every material Item from its current
    supplier prices. Callable from the Estimate Settings 'setup' button / console."""
    if not frappe.has_permission("Item", "read"):
        frappe.throw("Not permitted")
    pl = _default_buying_price_list()
    done = 0
    for code in set(frappe.get_all("Item Price", filters={"price_list": pl}, pluck="item_code")):
        if recompute_estimation_ceiling(code, pl) is not None:
            done += 1
    frappe.db.commit()
    return {"items_repriced": done}


def set_vendor_price(item_code, supplier, rate, price_list=None):
    """F2 — upsert a buying Item Price for a specific (item, supplier) so the same
    Item carries many vendor prices. Falls back to a supplier-less price when the
    site has no Supplier record for that name."""
    pl = price_list or _default_buying_price_list()
    if not pl:
        return None
    supplier = supplier_docname(supplier) or supplier
    has_supplier = supplier and frappe.db.exists("Supplier", supplier)
    flt = {"item_code": item_code, "price_list": pl}
    flt["supplier"] = supplier if has_supplier else ["in", ["", None]]
    name = frappe.db.get_value("Item Price", flt, "name")
    if name:
        frappe.db.set_value("Item Price", name, "price_list_rate", rate)
    else:
        doc = frappe.new_doc("Item Price")
        doc.item_code = item_code
        doc.price_list = pl
        doc.buying = 1
        if has_supplier:
            doc.supplier = supplier
        doc.price_list_rate = rate
        doc.insert(ignore_permissions=True)
        name = doc.name
    # S4 — keep the estimation ceiling = max supplier MRP for this item.
    recompute_estimation_ceiling(item_code, pl)
    return name


@frappe.whitelist()
def set_item_manufacturer(item_code, manufacturer=None, brand=None, part_no=None):
    """F2 — set an Item's native maker + brand (default_item_manufacturer / brand)
    and append an Item Manufacturer row. All fields optional; idempotent."""
    if not frappe.has_permission("Item", "write"):
        frappe.throw("Not permitted")
    item = frappe.get_doc("Item", item_code)
    meta = item.meta
    if manufacturer and frappe.db.exists("Manufacturer", manufacturer):
        _set(item, meta, "default_item_manufacturer", manufacturer)
        if part_no:
            _set(item, meta, "default_manufacturer_part_no", part_no)
    if brand and frappe.db.exists("Brand", brand):
        _set(item, meta, "brand", brand)
    item.save(ignore_permissions=True)
    # Item Manufacturer (multiple makers of the same spec) — separate doctype.
    if manufacturer and frappe.db.exists("Manufacturer", manufacturer) \
            and frappe.db.exists("DocType", "Item Manufacturer") \
            and not frappe.db.exists("Item Manufacturer", {"item_code": item_code, "manufacturer": manufacturer}):
        im = frappe.new_doc("Item Manufacturer")
        im.item_code = item_code
        im.manufacturer = manufacturer
        if part_no:
            im.manufacturer_part_no = part_no
        im.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"item": item_code, "manufacturer": manufacturer, "brand": brand}


@frappe.whitelist()
def refresh_material_choices(project):
    """F4 — fill assumed rate + variance on every Project Material Choice row from
    the current planning rates. Read-only helper: no procurement side effects."""
    if not frappe.has_permission("Project", "write"):
        frappe.throw("Not permitted")
    doc = frappe.get_doc("Project", project)
    rows = doc.get("mallet_material_choices") or []
    for r in rows:
        if r.chosen_item:
            rate, _src = material_rate(r.chosen_item)
            r.assumed_rate = rate
            r.variance = (r.actual_rate or 0) - (rate or 0)
    doc.save(ignore_permissions=True)
    return {"rows": len(rows)}


@frappe.whitelist()
def apply_material_choices(project):
    """F4 — push each choice's ACTUAL negotiated rate onto the real buying price
    list and record its vendor as an Item Supplier, so procurement (PO) uses the
    chosen price. The assumed planning rate is left untouched — the estimate keeps
    valuing at assumed and Project margin shows the assumed-vs-actual variance.
    Idempotent + reversible (delete the buying Item Price to undo)."""
    if not frappe.has_permission("Project", "write"):
        frappe.throw("Not permitted")
    doc = frappe.get_doc("Project", project)
    applied, skipped = [], []
    for r in doc.get("mallet_material_choices") or []:
        if not r.chosen_item or not (r.actual_rate or 0):
            skipped.append(r.code or r.chosen_item or "?")
            continue
        if r.vendor:
            # Per-vendor buying price (F2: same item, many vendor prices) + Item Supplier.
            set_vendor_price(r.chosen_item, r.vendor, r.actual_rate)
            _ensure_item_supplier(r.chosen_item, r.vendor)
        else:
            set_actual_buying_rate(r.chosen_item, r.actual_rate)
        applied.append(r.chosen_item)
    frappe.db.commit()
    return {"applied": applied, "skipped": skipped}


def ensure_material_item(name, kind=None, thickness=0, dims=None):
    """Ensure the material exists as one ERPNext stock Item (idempotent on
    item_code), with the right group, stock UOM, purchase UOM + conversion, and
    dimensions. Returns (item_code, rate, source). `name` is the OpenCutList code."""
    kind = kind or kind_for_code(name)
    spec = KIND_SPEC.get(kind, KIND_SPEC["hardware"])
    code = item_code_for(name, thickness, kind)
    # C1 — hardware/joinery items land in their shopper-facing group.
    group = hardware_group(code) if kind in ("hardware", "joinery") else spec["group"]

    if not frappe.db.exists("Item", code):
        meta = frappe.get_meta("Item")
        item = frappe.new_doc("Item")
        item.item_code = code
        item.item_name = (name or code)[:140]
        item.item_group = group if frappe.db.exists("Item Group", group) else \
            (spec["group"] if frappe.db.exists("Item Group", spec["group"]) else _fallback_group())
        item.stock_uom = spec["stock_uom"] if frappe.db.exists("UOM", spec["stock_uom"]) else "Nos"
        item.is_stock_item = 1
        item.is_purchase_item = 1
        if meta.has_field("include_item_in_manufacturing"):
            item.include_item_in_manufacturing = 1
        # Laminates carry a dye-lot: batch-track so a project can be matched to one
        # lot and avoid visible shade mismatch.
        if kind == "laminate":
            _set(item, meta, "has_batch_no", 1)
            _set(item, meta, "create_new_batch", 1)
            _set(item, meta, "batch_number_series", f"{code}-.####")
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
        item.description = _describe(name, kind, thickness, category=(dims or {}).get("category"))
        if kind in ("sheet", "laminate"):
            _set(item, meta, "mallet_sheet_length_mm", (dims or {}).get("length") or SHEET_LENGTH_MM)
            _set(item, meta, "mallet_sheet_width_mm", (dims or {}).get("width") or SHEET_WIDTH_MM)
            # F3 — decode V{n} + internal/external laminate into filterable fields.
            for fld, val in _coding_fields(name).items():
                if val is not None:
                    _set(item, meta, fld, val)
        elif kind == "hardware" and dims:
            # A hinge/handle/rail has a real physical size (from the part) — store
            # it in the same generic Length/Width fields (these are not "sheet" sizes).
            _set(item, meta, "mallet_sheet_length_mm", dims.get("length") or 0)
            _set(item, meta, "mallet_sheet_width_mm", dims.get("width") or 0)
        if thickness:
            _set(item, meta, "mallet_thickness_mm", thickness)
        _set(item, meta, "mallet_oc_code", name)
        item.insert(ignore_permissions=True)
    elif kind == "hardware" and dims:
        # F7a: a hardware Item created earlier (its designation matched an old
        # generic name, e.g. HWD_MiniFix) has no dimensions — backfill them from
        # the part now, without clobbering any value already set.
        _backfill_hardware_dims(code, dims)

    # S2 — attach the vendors allowed to supply this kind (idempotent).
    attach_scope_suppliers(code, kind)

    rate, source = material_rate(code)
    return code, rate, source


def ensure_catalogue_item(part_no, description=None, manufacturer=None, kind="hardware", item_group=None):
    """S3/S6 — create or enrich a vendor-catalogue Item keyed by its manufacturer
    part number (e.g. a Hafele hardware code 'H-311.01.357'). Sets group, stock
    flags, manufacturer + brand + part no + description, and attaches the vendors
    allowed to supply this kind. Never clobbers a value already set. Returns the
    item_code."""
    code = str(part_no or "").strip()
    if not code:
        return None
    spec = KIND_SPEC.get(kind, KIND_SPEC["hardware"])
    group = item_group or spec["group"]
    meta = frappe.get_meta("Item")
    is_mfr = manufacturer and frappe.db.exists("Manufacturer", manufacturer)
    is_brand = manufacturer and frappe.db.exists("Brand", manufacturer)

    if not frappe.db.exists("Item", code):
        item = frappe.new_doc("Item")
        item.item_code = code
        item.item_name = (description or code)[:140]
        item.item_group = group if frappe.db.exists("Item Group", group) else _fallback_group()
        item.stock_uom = spec["stock_uom"] if frappe.db.exists("UOM", spec["stock_uom"]) else "Nos"
        item.is_stock_item = 1
        item.is_purchase_item = 1
        if meta.has_field("include_item_in_manufacturing"):
            item.include_item_in_manufacturing = 1
        if description:
            item.description = description
        _set(item, meta, "mallet_mfr_part_no", code)
        if is_mfr:
            _set(item, meta, "default_item_manufacturer", manufacturer)
            _set(item, meta, "default_manufacturer_part_no", code)
        if is_brand:
            _set(item, meta, "brand", manufacturer)
        item.insert(ignore_permissions=True)
    else:
        item = frappe.get_doc("Item", code)
        changed = False
        if description and not (item.description or "").strip():
            item.description = description
            changed = True
        if meta.has_field("mallet_mfr_part_no") and not item.get("mallet_mfr_part_no"):
            item.mallet_mfr_part_no = code
            changed = True
        if is_mfr and meta.has_field("default_item_manufacturer") and not item.get("default_item_manufacturer"):
            item.default_item_manufacturer = manufacturer
            _set(item, meta, "default_manufacturer_part_no", code)
            changed = True
        if is_brand and meta.has_field("brand") and not item.get("brand"):
            item.brand = manufacturer
            changed = True
        if changed:
            item.save(ignore_permissions=True)

    attach_scope_suppliers(code, kind)
    return code


def _backfill_hardware_dims(code, dims):
    meta = frappe.get_meta("Item")
    item = frappe.get_doc("Item", code)
    changed = False
    for fld, val in (
        ("mallet_sheet_length_mm", (dims or {}).get("length")),
        ("mallet_sheet_width_mm", (dims or {}).get("width")),
        ("mallet_thickness_mm", (dims or {}).get("thickness")),
    ):
        if val and meta.has_field(fld) and not (item.get(fld) or 0):
            item.set(fld, val)
            changed = True
    if changed:
        item.save(ignore_permissions=True)


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
         "label": "Dimensions (mm)", "insert_after": "stock_uom", "collapsible": 1},
        {"fieldname": "mallet_oc_code", "fieldtype": "Data", "label": "OpenCutList Code",
         "insert_after": "mallet_material_sb", "read_only": 1},
        {"fieldname": "mallet_thickness_mm", "fieldtype": "Float", "label": "Thickness (mm)",
         "insert_after": "mallet_oc_code"},
        {"fieldname": "mallet_material_cb", "fieldtype": "Column Break",
         "insert_after": "mallet_thickness_mm"},
        # Generic Length/Width (mm): sheet stock size on panels, piece size on
        # hardware. (Fieldnames keep the historical 'sheet_' prefix to avoid a data
        # migration; the labels are generic.)
        {"fieldname": "mallet_sheet_length_mm", "fieldtype": "Float", "label": "Length (mm)",
         "insert_after": "mallet_material_cb"},
        {"fieldname": "mallet_sheet_width_mm", "fieldtype": "Float", "label": "Width (mm)",
         "insert_after": "mallet_sheet_length_mm"},
        # F3 — the SG/laminate code decoded into filterable attributes (the encoded
        # name is kept; these make V0/V1 + internal/external laminate reportable).
        {"fieldname": "mallet_coding_sb", "fieldtype": "Section Break", "label": "Material Coding",
         "insert_after": "mallet_sheet_width_mm", "collapsible": 1},
        {"fieldname": "mallet_visible_sides", "fieldtype": "Int", "label": "Visible Sides (V)",
         "insert_after": "mallet_coding_sb",
         "description": "0 = internal (shelves, drawer boxes); 1 = one visible face (door, visible carcass side)."},
        {"fieldname": "mallet_lam_internal", "fieldtype": "Data", "label": "Internal Laminate",
         "insert_after": "mallet_visible_sides"},
        {"fieldname": "mallet_coding_cb", "fieldtype": "Column Break", "insert_after": "mallet_lam_internal"},
        {"fieldname": "mallet_lam_external", "fieldtype": "Data", "label": "External Laminate",
         "insert_after": "mallet_coding_cb"},
        # S3 — the manufacturer part number (e.g. Hafele 'H-311.01.357'). Same spec
        # whoever supplies it, so it lives on the Item and flows onto every PO.
        {"fieldname": "mallet_sourcing_sb", "fieldtype": "Section Break", "label": "Sourcing",
         "insert_after": "mallet_lam_external", "collapsible": 1},
        {"fieldname": "mallet_mfr_part_no", "fieldtype": "Data", "label": "Manufacturer Part No",
         "insert_after": "mallet_sourcing_sb",
         "description": "OEM catalogue code (e.g. Hafele H-311.01.357) — carried onto purchase orders."},
        # T1 — the item's GST rate. Estimation rates are keyed EX-tax; the
        # estimator grosses up by this % so estimates show the landed (post-tax)
        # material cost. ITC makes the actual net cost lower — deliberate cushion.
        {"fieldname": "mallet_gst_pct", "fieldtype": "Percent", "label": "GST %",
         "insert_after": "mallet_mfr_part_no", "default": "18",
         "description": "GST charged on purchase. Estimation = ex-tax rate x (1 + GST%) = landed cost."},
    ]
}

DEFAULT_GST_PCT = 18.0


def item_gst_pct(item_code):
    """T1 — the item's GST %, defaulting to 18 when unset/absent."""
    if not frappe.get_meta("Item").has_field("mallet_gst_pct"):
        return DEFAULT_GST_PCT
    v = frappe.db.get_value("Item", item_code, "mallet_gst_pct")
    return float(v) if v not in (None, 0, "") else DEFAULT_GST_PCT


def landed_rate(item_code):
    """T1 — (landed_rate, base_rate, gst_pct, source): the estimation rate grossed
    up by the item's GST% — the post-tax cost the estimate carries."""
    base, source = material_rate(item_code)
    gst = item_gst_pct(item_code)
    return base * (1 + gst / 100.0), base, gst, source


def material_bucket(item_code, oc_code=None):
    """C1 — classify a material line into the user's cost-breakup buckets."""
    code = str(oc_code or item_code or "")
    kind = kind_for_code(code)
    up = code.upper()
    if kind == "sheet":
        return "Ply V1 (visible grade)" if "_V1" in up else "Ply V0 (structure grade)"
    if kind == "laminate":
        return "Laminate External" if ("_V1" in up or "_EX" in up) else "Laminate Internal"
    if kind == "edge":
        return "Edge Banding External" if "_EX" in up else "Edge Banding Internal"
    if kind in ("hardware", "joinery"):
        return "Joinery Hardware" if hardware_group(code) == JOINERY_GROUP else "Client Hardware"
    return "Other Material"


def ensure_inventory_masters():
    """UOMs, the material Item Group tree, the Client SKU group and the Item
    custom fields. Idempotent."""
    result = {"item_groups": 0, "uoms": 0, "custom_fields": 0, "errors": []}

    for uom in ("Sheet", "Meter", "Roll", "Square Meter", "Litre"):
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

    # F5 — assumed-price planning list.
    try:
        pr = ensure_pricing_masters()
        result["price_list"] = pr.get("price_list", 0)
        result["errors"] += pr.get("errors", [])
    except Exception as exc:
        result["errors"].append(f"pricing masters: {exc}")

    # F2 — Manufacturer / Brand / Supplier option pools.
    try:
        vm = ensure_vendor_masters()
        result["vendors"] = {k: vm[k] for k in ("manufacturers", "brands", "suppliers")}
        result["errors"] += vm.get("errors", [])
    except Exception as exc:
        result["errors"].append(f"vendor masters: {exc}")

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


def ensure_decor_item(placeholder, brand, catalogue, name, raw):
    """S9 — create/enrich the REAL décor Item behind a placeholder slot:
    LAMD_* (Laminate group, sheet, dye-lot batch) or EBD_* (Edge Banding group,
    metre/roll). One item per real-world décor, reused across projects. Structure
    only — the rate is keyed once on the price list (middle-path rule). Returns
    the item_code."""
    from mallet_estimator import decor
    code = decor.decor_item_code(placeholder, brand, catalogue, raw)
    is_edge = code.startswith("EBD_")
    kind = "edge" if is_edge else "laminate"
    spec = KIND_SPEC[kind]
    display = " ".join(x for x in (brand, catalogue, name) if x) or raw

    if not frappe.db.exists("Item", code):
        meta = frappe.get_meta("Item")
        item = frappe.new_doc("Item")
        item.item_code = code
        item.item_name = display[:140]
        item.item_group = spec["group"] if frappe.db.exists("Item Group", spec["group"]) else _fallback_group()
        item.stock_uom = spec["stock_uom"] if frappe.db.exists("UOM", spec["stock_uom"]) else "Nos"
        item.is_stock_item = 1
        item.is_purchase_item = 1
        if not is_edge:
            _set(item, meta, "has_batch_no", 1)
            _set(item, meta, "create_new_batch", 1)
            _set(item, meta, "batch_number_series", f"{code}-.####")
        _add_uom(item, item.stock_uom, 1)
        pu = spec.get("purchase_uom")
        if pu and pu != item.stock_uom and frappe.db.exists("UOM", pu):
            item.purchase_uom = pu
            _add_uom(item, pu, spec["conv"])
        if not is_edge and frappe.db.exists("UOM", "Square Meter"):
            _add_uom(item, "Square Meter", SHEET_AREA_SQM)
        item.description = f"Décor: {display} (slot item — the real laminate/banding behind an OCL placeholder)"
        if brand and frappe.db.exists("Manufacturer", brand):
            _set(item, meta, "default_item_manufacturer", brand)
            if catalogue:
                _set(item, meta, "default_manufacturer_part_no", catalogue)
        if brand and frappe.db.exists("Brand", brand):
            _set(item, meta, "brand", brand)
        item.insert(ignore_permissions=True)
    else:
        # enrich manufacturer/brand when a later description adds it
        if brand and frappe.db.exists("Manufacturer", brand) \
                and not frappe.db.get_value("Item", code, "default_item_manufacturer"):
            frappe.db.set_value("Item", code, "default_item_manufacturer", brand, update_modified=False)

    attach_scope_suppliers(code, kind)
    return code

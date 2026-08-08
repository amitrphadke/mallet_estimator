# ---------------------------------------------------------------------------
# The material board — one editable view of an SKU's material lines, used
# identically on the Estimate SKU form and inside the Estimate screen.
#
# The desk grid could not do this job. Its columns are the same for every row,
# so a ply sheet and a hinge had to share a header; the money chain (MRP →
# discount → taxable → policy vs applied tax → landed) did not fit across, so
# it scrolled sideways or hid in a row editor; and assigning a décor meant a
# separate table keyed by slot letters, which is a level of indirection nobody
# asked for. So the board is rendered by us: lines GROUPED by what they are,
# each group carrying only the columns that mean something for it, with the
# décor sitting on the line it belongs to.
#
# The server stays the authority. The browser recomputes locally for instant
# totals, but every edit is written through save_material_edits, which saves
# the SKU and returns the board the SAVE produced — so what you end up looking
# at is always what was stored, never the optimistic guess.
# ---------------------------------------------------------------------------

# The column shapes and the write allow-list below are plain data with no
# frappe import at module level, so the fast unit lane can pin them without a
# database. frappe is imported where it is actually used.

# Group order = the order a person reads a cutting list in: structure, then
# the surfaces on it, then what holds it together, then everything else.
GROUP_ORDER = (
    "Ply V0 (structure grade)", "Ply V1 (visible grade)",
    "Laminate Internal", "Laminate External",
    "Edge Banding Internal", "Edge Banding External",
    "Client Hardware", "Joinery Hardware", "Other Material",
)

# Which extra columns each group earns. `decor` = the line carries a décor
# (and, for ply, a second face); `dims` = the physical size is what you check.
# Hardware gets neither: its group header already says Client vs Joinery, and
# the line's own code IS the designation.
GROUP_SHAPE = {
    "Ply V0 (structure grade)": {"decor": 2, "dims": True},
    "Ply V1 (visible grade)": {"decor": 2, "dims": True},
    "Laminate Internal": {"decor": 1},
    "Laminate External": {"decor": 1},
    "Edge Banding Internal": {"decor": 1},
    "Edge Banding External": {"decor": 1},
}

EDITABLE = ("discount_pct", "tax_rate", "customer_supplied", "decor", "decor_ext")


def _line(m):
    return {
        "row": m.name,
        "idx": m.idx,
        "material": m.get("material") or "",
        "item": m.get("item") or "",
        "description": m.get("description") or "",
        "qty": float(m.get("qty") or 0),
        "uom": m.get("uom") or "",
        "length": float(m.get("length") or 0),
        "width": float(m.get("width") or 0),
        "thickness": float(m.get("thickness") or 0),
        "unit_cost": float(m.get("unit_cost") or 0),
        "discount_pct": float(m.get("discount_pct") or 0),
        "discount_amount": float(m.get("discount_amount") or 0),
        "net_rate": float(m.get("net_rate") or 0),
        "line_cost": float(m.get("line_cost") or 0),
        "tax_rate_policy": float(m.get("tax_rate_policy") or 0),
        "tax_rate": m.get("tax_rate"),
        "tax_amount": float(m.get("tax_amount") or 0),
        "tax_saved": float(m.get("tax_saved") or 0),
        "amount_with_tax": float(m.get("amount_with_tax") or 0),
        "customer_supplied": 1 if m.get("customer_supplied") else 0,
        "manual": 1 if m.get("is_manual") else 0,
        "decor": m.get("decor") or "",
        "decor_ext": m.get("decor_ext") or "",
    }


def decor_options():
    """Every décor master, split by domain, for the in-line pickers. Small
    enough to send whole — a studio carries tens of laminates, not thousands —
    which is what makes the picker instant and keeps the board self-contained."""
    import frappe

    if not frappe.db.exists("DocType", "Mallet Decor"):
        return {"Laminate": [], "Edge Band": []}
    out = {"Laminate": [], "Edge Band": []}
    for d in frappe.get_all(
        "Mallet Decor",
        fields=["name", "domain", "brand", "code", "decor_name"],
        order_by="brand asc, code asc",
        limit_page_length=0,
    ):
        label = " · ".join([p for p in (d.brand, d.code, d.decor_name) if p]) or d.name
        out.setdefault(d.domain or "Laminate", []).append({"value": d.name, "label": label})
    return out


def build(doc):
    """The whole board for one SKU: grouped lines, per-group and overall
    totals, and the décor choices. `doc` is an Estimate SKU document."""
    from mallet_estimator import inventory

    groups, order = {}, []
    for m in doc.get("materials") or []:
        bucket = inventory.material_bucket(m.get("item"), m.get("material")) or "Other Material"
        if bucket not in groups:
            groups[bucket] = {
                "group": bucket,
                "shape": GROUP_SHAPE.get(bucket, {}),
                "lines": [],
                "taxable": 0.0, "tax": 0.0, "landed": 0.0,
                "discount": 0.0, "tax_saved": 0.0, "client_supplied": 0.0,
            }
            order.append(bucket)
        g = groups[bucket]
        line = _line(m)
        g["lines"].append(line)
        # A client-supplied line is priced but never costed, so it is totalled
        # on its own rather than folded into the group's money.
        if line["customer_supplied"]:
            g["client_supplied"] += line["amount_with_tax"]
        else:
            g["taxable"] += line["line_cost"]
            g["tax"] += line["tax_amount"]
            g["landed"] += line["amount_with_tax"]
            g["discount"] += line["discount_amount"]
            g["tax_saved"] += line["tax_saved"]

    rank = {name: i for i, name in enumerate(GROUP_ORDER)}
    order.sort(key=lambda b: (rank.get(b, len(rank)), b))
    ordered = [groups[b] for b in order]

    def total(key):
        return sum(g[key] for g in ordered)

    return {
        "sku": doc.name,
        "article": doc.get("article_name") or doc.name,
        "code": doc.get("sku_code") or "",
        "room": doc.get("room") or "",
        "mode": doc.get("estimation_mode") or "OCL PDF (standard)",
        "frozen": 1 if doc.get("rates_frozen") else 0,
        "unpriced": doc.get("unpriced_materials") or "",
        "groups": ordered,
        "decor_options": decor_options(),
        "totals": {
            "taxable": total("taxable"),
            "tax": total("tax"),
            "landed": total("landed"),
            "discount": total("discount"),
            "tax_saved": total("tax_saved"),
            "client_supplied": total("client_supplied"),
            "material_cost": float(doc.get("material_cost") or 0),
            "client_total": float(doc.get("client_total") or 0),
            "est_days": float(doc.get("est_days") or 0),
        },
        # The generic codes still waiting for a décor — the number the Apply
        # button reports back, so "did it map?" has an answer on screen.
        "unmapped": unmapped_count(doc),
    }


def unmapped_count(doc):
    """Lines whose item is still the generic slot code — i.e. no décor has
    resolved them yet."""
    n = 0
    for m in doc.get("materials") or []:
        code = str(m.get("material") or "")
        up = code.upper()
        if not (up.startswith("SG_LAM") or up.startswith("EB_") or up.startswith("SG_PLY")):
            continue
        if (m.get("item") or "") == code:
            n += 1
    return n


def apply_edits(doc, changes):
    """Write the edits a user made on the board, then let the ordinary save
    reprice everything. Only the fields the board actually offers are
    writable — a payload naming anything else is ignored rather than trusted,
    since this arrives straight from the browser."""
    by_row = {m.name: m for m in (doc.get("materials") or [])}
    touched = 0
    for change in changes or []:
        m = by_row.get(change.get("row"))
        if not m:
            continue
        for field in EDITABLE:
            if field not in change:
                continue
            value = change[field]
            if field in ("discount_pct", "tax_rate"):
                value = None if value in (None, "") else float(value)
                if field == "discount_pct":
                    value = max(0.0, min(100.0, value or 0))
            elif field == "customer_supplied":
                value = 1 if value else 0
            m.set(field, value)
            touched += 1
    return touched

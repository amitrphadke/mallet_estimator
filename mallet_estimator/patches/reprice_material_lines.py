import frappe

HOUSE_GST = 18.0


def execute():
    """Recompute the per-line pricing columns on material lines that were
    saved before those columns existed.

    Net Rate, Std Tax %, Tax and Landed only get values when a SKU is saved,
    so every line written before the pricing chain shipped still reads 0 —
    and a Std Tax of 0% on a GST job looks like a bug in the arithmetic rather
    than a field nobody has filled yet. This writes the same numbers
    price_material_lines would, WITHOUT re-saving the SKUs (a save re-imports
    the CSV and would reprice quantities as a side effect).

    Frozen SKUs are skipped: their lines are what was quoted."""
    if not frappe.db.has_column("Estimate Material", "net_rate"):
        return
    frozen = set(frappe.get_all("Estimate SKU", filters={"rates_frozen": 1}, pluck="name"))
    rows = frappe.get_all(
        "Estimate Material",
        filters={"parenttype": "Estimate SKU"},
        fields=["name", "parent", "item", "qty", "unit_cost", "discount_pct",
                "tax_rate", "customer_supplied"],
    )
    has_item_gst = frappe.db.has_column("Item", "mallet_gst_pct")
    policy_cache = {}
    for r in rows:
        if r.parent in frozen:
            continue
        try:
            qty = float(r.qty or 0)
            rate = float(r.unit_cost or 0)
            disc = max(0.0, min(100.0, float(r.discount_pct or 0)))
            net_rate = rate * (1 - disc / 100.0)
            line_cost = qty * net_rate
            policy = HOUSE_GST
            if has_item_gst and r.item:
                if r.item not in policy_cache:
                    keyed = frappe.db.get_value("Item", r.item, "mallet_gst_pct")
                    # 0 means "nobody keyed it", not "zero-rated"
                    policy_cache[r.item] = float(keyed) if keyed else HOUSE_GST
                policy = policy_cache[r.item]
            applied = float(r.tax_rate) if r.tax_rate not in (None, "") else policy
            tax = line_cost * applied / 100.0
            frappe.db.set_value("Estimate Material", r.name, {
                "net_rate": net_rate,
                "discount_amount": qty * rate * disc / 100.0,
                "line_cost": line_cost,
                "tax_rate_policy": policy,
                "tax_discount_pct": policy - applied,
                "tax_amount": tax,
                "tax_saved": line_cost * (policy - applied) / 100.0,
                "amount_with_tax": line_cost + tax,
            }, update_modified=False)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"reprice line {r.name}")
    frappe.db.commit()

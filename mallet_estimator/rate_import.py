# S6 — supplier rate-sheet importer. Turns a vendor's rate list (CSV) into
# ERPNext catalogue Items + per-supplier buying prices, so the same technical item
# carries many vendor prices and the estimation ceiling stays = max(MRP).
#
# Expected CSV columns (headers are matched loosely, case-insensitive):
#   part_no / code / mfr part no   -> the manufacturer catalogue code (item_code)
#   description / desc / name      -> item name / description
#   rate / mrp / price / list rate -> the MRP without tax (per-supplier buying price)
#   discount / disc %  (optional)  -> recorded in the results log only (discount is
#                                     applied on the PO line, not stored on the item)
#   uom / unit / per   (optional)  -> ignored for now (stock UOM comes from the kind)

import csv
import io

import frappe

from mallet_estimator import inventory

HEADER_ALIASES = {
    "part_no": ["part_no", "part no", "partno", "code", "mfr_part_no", "mfr part no",
                "item code", "item_code", "part number", "catalogue", "cat no", "cat. no", "sku"],
    "description": ["description", "desc", "name", "goods", "description of goods",
                    "description of goods/services", "item", "particulars"],
    "rate": ["rate", "mrp", "price", "list rate", "list price", "unit price", "unit rate"],
    "discount": ["discount", "disc", "disc %", "disc%", "discount %", "discount%"],
    "uom": ["uom", "unit", "per"],
}


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except Exception:
        return 0.0


def _map_headers(fieldnames):
    """Map the CSV's own headers onto our canonical keys."""
    out = {}
    for i, h in enumerate(fieldnames or []):
        key = (h or "").strip().lower()
        for canon, aliases in HEADER_ALIASES.items():
            if key in aliases and canon not in out:
                out[canon] = fieldnames[i]
    return out


def parse_rate_csv(text):
    """Parse rate-sheet CSV text into rows [{part_no, description, rate, discount, uom}].
    Rows without a part_no or a positive rate are skipped."""
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    colmap = _map_headers(reader.fieldnames)
    if "part_no" not in colmap or "rate" not in colmap:
        frappe.throw(
            "Rate CSV needs at least a part-number column (part_no / code) and a "
            "rate column (rate / mrp / price). Found headers: "
            + ", ".join(reader.fieldnames or [])
        )
    rows = []
    for r in reader:
        part = (r.get(colmap["part_no"]) or "").strip()
        rate = _num(r.get(colmap["rate"]))
        if not part or rate <= 0:
            continue
        rows.append({
            "part_no": part,
            "description": (r.get(colmap.get("description", "")) or "").strip(),
            "rate": rate,
            "discount": _num(r.get(colmap.get("discount", ""))) if colmap.get("discount") else 0.0,
            "uom": (r.get(colmap.get("uom", "")) or "").strip(),
        })
    return rows


# V4 — PDF rate sheets (Sun Tradelink / Bizanalyst layout). Each item is one
# NOTE (2026-08-01): the supplier-PDF import path was REMOVED at the user's
# request — every supplier formats their rate sheet differently, and hardware
# items follow the shop's OWN naming convention (HWD_AH_SC_0), not the vendor
# catalogue's. Rate sheets are imported as CSV in our own column layout; matching
# a purchase order to a supplier's format comes later.


def _import_rows(supplier, rows, manufacturer=None, item_group=None, kind="hardware"):
    """Core: turn parsed rate rows into catalogue Items + per-supplier MRP prices."""
    if not frappe.has_permission("Item", "create"):
        frappe.throw("Not permitted")
    sup = inventory.supplier_docname(supplier) or supplier
    if not sup or not frappe.db.exists("Supplier", sup):
        frappe.throw(f"Unknown Supplier: {supplier}")

    items, priced, log, errors = 0, 0, [], []
    for row in rows:
        try:
            code = inventory.ensure_catalogue_item(
                row["part_no"], description=row.get("description"),
                manufacturer=manufacturer, kind=kind, item_group=item_group,
            )
            if not code:
                continue
            items += 1
            inventory._ensure_item_supplier(code, sup, part_no=row["part_no"])
            inventory.set_vendor_price(code, sup, row["rate"])  # + ceiling refresh
            priced += 1
            disc = f" (disc {row['discount']:g}%)" if row.get("discount") else ""
            log.append(f"{code}: {supplier} MRP {row['rate']:g}{disc}")
        except Exception as exc:
            errors.append(f"{row.get('part_no')}: {exc}")
            frappe.log_error(frappe.get_traceback(), f"rate import {row.get('part_no')}")
    frappe.db.commit()
    return {"rows": len(rows), "items": items, "priced": priced, "log": log, "errors": errors}


@frappe.whitelist()
def import_supplier_rates(supplier, csv_text, manufacturer=None, item_group=None, kind="hardware"):
    """Import a supplier rate list from CSV text."""
    return _import_rows(supplier, parse_rate_csv(csv_text), manufacturer, item_group, kind)

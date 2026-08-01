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
import re

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
# logical line ending in "<qty> PR <rate> PR <disc>% <amount>", with a part code
# like H-311.01.357 earlier in the line; descriptions can wrap across lines.
_PART_RE = re.compile(r"([A-Za-z]-[\d.]+)")
_TAIL_RE = re.compile(r"(\d+)\s+PR\s+([\d,]+(?:\.\d+)?)\s+PR\s+([\d.]+)\s*%\s+([\d,]+(?:\.\d+)?)\s*$")


def _pdf_text(content):
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    # pypdf is a declared app dependency (pyproject.toml), so it's always present.
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except ImportError:
        pass
    try:
        from pdfminer.high_level import extract_text
        return extract_text(io.BytesIO(content)) or ""
    except ImportError:
        frappe.throw(
            "PDF parsing needs pypdf (or pdfminer.six) on the bench. Export the "
            "rate sheet to CSV, or ask to add the dependency."
        )


def parse_rate_pdf(content):
    """Parse a Sun Tradelink-style rate PDF into rows [{part_no, description, rate,
    discount, uom}]. `rate` is the MRP (the pre-discount column). Accumulates
    wrapped lines until the per-item tail is seen."""
    rows, buf = [], ""
    for line in _pdf_text(content).splitlines():
        line = line.strip()
        if not line:
            continue
        buf = f"{buf} {line}".strip() if buf else line
        mt = _TAIL_RE.search(buf)
        mp = _PART_RE.search(buf)
        if mt and mp:
            qty, rate, disc, amt = mt.groups()
            rows.append({
                "part_no": mp.group(1),
                "description": buf[mp.end():mt.start()].strip(" -–"),
                "rate": _num(rate), "discount": _num(disc), "uom": "PR",
            })
            buf = ""
    return rows


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


def import_supplier_rates_pdf(supplier, pdf_content, manufacturer=None, item_group=None, kind="hardware"):
    """Import a supplier rate list from PDF bytes (Sun Tradelink / Bizanalyst)."""
    return _import_rows(supplier, parse_rate_pdf(pdf_content), manufacturer, item_group, kind)

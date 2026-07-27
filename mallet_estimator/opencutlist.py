# ---------------------------------------------------------------------------
# Native OpenCutList CSV parser + material aggregator.
#
# OpenCutList's "parts" export (semicolon-delimited) lists every part instance
# with its material, final area, edge-banding and laminate faces — but no
# prices. This module turns that part list into an aggregated material estimate:
#
#   Sheet Goods -> total area per (material, thickness) -> whole sheets
#                  (area x (1+wastage) / sheet area, rounded UP)
#   Hardware    -> count of instances per material
#   Edge banding -> running meters per edge-banding material
#   Laminate    -> face area per laminate material -> whole sheets
#
# Prices are looked up separately from the ERPNext Item rate card; this module
# is framework-free so it can be unit-tested without a bench.
# ---------------------------------------------------------------------------

import csv
import io
import math
import re

SHEET_TYPES = {"sheet goods"}
HARDWARE_TYPES = {"hardware"}


def _num(v):
    """'2060 mm' / '1.19 m²' / '1,19' -> float (first number found)."""
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else 0.0


def _material_from(cell):
    """'EB_PVC_IN_a (1 mm x 22 mm)' -> 'EB_PVC_IN_a'; '' -> None."""
    s = (cell or "").strip()
    if not s:
        return None
    return s.split("(")[0].strip() or None


def parse_opencutlist_csv(text):
    """Parse the semicolon-delimited native OpenCutList export into dict rows.

    OpenCutList sometimes repeats the header before each material-type block;
    we key every data row off the first header and skip any repeated headers
    and blank lines.
    """
    text = text.replace("﻿", "")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if [c.strip() for c in r] == header:  # repeated header block
            continue
        rec = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
        out.append(rec)
    return out


def parts_list(rows):
    """Extract the panel part list (Material type = Sheet Goods, name starts SG)
    from the parts CSV — one entry per part instance, carrying the part number
    (the id encoded in the OpenCutList QR label) for job-card tracking."""
    out = []
    for r in rows:
        if (r.get("Material type") or "").strip().lower() != "sheet goods":
            continue
        name = (r.get("Material name") or "").strip()
        if not name.upper().startswith("SG"):
            continue
        out.append({
            "part_no": (r.get("No.") or "").strip(),
            "designation": (r.get("Designation") or r.get("Instance") or "").strip(),
            "material": name,
            "length": _num(r.get("Length") or r.get("Length - raw")),
            "width": _num(r.get("Width") or r.get("Width - raw")),
            "thickness": _num(r.get("Thickness") or r.get("Thickness - raw")),
            "tag": (r.get("Tag") or "").strip(),
        })
    return out


def classify_hardware(name):
    """Bucket a hardware material name into an operation-driver category."""
    n = (name or "").lower()
    if "minifix" in n:
        return "minifix"
    if "hinge" in n:
        return "hinges"
    if "handle" in n:
        return "handles"
    if "rail" in n:
        return "rails"
    if "shelf" in n or "support" in n:
        return "shelf_supports"
    if "lock" in n or "tower" in n or "bolt" in n:
        return "locks"
    if "screw" in n:
        return "screws"
    return "other"


def aggregate(rows, sheet_length_mm=2440.0, sheet_width_mm=1220.0, wastage_pct=12.0):
    """Aggregate parsed part rows into material estimate lines + operation drivers.

    Returns {"lines": [...], "drivers": {...}}. Each line is
    {kind, material, thickness, uom, qty, area, desc}; `qty` is whole sheets
    (sheet/laminate), running meters (edge) or count (hardware). `drivers` gives
    the quantities that auto-fill the labor/operation table (sheets, laminate
    sheets, edge parts/meters, panels, minifix, hinges, handles, rails, shelf
    supports, locks, screws, hardware_total). Prices come later from the Item
    rate card.
    """
    sheet_area = (sheet_length_mm * sheet_width_mm) / 1_000_000.0  # m² per sheet
    factor = 1 + (wastage_pct / 100.0)

    sheets = {}    # (material, thickness) -> area m²
    laminate = {}  # material -> area m²
    edging = {}    # material -> meters
    hardware = {}  # material -> count
    hw_cat = {}    # category -> count
    panels = 0
    edge_parts = 0

    for r in rows:
        mtype = (r.get("Material type") or "").strip().lower()
        mname = (r.get("Material name") or "").strip()
        if mtype in SHEET_TYPES:
            panels += 1
            area = _num(r.get("Area - final") or r.get("Area"))
            th = _num(r.get("Thickness") or r.get("Thickness - raw"))
            sheets[(mname, th)] = sheets.get((mname, th), 0.0) + area

            length_m = _num(r.get("Length") or r.get("Length - raw")) / 1000.0
            width_m = _num(r.get("Width") or r.get("Width - raw")) / 1000.0
            has_edge = False
            for col, dim in (
                ("Edge Length 1", length_m), ("Edge Length 2", length_m),
                ("Edge Width 1", width_m), ("Edge Width 2", width_m),
            ):
                eb = _material_from(r.get(col))
                if eb:
                    edging[eb] = edging.get(eb, 0.0) + dim
                    has_edge = True
            if has_edge:
                edge_parts += 1
            for col in ("Frontside", "Backside"):
                lam = _material_from(r.get(col))
                if lam:
                    laminate[lam] = laminate.get(lam, 0.0) + area
        elif mtype in HARDWARE_TYPES:
            if mname:
                hardware[mname] = hardware.get(mname, 0) + 1
                cat = classify_hardware(mname)
                hw_cat[cat] = hw_cat.get(cat, 0) + 1

    lines = []
    for (mname, th), area in sorted(sheets.items()):
        qty = math.ceil(area * factor / sheet_area) if sheet_area > 0 else 0
        lines.append({
            "kind": "sheet", "material": mname, "thickness": th, "uom": "Nos", "qty": qty,
            "area": round(area, 3),
            "desc": f"{mname} {th:g}mm — {round(area, 2)} m² → {qty} sheet(s) incl {wastage_pct:g}% waste",
        })
    for mname, area in sorted(laminate.items()):
        qty = math.ceil(area * factor / sheet_area) if sheet_area > 0 else 0
        lines.append({
            "kind": "laminate", "material": mname, "thickness": 0, "uom": "Nos", "qty": qty,
            "area": round(area, 3),
            "desc": f"{mname} laminate — {round(area, 2)} m² → {qty} sheet(s)",
        })
    for mname, meters in sorted(edging.items()):
        lines.append({
            "kind": "edge", "material": mname, "thickness": 0, "uom": "Meter",
            "qty": round(meters * factor, 2), "area": 0,
            "desc": f"{mname} edge banding — {round(meters * factor, 1)} m incl {wastage_pct:g}% waste",
        })
    for mname, count in sorted(hardware.items()):
        lines.append({
            "kind": "hardware", "material": mname, "thickness": 0, "uom": "Nos",
            "qty": count, "area": 0, "desc": f"{mname} — {count} nos",
        })

    drivers = {
        "sheets": sum(l["qty"] for l in lines if l["kind"] == "sheet"),
        "laminate_sheets": sum(l["qty"] for l in lines if l["kind"] == "laminate"),
        "edge_meters": round(sum(l["qty"] for l in lines if l["kind"] == "edge"), 2),
        "edge_parts": edge_parts,
        "panels": panels,
        "minifix": hw_cat.get("minifix", 0),
        "hinges": hw_cat.get("hinges", 0),
        "handles": hw_cat.get("handles", 0),
        "rails": hw_cat.get("rails", 0),
        "shelf_supports": hw_cat.get("shelf_supports", 0),
        "locks": hw_cat.get("locks", 0),
        "screws": hw_cat.get("screws", 0),
    }
    drivers["hardware_total"] = (
        drivers["minifix"] + drivers["hinges"] + drivers["handles"]
        + drivers["rails"] + drivers["shelf_supports"] + drivers["locks"]
    )
    return {"lines": lines, "drivers": drivers}


def item_code_for(line):
    """Stable ERPNext item_code for a material line (thickness matters for sheets)."""
    if line["kind"] == "sheet" and line.get("thickness"):
        return f"{line['material']}_{line['thickness']:g}mm"
    return line["material"]

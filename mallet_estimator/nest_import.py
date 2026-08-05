# ---------------------------------------------------------------------------
# CSV-Nest mode (Nest Estimate, Phase 1): build an Estimate SKU's material
# lines from the OpenCutList Part List CSV ALONE — the nesting engine computes
# sheet counts server-side (no Material Estimate PDF, no Part List PDF).
#
# The CSV fully specifies the job: per-part dims + ply material, per-face
# laminate codes (Frontside/Backside), per-edge banding codes (Edge Length/
# Width 1-2) and hardware rows. Everything downstream (décor map, rates,
# margins, ops, prints) sees ordinary material lines and works unchanged.
# Standard-mode SKUs never enter this module.
# ---------------------------------------------------------------------------

import json

import frappe
from frappe import _

from mallet_estimator import estimate_pdf, inventory, nesting, opencutlist, decor
from mallet_estimator.estimator import op_phase
from mallet_estimator.opencutlist import _material_from, _num

# calibrated on the shop's real OCL exports (WAR 9/9, BED 23/23 exact)
KERF_MM = 4.0
TRIM_MM = 10.0


def collect(rows):
    """Aggregate the CSV part rows into nesting inputs:
    (ply {(code, th): [(l,w)..]}, lam {code: [(l,w)..]}, edges {code: meters},
    hardware {code: qty}, banded_edge_count)."""
    ply, lam, edges, hw = {}, {}, {}, {}
    banded = 0
    for r in rows:
        name = (r.get("Material name") or "").strip()
        mtype = (r.get("Material type") or "").strip().lower()
        if mtype == "sheet goods" and name.upper().startswith("SG"):
            l = _num(r.get("Length") or r.get("Length - raw"))
            w = _num(r.get("Width") or r.get("Width - raw"))
            th = _num(r.get("Thickness") or r.get("Thickness - raw"))
            if not (l and w):
                continue
            ply.setdefault((name, th), []).append((l, w))
            for col, dim in (("Edge Length 1", l), ("Edge Length 2", l),
                             ("Edge Width 1", w), ("Edge Width 2", w)):
                eb = _material_from(r.get(col))
                if eb:
                    edges[eb] = edges.get(eb, 0.0) + dim / 1000.0
                    banded += 1
            for col in ("Frontside", "Backside"):
                lc = _material_from(r.get(col))
                if lc:
                    lam.setdefault(lc, []).append((l, w))
        elif name.upper().startswith(("HWD", "JH_")):
            hw[name] = hw.get(name, 0) + 1
    return ply, lam, edges, hw, banded


def run(doc):
    """The CSV-Nest import: mirrors do_import()'s contract (lines, parts table,
    décor blank rows, op drivers, design qty, unpriced flag) with quantities
    from the nesting engine instead of the OCL estimate PDF."""
    from mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku import _file_content

    content = _file_content(doc.parts_csv)
    if isinstance(content, bytes):
        content = content.decode("utf-8", "ignore")
    rows = opencutlist.parse_opencutlist_csv(content)
    if not rows:
        frappe.throw(_("The Part List CSV could not be parsed — is it the OpenCutList export?"))
    ply, lam, edges, hw, banded_edges = collect(rows)
    if not ply:
        frappe.throw(_("No sheet-good parts found in the CSV."))

    manual_rows = [m.as_dict() for m in (doc.materials or []) if m.get("is_manual")]
    doc.set("materials", [])
    unpriced, mats_shape, nest_info = [], [], {}

    for (code, th), parts in sorted(ply.items()):
        r = nesting.pack_sheets(parts, kerf=KERF_MM, trim=TRIM_MM, allow_rotate=False)
        nest_info[f"{code}@{th:g}mm"] = {"sheets": r["sheets"], "util": round(r["utilization"], 3),
                                         "parts": len(parts)}
        doc._add_material_line(
            code, "sheet", th, r["sheets"],
            f"{code} — {len(parts)} parts nested → {r['sheets']} sheet(s) ({r['utilization']:.0%} used) [CSV-Nest]",
            unpriced)
        mats_shape.append({"name": code, "kind": "sheet", "thickness": th, "qty": r["sheets"]})

    for code, faces in sorted(lam.items()):
        r = nesting.pack_sheets(faces, kerf=KERF_MM, trim=TRIM_MM, allow_rotate=False)
        nest_info[code] = {"sheets": r["sheets"], "util": round(r["utilization"], 3), "parts": len(faces)}
        doc._add_material_line(
            code, "laminate", 0, r["sheets"],
            f"{code} — {len(faces)} faces nested → {r['sheets']} sheet(s) ({r['utilization']:.0%} used) [CSV-Nest]",
            unpriced)
        mats_shape.append({"name": code, "kind": "laminate", "thickness": 0, "qty": r["sheets"]})

    for code, meters in sorted(edges.items()):
        rolls = nesting.edge_rolls(meters)
        doc._add_material_line(
            code, "edge", 0, rolls,
            f"{code} — {meters:.2f} m banding → {rolls} whole roll(s) of {inventory.EDGE_ROLL_METERS:g} m [CSV-Nest]",
            unpriced, uom="Roll", rate_factor=inventory.EDGE_ROLL_METERS)
        mats_shape.append({"name": code, "kind": "edge", "thickness": 0, "qty": rolls})

    for code, qty in sorted(hw.items()):
        doc._add_material_line(code, "hardware", 0, qty, f"{code} — {qty} nos [CSV-Nest]", unpriced)
        mats_shape.append({"name": code, "kind": "hardware", "thickness": 0, "qty": qty})

    for r in manual_rows:
        doc.append("materials", {
            "item": r.get("item"), "material": r.get("material"),
            "description": r.get("description"), "qty": r.get("qty") or 0,
            "uom": r.get("uom"), "unit_cost": r.get("unit_cost") or 0,
            "line_cost": (r.get("qty") or 0) * (r.get("unit_cost") or 0),
            "customer_supplied": r.get("customer_supplied") or 0, "is_manual": 1,
        })

    doc.unpriced_materials = ", ".join(unpriced)
    if unpriced:
        frappe.msgprint(
            _("UNPRICED lines entered at ₹0 — key each rate on the <b>Estimation (Assumed)</b> "
              "price list and Refresh rates:<br><b>{0}</b>").format(", ".join(unpriced)),
            title=_("Materials need a price"), indicator="red")

    # blank décor rows per slot instance (CSV has no description blocks)
    have = {("Laminate" if (r.get("domain") or "Laminate") != "Edge Band" else "Edge Band",
             (r.slot or "").strip().lower()) for r in (doc.get("sku_decors") or [])}
    have |= {("Edge Band", (r.slot or "").strip().lower()) for r in (doc.get("sku_decor_edges") or [])}
    ply_max = max([th for (_c, th) in ply.keys()] or [16])
    eb_thick, eb_wide = (1.0, 50.0) if ply_max > 18 else (0.8, 22.0)
    for m in doc.materials or []:
        base = str(m.material or "")
        up = base.upper()
        if not (up.startswith("SG_LAM") or up.startswith("EB_")):
            continue
        key = decor.slot_key(base)
        if not key:
            continue
        dom = "Edge Band" if up.startswith("EB_") else "Laminate"
        if (dom, key) not in have:
            if dom == "Edge Band":
                doc.append("sku_decor_edges", {"slot": key, "thickness": eb_thick, "width": eb_wide})
            else:
                doc.append("sku_decors", {"slot": key, "domain": dom})
            have.add((dom, key))

    # operation quantities from the nested materials + banded edge count
    opq = estimate_pdf.operation_quantities(mats_shape, banded_edges)
    for row in doc.labor:
        op = op_phase(row)
        if op in opq:
            row.qty = opq[op]
    opq["__nest__"] = nest_info
    doc.import_drivers = json.dumps(opq)

    for row in doc.get("design_labor") or []:
        if not float(row.qty or 0):
            row.qty = 1

    # parts table (QR/job-card tracking) — same as standard mode
    parts = opencutlist.parts_list(rows)
    if parts:
        doc.set("parts", [])
        for p in parts:
            doc.append("parts", {
                "part_no": p["part_no"], "designation": p["designation"], "material": p["material"],
                "tag": p["tag"], "length": p["length"], "width": p["width"], "thickness": p["thickness"],
                "cut": p.get("cut", 1), "edge_banded": p.get("edge_banded", 0),
                "laminated": p.get("laminated", 0),
            })

    frappe.msgprint(
        _("CSV-Nest import: {0} — sheets computed by the calibrated nesting engine "
          "(kerf {1} mm, trim {2} mm, grain-locked).").format(
            ", ".join(f"{k}: {v['sheets']}" for k, v in nest_info.items()),
            KERF_MM, TRIM_MM),
        title=_("Nest details"), indicator="blue")

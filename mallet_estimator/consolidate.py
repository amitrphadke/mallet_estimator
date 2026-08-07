# ---------------------------------------------------------------------------
# Cross-SKU consolidation (Nest Estimate, Phase 2): nest ALL of an Estimate's
# CSV-Nest SKUs' parts together per material, so shared sheets/rolls make each
# SKU cheaper than it is alone — and allocate the combined quantities back to
# the SKUs by PART-AREA SHARE per material (decided 2026-08-07: each SKU pays
# its parts' area directly; offcut waste is split pro-rata by that share.
# Facial sqft stays the pricing/display denominator, never the waste key).
#
# Pure module — no frappe import — so the whole engine unit-tests in CI's
# no-DB job. The Estimate controller feeds it the __nest_inputs__ blobs that
# nest_import stashed on each SKU and applies the allocation in memory only
# (SKU docs are shared across estimates and are never saved with
# estimate-specific numbers).
# ---------------------------------------------------------------------------

from mallet_estimator import nesting

KERF_MM = 4.0
TRIM_MM = 10.0


def _area(parts):
    return sum(float(l) * float(w) for (l, w) in parts)


def consolidate(sku_inputs, kerf=KERF_MM, trim=TRIM_MM):
    """sku_inputs: {sku: {"ply": {"CODE@th": [(l, w), ...]},
                          "lam": {code: [(l, w), ...]},
                          "edges": {code: meters}}}

    Returns {"materials": {key: {kind, combined, standalone, util,
                                 alloc: {sku: fractional qty},
                                 standalone_by_sku: {sku: qty}}},
             "sheet_ratio": {sku: allocated_sheets / standalone_sheets}}

    `sheet_ratio` covers ply + laminate sheets together — the driver for
    sheet-count operations (lamination, tape removal, cutting): fewer combined
    sheets means proportionally fewer sheet-level operations per SKU.
    """
    buckets = {}  # key -> {"kind", "per_sku": {sku: parts-or-meters}}
    for sku, inputs in sku_inputs.items():
        for key, parts in (inputs.get("ply") or {}).items():
            b = buckets.setdefault(key, {"kind": "sheet", "per_sku": {}})
            b["per_sku"].setdefault(sku, []).extend(tuple(p) for p in parts)
        for code, faces in (inputs.get("lam") or {}).items():
            b = buckets.setdefault(code, {"kind": "laminate", "per_sku": {}})
            b["per_sku"].setdefault(sku, []).extend(tuple(p) for p in faces)
        for code, meters in (inputs.get("edges") or {}).items():
            b = buckets.setdefault(code, {"kind": "edge", "per_sku": {}})
            b["per_sku"][sku] = b["per_sku"].get(sku, 0.0) + float(meters)

    materials = {}
    sheets_alloc = {}   # sku -> allocated sheet-count (ply + lam)
    sheets_alone = {}   # sku -> standalone sheet-count (ply + lam)
    for key, b in sorted(buckets.items()):
        per_sku = b["per_sku"]
        if b["kind"] == "edge":
            total_m = sum(per_sku.values())
            combined = nesting.edge_rolls(total_m)
            standalone_by_sku = {s: nesting.edge_rolls(m) for s, m in per_sku.items()}
            shares = {s: (m / total_m if total_m else 0.0) for s, m in per_sku.items()}
            util = None
        else:
            all_parts = [p for parts in per_sku.values() for p in parts]
            r = nesting.pack_sheets(all_parts, kerf=kerf, trim=trim, allow_rotate=False)
            combined = r["sheets"]
            util = round(r["utilization"], 3)
            standalone_by_sku = {
                s: nesting.pack_sheets(parts, kerf=kerf, trim=trim, allow_rotate=False)["sheets"]
                for s, parts in per_sku.items()
            }
            total_area = _area(all_parts)
            shares = {s: (_area(parts) / total_area if total_area else 0.0)
                      for s, parts in per_sku.items()}
        alloc = {s: round(combined * share, 3) for s, share in shares.items()}
        if b["kind"] in ("sheet", "laminate"):
            for s in per_sku:
                sheets_alloc[s] = sheets_alloc.get(s, 0.0) + alloc[s]
                sheets_alone[s] = sheets_alone.get(s, 0.0) + standalone_by_sku[s]
        materials[key] = {
            "kind": b["kind"],
            "combined": combined,
            "standalone": sum(standalone_by_sku.values()),
            "util": util,
            "alloc": alloc,
            "standalone_by_sku": standalone_by_sku,
        }

    sheet_ratio = {
        s: (sheets_alloc.get(s, 0.0) / sheets_alone[s]) if sheets_alone.get(s) else 1.0
        for s in set(sheets_alloc) | set(sheets_alone)
    }
    return {"materials": materials, "sheet_ratio": sheet_ratio}


def batch_factor(tiers, qty):
    """Batch-efficiency multiplier for an operation: `tiers` =
    [(from_qty, factor), ...] (any order); the tier with the greatest from_qty
    that is <= qty wins; no tier matched -> 1.0. Factors scale minutes/unit,
    so 0.75 means 'in this batch size the operation runs 25% faster'."""
    best_from, best = -1.0, 1.0
    for from_qty, factor in tiers or []:
        f, fac = float(from_qty or 0), float(factor or 0)
        if fac > 0 and f <= float(qty) and f > best_from:
            best_from, best = f, fac
    return best


CSV_MODE = "CSV-Nest"
PDF_MODE = "OCL PDF (standard)"


def split_by_mode(modes):
    """`modes` = {sku: estimation_mode}. Returns (csv_nest, pdf) name lists.

    The two modes carry material packing by DIFFERENT authorities: CSV-Nest
    sheets are nested here (and re-nested across the estimate's SKUs), while
    PDF-mode sheet counts come from OpenCutList's own nesting, already baked
    into the PDF per SKU. An estimate holding both would add sheet counts
    that were never packed together — and only the CSV subset would show the
    shared-material saving, so the totals read as if the PDF SKUs simply
    never benefit. Estimates must therefore be single-mode."""
    csv_nest, pdf = [], []
    for sku, mode in sorted((modes or {}).items()):
        (csv_nest if (mode or PDF_MODE) == CSV_MODE else pdf).append(sku)
    return csv_nest, pdf


def is_mixed(modes):
    csv_nest, pdf = split_by_mode(modes)
    return bool(csv_nest) and bool(pdf)

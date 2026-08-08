# ---------------------------------------------------------------------------
# Whitelisted API — the integration contract for the mcft-ocl SketchUp
# companion plugin (and any other client). The human attaching a CSV in the
# UI and the plugin POSTing one land on the SAME import path (nest_import via
# the SKU's save pipeline), so estimation behaves identically either way.
#
#   POST /api/method/mallet_estimator.api.import_parts_csv
#        {sku, csv_content, filename?}
#   GET  /api/method/mallet_estimator.api.get_sku_context?sku=MEST-SKU-00001
#
# Auth: normal Frappe token/session auth; permissions are the Estimate SKU
# doc permissions — nothing extra granted here.
# ---------------------------------------------------------------------------

import json

import frappe
from frappe import _


@frappe.whitelist(allow_guest=False)
def version_info():
    """The estimator code ACTUALLY running on this site — the antidote to a
    deploy that claims success. Frappe Cloud builds an image from the repo and
    the container may carry no git binary and no .git tree, so the commit is
    resolved by falling back through every source that can exist there:
      1. `git rev-parse` (dev benches)
      2. .git/HEAD + refs/packed-refs read as plain files (no git binary)
      3. the bench's apps.json (what FC's image build recorded)
    `source` says which one answered, so a missing badge is diagnosable."""
    import json as _json
    import os
    import subprocess

    from mallet_estimator import __version__

    out = {"version": __version__, "commit": None, "branch": None, "source": "none"}
    try:
        app_root = os.path.dirname(frappe.get_app_path("mallet_estimator"))
    except Exception:
        return out

    # 1) real git
    try:
        def git(*args):
            return subprocess.check_output(
                ["git", "-C", app_root] + list(args),
                text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        out["commit"] = git("rev-parse", "--short", "HEAD")
        out["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
        out["source"] = "git"
        return out
    except Exception:
        pass

    # 2) plain-file read of .git (no git binary needed)
    try:
        head_path = os.path.join(app_root, ".git", "HEAD")
        with open(head_path) as fh:
            head = fh.read().strip()
        sha = None
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            out["branch"] = ref.rsplit("/", 1)[-1]
            ref_file = os.path.join(app_root, ".git", ref)
            if os.path.exists(ref_file):
                with open(ref_file) as fh:
                    sha = fh.read().strip()
            else:  # packed refs
                packed = os.path.join(app_root, ".git", "packed-refs")
                if os.path.exists(packed):
                    with open(packed) as fh:
                        for line in fh:
                            if line.rstrip().endswith(" " + ref):
                                sha = line.split(" ", 1)[0].strip()
                                break
        else:
            sha = head
        if sha:
            out["commit"] = sha[:7]
            out["source"] = "git-files"
            return out
    except Exception:
        pass

    # 3) whatever the image build recorded
    try:
        bench_root = os.path.dirname(os.path.dirname(app_root))  # …/apps/<app> -> bench
        for candidate in (os.path.join(bench_root, "apps.json"),
                          os.path.join(bench_root, "sites", "apps.json")):
            if not os.path.exists(candidate):
                continue
            with open(candidate) as fh:
                data = _json.load(fh)
            entry = data.get("mallet_estimator") if isinstance(data, dict) else None
            if isinstance(entry, dict):
                sha = entry.get("commit") or entry.get("hash") or entry.get("version")
                if sha:
                    out["commit"] = str(sha)[:7]
                    out["branch"] = entry.get("branch") or out["branch"]
                    out["source"] = "apps.json"
                    return out
    except Exception:
        pass

    return out


@frappe.whitelist()
def import_parts_csv(sku, csv_content, filename=None):
    """Attach an OpenCutList Part List CSV to the SKU, switch it to CSV-Nest
    mode, run the import (nesting, décor slots, ops, costing) and return the
    result the caller needs to display: nest details, part-list-vs-views
    issues, unpriced materials, and the client totals."""
    doc = frappe.get_doc("Estimate SKU", sku)
    doc.check_permission("write")
    if doc.get("rates_frozen"):
        frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
    if not (csv_content or "").strip():
        frappe.throw(_("csv_content is empty."))

    f = frappe.get_doc({
        "doctype": "File",
        "file_name": filename or f"{doc.name}_partlist.csv",
        "attached_to_doctype": "Estimate SKU",
        "attached_to_name": doc.name,
        "attached_to_field": "parts_csv",
        "is_private": 1,
        "content": csv_content,
    }).insert(ignore_permissions=True)

    doc.estimation_mode = "CSV-Nest"
    doc.parts_csv = f.file_url
    doc.save()

    drivers = {}
    try:
        drivers = json.loads(doc.import_drivers or "{}") or {}
    except Exception:
        pass
    return {
        "sku": doc.name,
        "sku_code": doc.get("sku_code"),
        "nest": drivers.get("__nest__") or {},
        "issues": drivers.get("__issues__") or [],
        "unpriced": doc.get("unpriced_materials") or "",
        "material_cost": doc.get("material_cost"),
        "client_total": doc.get("client_total"),
        "est_days": doc.get("est_days"),
    }


@frappe.whitelist()
def attach_sku_file(sku, fieldname, file_url):
    """Attach/replace one of an SKU's source files from the Estimate's
    per-SKU files panel; saving runs the normal import pipeline."""
    if fieldname not in ("parts_csv", "views_pdf"):
        frappe.throw(_("fieldname must be parts_csv or views_pdf"))
    doc = frappe.get_doc("Estimate SKU", sku)
    doc.check_permission("write")
    if doc.get("rates_frozen"):
        frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
    doc.set(fieldname, file_url)
    if fieldname == "parts_csv" and not doc.get("estimation_mode"):
        doc.estimation_mode = "CSV-Nest"
    for fname in frappe.get_all("File", filters={"file_url": file_url}, pluck="name"):
        frappe.db.set_value("File", fname, {
            "attached_to_doctype": "Estimate SKU",
            "attached_to_name": doc.name,
            "attached_to_field": fieldname,
        }, update_modified=False)
    doc.save()
    return {"sku": doc.name, "client_total": doc.get("client_total"),
            "unpriced": doc.get("unpriced_materials") or ""}


@frappe.whitelist()
def get_sku_context(sku):
    """Everything the SketchUp side needs to set a model up for this SKU:
    identity (customer/project/room/code), the décor slot map (which generic
    codes to paint with), current material lines with rates, and state flags.
    Read-only."""
    doc = frappe.get_doc("Estimate SKU", sku)
    doc.check_permission("read")
    return {
        "sku": doc.name,
        "sku_code": doc.get("sku_code"),
        "article_name": doc.get("article_name"),
        "customer": doc.get("customer"),
        "project": doc.get("project"),
        "room": doc.get("room"),
        "estimation_mode": doc.get("estimation_mode"),
        "rates_frozen": bool(doc.get("rates_frozen")),
        "outer_mm": {"w": doc.get("outer_w"), "d": doc.get("outer_d"), "h": doc.get("outer_h")},
        "decor_map": [
            {"slot": r.get("slot"), "domain": r.get("domain") or "Laminate",
             "brand": r.get("brand"), "code": r.get("code"), "name": r.get("decor_name"),
             "short": r.get("short")}
            for r in list(doc.get("sku_decors") or [])
        ] + [
            {"slot": r.get("slot"), "domain": "Edge Band", "brand": r.get("brand"),
             "code": r.get("code"), "name": r.get("decor_name"), "short": r.get("short")}
            for r in list(doc.get("sku_decor_edges") or [])
        ],
        "materials": [
            {"material": m.get("material"), "item": m.get("item"), "qty": m.get("qty"),
             "uom": m.get("uom"), "rate": m.get("unit_cost"), "manual": bool(m.get("is_manual"))}
            for m in (doc.get("materials") or [])
        ],
        "client_total": doc.get("client_total"),
    }


@frappe.whitelist()
def material_board(sku):
    """The grouped, editable material view for one Estimate SKU. Same payload
    whether it is rendered on the SKU form or inside the Estimate screen —
    there is one board, not two implementations that drift apart."""
    from mallet_estimator import material_board as board

    doc = frappe.get_doc("Estimate SKU", sku)
    doc.check_permission("read")
    return board.build(doc)


@frappe.whitelist()
def save_material_edits(sku, changes):
    """Apply board edits and SAVE, returning the board the save produced.

    The browser recomputes locally so totals move as you type, but it never
    gets the last word: the save runs the whole pricing pipeline (price list
    rates, discount, tax policy vs applied, décor re-point) and what comes
    back is what was actually stored."""
    from mallet_estimator import material_board as board

    if isinstance(changes, str):
        changes = json.loads(changes or "[]")
    doc = frappe.get_doc("Estimate SKU", sku)
    doc.check_permission("write")
    if doc.get("rates_frozen"):
        frappe.throw(_("{0} is frozen (quoted on an approved estimate) — cancel and "
                       "amend that estimate to change its lines.").format(sku))
    board.apply_edits(doc, changes)
    doc.save()
    return board.build(doc)


@frappe.whitelist()
def apply_decor(sku):
    """Re-point every generic line at the décor now assigned to it, and report
    how many are still generic — the 'did it actually map?' answer."""
    from mallet_estimator import material_board as board

    doc = frappe.get_doc("Estimate SKU", sku)
    doc.check_permission("write")
    doc.save()  # apply_decor_map runs inside the ordinary validate pipeline
    result = board.build(doc)
    result["applied"] = 1
    return result

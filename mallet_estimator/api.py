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


@frappe.whitelist()
def version_info():
    """The estimator code actually running on this site: package version plus
    the git commit/branch of the deployed app tree (when the bench keeps the
    repo). Drives the navbar 'MEst @ <commit>' badge — at-a-glance proof of
    what a deploy landed (the antidote to false-green deploys)."""
    import os
    import subprocess

    from mallet_estimator import __version__

    out = {"version": __version__, "commit": None, "branch": None}
    try:
        app_root = os.path.dirname(frappe.get_app_path("mallet_estimator"))
        def git(*args):
            return subprocess.check_output(
                ["git", "-C", app_root] + list(args),
                text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        out["commit"] = git("rev-parse", "--short", "HEAD")
        out["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception:
        pass  # no git in the built image — version alone still shows
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

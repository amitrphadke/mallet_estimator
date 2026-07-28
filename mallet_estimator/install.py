import json
import os

import frappe

from mallet_estimator.estimator import (
    DEFAULT_MACHINES, STEP_TEMPLATE, WORKSTATIONS, OPERATION_WORKSTATION,
    ROUTING_NAME, workstation_rates,
)

PRINT_FORMAT_NAME = "Mallet Client Estimate"
WORKSPACE_NAME = "Mallet Estimator"


def after_install():
    seed_settings()
    _safe(ensure_manufacturing_masters)
    _safe(ensure_print_format)
    _safe(ensure_workspace)


def after_migrate():
    # Keep masters, print format and workspace in sync — but never break migrate.
    _safe(ensure_manufacturing_masters)
    _safe(ensure_print_format)
    _safe(ensure_workspace)


def _safe(fn):
    try:
        fn()
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"mallet_estimator {fn.__name__}")


def _op_name(phase):
    # Doc names avoid "/" which is awkward in Frappe routing/urls.
    return phase.replace(" / ", " - ").replace("/", "-")


def ensure_manufacturing_masters():
    """Create the 7 Workstations (space-based hour rates), 17 Operations and the
    standard Routing as ERPNext manufacturing masters. Idempotent: existing
    records are left untouched so in-app rate edits survive re-deploys. Each
    record is created independently so one failure doesn't abort the rest."""
    settings = frappe.get_single("Estimate Settings")
    rates = {w["name"]: w for w in workstation_rates(settings)}
    result = {"workstations": 0, "operations": 0, "routing": 0, "errors": []}

    def fail(label, exc):
        result["errors"].append(f"{label}: {exc}")
        frappe.log_error(frappe.get_traceback(), f"mallet_estimator masters: {label}")

    for w in WORKSTATIONS:
        try:
            if frappe.db.exists("Workstation", w["name"]):
                continue
            r = rates[w["name"]]
            ws = frappe.new_doc("Workstation")
            ws.workstation_name = w["name"]
            # Set only the rate fields this ERPNext version actually has (v16
            # reorganised the Workstation hour-rate fields).
            meta = frappe.get_meta("Workstation")
            for field, val in (
                ("hour_rate_rent", r["rent_hr"]),
                ("hour_rate_consumable", r["dep_hr"]),
                ("hour_rate_labour", r["labour_hr"]),
                ("hour_rate", r["total_hr"]),
            ):
                if meta.has_field(field):
                    ws.set(field, round(val, 2))
            ws.insert(ignore_permissions=True)
            result["workstations"] += 1
        except Exception as exc:
            fail(f"Workstation {w['name']}", exc)

    for t in STEP_TEMPLATE:
        op_name = _op_name(t["phase"])
        try:
            if frappe.db.exists("Operation", op_name):
                continue
            op = frappe.new_doc("Operation")
            op.name = op_name
            op.workstation = OPERATION_WORKSTATION.get(t["phase"])
            op.insert(ignore_permissions=True, set_name=op_name)
            result["operations"] += 1
        except Exception as exc:
            fail(f"Operation {op_name}", exc)

    try:
        if not frappe.db.exists("Routing", ROUTING_NAME):
            routing = frappe.new_doc("Routing")
            routing.routing_name = ROUTING_NAME
            for i, t in enumerate(STEP_TEMPLATE, start=1):
                routing.append("operations", {
                    "sequence_id": i,
                    "operation": _op_name(t["phase"]),
                    "workstation": OPERATION_WORKSTATION.get(t["phase"]),
                    "time_in_mins": 0,
                })
            routing.insert(ignore_permissions=True)
            result["routing"] = 1
    except Exception as exc:
        fail("Routing", exc)

    frappe.db.commit()
    return result


@frappe.whitelist()
def setup():
    """Manually (re)create all app masters — callable from the Estimate Settings
    button. Returns a summary so the UI can report what was created and any error."""
    if not frappe.has_permission("Estimate Settings", "write"):
        frappe.throw("Not permitted")
    seed_settings()
    result = ensure_manufacturing_masters()
    for fn in (ensure_print_format, ensure_workspace):
        try:
            fn()
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), f"mallet_estimator {fn.__name__}")
            result.setdefault("errors", []).append(f"{fn.__name__}: {exc}")
    result["workspace_exists"] = bool(frappe.db.exists("Workspace", WORKSPACE_NAME))
    return result


def ensure_workspace():
    """Create/refresh the desk Workspace programmatically (disk sync of
    workspaces is unreliable across benches)."""
    if frappe.db.exists("Workspace", WORKSPACE_NAME):
        frappe.delete_doc("Workspace", WORKSPACE_NAME, ignore_permissions=True, force=True)

    ws = frappe.new_doc("Workspace")
    ws.name = WORKSPACE_NAME
    ws.label = WORKSPACE_NAME
    ws.title = WORKSPACE_NAME
    ws.public = 1
    ws.module = "Mallet Estimator"
    ws.icon = "project"
    ws.content = json.dumps([{"id": "mest_card", "type": "card", "data": {"card_name": "Estimating", "col": 4}}])
    for typ, label, dt in [
        ("Card Break", "Estimating", None),
        ("Link", "Estimate SKU", "Estimate SKU"),
        ("Link", "Estimate", "Estimate"),
        ("Link", "Estimate Settings", "Estimate Settings"),
    ]:
        row = {"type": typ, "label": label}
        if dt:
            row.update({"link_type": "DocType", "link_to": dt})
        ws.append("links", row)
    ws.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()


def seed_settings():
    # Persist the single so default rates/rent are stored (workstation costing
    # reads them). Machine masters live as ERPNext Workstations, not here.
    settings = frappe.get_single("Estimate Settings")
    settings.flags.ignore_permissions = True
    settings.save()
    frappe.db.commit()


def ensure_print_format():
    path = os.path.join(
        frappe.get_app_path("mallet_estimator"),
        "templates", "print", "mallet_client_estimate.html",
    )
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
        pf = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
    else:
        pf = frappe.new_doc("Print Format")
        pf.name = PRINT_FORMAT_NAME

    pf.doc_type = "Estimate"
    pf.module = "Mallet Estimator"
    pf.print_format_type = "Jinja"
    pf.custom_format = 1
    pf.standard = "No"
    pf.disabled = 0
    pf.html = html
    pf.flags.ignore_permissions = True
    pf.save()
    frappe.db.commit()

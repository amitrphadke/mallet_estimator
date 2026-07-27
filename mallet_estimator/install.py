import json
import os

import frappe

from mallet_estimator.estimator import DEFAULT_MACHINES

PRINT_FORMAT_NAME = "Mallet Client Estimate"
WORKSPACE_NAME = "Mallet Estimator"


def after_install():
    seed_settings()
    ensure_print_format()
    ensure_workspace()


def after_migrate():
    # Keep the shipped print format and desk workspace in sync on every deploy.
    ensure_print_format()
    ensure_workspace()


def ensure_workspace():
    """Create/refresh the desk Workspace from the shipped JSON (disk sync of
    workspaces is unreliable across benches, so we upsert it explicitly)."""
    path = os.path.join(
        frappe.get_app_path("mallet_estimator"),
        "mallet_estimator", "workspace", "mallet_estimator", "mallet_estimator.json",
    )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if frappe.db.exists("Workspace", WORKSPACE_NAME):
        frappe.delete_doc("Workspace", WORKSPACE_NAME, ignore_permissions=True, force=True)

    doc = frappe.get_doc(data)
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()


def seed_settings():
    settings = frappe.get_single("Estimate Settings")
    if not settings.machines:
        for m in DEFAULT_MACHINES:
            settings.append("machines", m)
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

    pf.doc_type = "Execution Estimate"
    pf.module = "Mallet Estimator"
    pf.print_format_type = "Jinja"
    pf.custom_format = 1
    pf.standard = "No"
    pf.disabled = 0
    pf.html = html
    pf.flags.ignore_permissions = True
    pf.save()
    frappe.db.commit()

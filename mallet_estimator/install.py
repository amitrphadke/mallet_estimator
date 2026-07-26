import os

import frappe

from mallet_estimator.estimator import DEFAULT_MACHINES

PRINT_FORMAT_NAME = "Mallet Client Estimate"


def after_install():
    seed_settings()
    ensure_print_format()


def after_migrate():
    # Keep the shipped print format in sync with the template file.
    ensure_print_format()


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

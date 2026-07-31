import frappe


def execute():
    """Add the Operation 'Std Time (min/unit)' custom field (mallet_min_per_unit)
    and seed it on the standard Operations. ERPNext auto-computes
    total_operation_time from sub-operations, so a value set there is wiped — this
    dedicated field is the single source of truth for the estimator's step time."""
    from mallet_estimator.install import ensure_manufacturing_masters, OPERATION_CUSTOM_FIELDS
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(OPERATION_CUSTOM_FIELDS, ignore_validate=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "operation_std_time: custom field")
    try:
        ensure_manufacturing_masters()  # seeds mallet_min_per_unit (+ workstations)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "operation_std_time: seed")
    frappe.db.commit()

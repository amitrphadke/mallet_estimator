import frappe

from mallet_estimator.estimator import MISC_OPERATION


def execute():
    """Process Steps now reference a native Manufacturing Operation.
      • Seed each Operation's Total Operation Time (min/unit) + Default Workstation
        (via ensure_manufacturing_masters) so the Operation master is the single
        source of truth for operation time.
      • Backfill Estimate Labor.operation from the legacy phase text (mapping the
        misc row to the sanitized Operation name). Create an Operation for any
        stray custom phase so the now-required link is always valid."""
    from mallet_estimator.install import ensure_manufacturing_masters
    try:
        ensure_manufacturing_masters()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "link_operations: seed operations")

    # Runs post-model-sync, so the 'operation' column exists; guard anyway so a
    # mis-ordering can never hard-fail the whole migrate.
    if not frappe.db.has_column("Estimate Labor", "operation"):
        frappe.db.commit()
        return

    for r in frappe.get_all("Estimate Labor", fields=["name", "phase", "operation", "is_misc"]):
        if r.operation:
            continue
        op = MISC_OPERATION if r.is_misc else (r.phase or "").strip()
        if op in ("Miscellaneous / extra", "Miscellaneous"):
            op = MISC_OPERATION
        if not op:
            continue
        if not frappe.db.exists("Operation", op):
            try:
                d = frappe.new_doc("Operation")
                d.name = op
                d.insert(ignore_permissions=True, set_name=op)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"link_operations: create Operation {op}")
                continue
        frappe.db.set_value("Estimate Labor", r.name, "operation", op, update_modified=False)
    frappe.db.commit()

import frappe

from mallet_estimator.patches import cost_model_rework

# The user's staffing facts (2026-08-02) — L1's source of truth.
DEFAULTS = {
    "carpenter_salary": ###,
    "helper_salary": ###,
    "designer_salary": ###,
    "bonus_months": 1,
    "paid_holidays_per_month": 2,
    "national_holidays_per_year": 10,
    "lunch_hours_per_day": 1,
}


def execute():
    """L1 follow-up: JSON field defaults don't apply to an existing Single, so
    cost_model_rework rebuilt the workstations at the legacy hourly fallback
    (###). Seed the salary + calendar values where unset, then rebuild the
    operating components at the salary-derived rates (~### over ~162 hrs)."""
    if not frappe.db.exists("DocType", "Estimate Settings"):
        return
    s = frappe.get_single("Estimate Settings")
    changed = False
    for field, val in DEFAULTS.items():
        if s.meta.has_field(field) and not (s.get(field) or 0):
            s.set(field, val)
            changed = True
    if changed:
        s.flags.ignore_permissions = True
        s.save()
    try:
        cost_model_rework._rebuild_ws_components()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "seed_salary_calendar rebuild")
    frappe.db.commit()

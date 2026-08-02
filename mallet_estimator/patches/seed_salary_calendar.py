import frappe

from mallet_estimator.patches import cost_model_rework

# Salaries are SENSITIVE (###) — never stored in this repo. They are keyed on
# the site's Estimate Settings (already present on mcft-stg). Only the neutral
# CALENDAR values are seeded here; the workstation rebuild then uses whatever
# salaries the site holds.
DEFAULTS = {
    "bonus_months": 1,
    "paid_holidays_per_month": 2,
    "national_holidays_per_year": 10,
    "lunch_hours_per_day": 1,
}


def execute():
    """L1 follow-up: JSON field defaults don't apply to an existing Single. Seed
    the calendar values where unset, then rebuild the operating components at the
    salary-derived rates (salaries: keyed on the site, ### in this repo)."""
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
